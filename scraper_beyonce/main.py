"""LinkedIn hourly/admin job alert scraper — main entry point (Beyonce persona).

Fork of scraper/main.py, adapted for a non-competitive, Atlanta-only,
hourly-admin job search rather than a competitive SWE-internship search.
Two structural differences from the original:

  1. No GitHub-tracker sources — those are exclusively CS/SWE-internship
     lists with no equivalent for admin/healthcare/hospitality roles, so
     this pipeline is LinkedIn-only.
  2. The title pre-filter is INVERTED: the original SKIPs anything that
     doesn't look like an internship; this persona wants hourly/full-time
     work, so it SKIPs anything that DOES look like an internship, co-op,
     or new-grad/student program.

Run:  cd LinkedIn_Job_Bot/scraper_beyonce && python main.py
Env:  set variables in ../.env.beyonce (local) or the job-alert-secrets-beyonce
      Modal secret (deployed).

Flow per run:
  0. Acquire a run-lock in Supabase (guards against overlapping runs)
  1. Search LinkedIn (11 terms x 1 location: Atlanta, GA), paginating up to
     MAX_PAGES per search. Dedup runs against an in-memory index loaded once
     at the start of the run.
  2. Canary check — 0 raw LinkedIn results across all searches = something is broken
  3. Fetch description for each new job (separate detail request)
  4. Classify with Claude Haiku against Beyonce_Candidate_Profile_and_Filters.md
  5. Store all results in Supabase (including SKIP — so they're never re-classified)
  6. ntfy.sh push for APPLY and MAYBE, only once storage succeeded
"""

import logging
import re
import sys
import time
import random
from pathlib import Path

# Load .env.beyonce from repo root for local development
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env.beyonce")

# Job titles/reasons can contain emoji or other non-ASCII characters. On
# Windows, stdout defaults to the system code page (e.g. cp1252) rather than
# UTF-8, which makes logging raise (and silently swallow) an encoding error
# on every such line. GitHub Actions/Linux runners already default to UTF-8,
# so this is a no-op there.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config import SEARCH_TERMS, LOCATIONS, LOOKBACK_SECONDS
from linkedin import fetch_listings, fetch_description
from classifier import classify
from notifier import push_job, push_canary
from db import load_dedup_index, make_norm_key, insert_job, start_run, finish_run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# Title-level pre-filter: skip before hitting Claude if the title is
# unambiguously an executive/leadership role — not an entry-level admin
# position. Saves a description fetch and an API call.
#
# This list is deliberately NARROWER than the original SWE-internship
# pipeline's, which it was forked from. Words that reliably mean "too senior"
# in a software-engineering title appear in ordinary entry-level ADMIN titles,
# and blanket-matching them here silently dropped real target roles before
# Claude ever saw them (they were logged as a legitimate-looking
# "Pre-filtered: seniority/management keyword in title", so the miss was
# invisible on the dashboard). Confirmed by probing the filter directly:
#   "executive" -> blocked "Executive Assistant" (a target role — an
#                  Executive Assistant is not an executive)
#   "staff"     -> blocked "Staff Assistant" / "Administrative Staff Assistant"
#   "lead"      -> blocked "Lead Patient Access Representative"
#   "senior"    -> blocked "Senior Administrative Assistant"
#   "manager"   -> blocked "Office Manager", "Front Desk Manager"
#
# Seniority is still enforced — just by the rubric rather than by a blanket
# title match, so the decision is a visible, explained SKIP reason instead of
# a silent pre-filter. See the SKIP list in
# Beyonce_Candidate_Profile_and_Filters.md, which names Director/Manager/
# Supervisor/VP directly and treats Practice Manager / Medical Office Manager
# as credential-gapped.
#
# Matched as whole words/phrases via \b, which prevents SUBSTRING matches
# ("Leadership Development Assistant" and "Misleading..." do not match "lead")
# but does NOT protect a title that genuinely contains the word as its own
# token — "Lead Generation Coordinator" really would match "lead", which is
# part of why "lead" is no longer in this list.
_SENIOR_SIGNALS = [
    "principal", "director", "head of", "vp", "vice president",
    "architect", "fellow", "distinguished",
]

_SENIOR_RE = re.compile(r"\b(?:" + "|".join(re.escape(s) for s in _SENIOR_SIGNALS) + r")\b")


def _is_senior_role(title: str) -> bool:
    return bool(_SENIOR_RE.search(title.lower()))


# Inverted from the original's _is_non_internship_title: this persona wants
# hourly/full-time/part-time work, NOT an internship, co-op, apprenticeship,
# new-grad program, or student-worker position — so SKIP when one of those
# markers IS present, not when it's absent.
_INTERN_OR_STUDENT_TITLE_RE = re.compile(
    r"\bintern(?:ship)?s?\b|\bco[\s-]?ops?\b|\bapprentice(?:ship)?s?\b"
    r"|\bnew\s+grad(?:uate)?\b|\bstudent\s+(?:worker|program)\b"
)


def _is_internship_or_student_title(title: str) -> bool:
    return bool(_INTERN_OR_STUDENT_TITLE_RE.search(title.lower()))


MAX_PAGES_PER_SEARCH = 5  # 50 results max per search term/location pair —
# a single-metro admin-title search is inherently lower-volume than the
# original's nationwide "software engineer intern" search; loosen if a term
# regularly hits this cap.


def process_job(job: dict) -> bool:
    """Fetch description, classify, store, and notify for a single job that
    has already passed the title pre-filter. Returns True if a push
    notification was sent.
    """
    log.info("Processing: '%s' @ %s [%s]", job["title"], job["company"], job["id"])

    desc, logo_url, apply_url, is_easy_apply, salary_li = fetch_description(job["id"])
    if desc:
        job["description"] = desc
        log.info("  Description: %d chars", len(desc))
    else:
        log.info("  No description — classifying on title/company/location")
    if logo_url:
        job["logo_url"] = logo_url
        log.info("  Logo: %s", logo_url)
    job["is_easy_apply"] = job.get("is_easy_apply", False) or is_easy_apply
    if apply_url:
        job["apply_url"] = apply_url
        log.info("  Apply URL: %s", apply_url)
    if salary_li:
        job["salary"] = salary_li
        log.info("  Salary (LinkedIn): %s", salary_li)

    # Classify
    result = classify(job)

    # A transient API failure must NOT be persisted. Storing a fallback verdict
    # puts the job in the dedup index, so it is never reconsidered — that is
    # exactly how the 2026-08-04 outage buried real APPLY jobs as junk MAYBEs.
    # Returning early leaves the row absent, and the next run rediscovers it.
    if result.get("failed"):
        log.warning("  Classification FAILED — not storing '%s' @ %s; the next run will retry it",
                    job.get("title"), job.get("company"))
        return False
    job["tier"] = result.get("tier", "APPLY_CAVEAT")
    job["reason"] = result.get("reason", "")
    if not job.get("salary") and result.get("salary"):
        job["salary"] = result["salary"]
        log.info("  Salary (Claude): %s", job["salary"])
    # Reason deliberately not logged — public repo, world-readable Actions
    # logs, and reasons can quote personal details. Look it up by id instead.
    log.info("  -> %s | id=%s", job["tier"], job.get("id"))

    # Store in Supabase first (including SKIP — prevents re-classification)
    stored = insert_job(job)

    # Push notification for APPLY and MAYBE — only if it was actually
    # persisted, so a DB hiccup doesn't cause the same job to be
    # re-classified and re-notified every run until the write succeeds.
    if job["tier"] in ("APPLY", "APPLY_CAVEAT") and stored:
        push_job(job)
        return True
    return False


def run():
    log.info("=== Job scraper starting — %d terms x %d locations ===",
             len(SEARCH_TERMS), len(LOCATIONS))

    # -- 0. Run-lock: skip if another run looks still in progress --
    run_id = start_run()
    if run_id is None:
        log.warning("Another run appears to be in progress (started <20 min ago, "
                     "unfinished) — skipping to avoid double-processing.")
        return

    new_jobs: list[dict] = []
    seen_in_run: set[str] = set()
    total_raw = 0
    rate_limited_count = 0
    notified = 0

    try:
        # -- 1. Fetch + dedup, paginating until an all-duplicate page --
        known_ids, known_norm_keys = load_dedup_index()

        for term in SEARCH_TERMS:
            for location in LOCATIONS:
                log.info("Searching: '%s' in %s", term, location)

                for page in range(MAX_PAGES_PER_SEARCH):
                    start = page * 10
                    jobs, err = fetch_listings(term, location, LOOKBACK_SECONDS, start=start)

                    if err == "rate_limited":
                        rate_limited_count += 1
                        log.warning("  p%d: rate limited — stopping pagination", page)
                        break
                    if err:
                        log.error("  p%d: error — %s", page, err)
                        break
                    if not jobs:
                        # Same flaky-endpoint retry as the original pipeline —
                        # a single empty page isn't reliable evidence
                        # pagination is done.
                        time.sleep(random.uniform(1.5, 2.5))
                        jobs, err = fetch_listings(term, location, LOOKBACK_SECONDS, start=start)
                        if err or not jobs:
                            log.info("  p%d: 0 listings on retry too — done", page)
                            break
                        log.info("  p%d: 0 listings on first try, %d on retry — continuing", page, len(jobs))

                    total_raw += len(jobs)
                    new_on_page = 0
                    all_db_duplicate = True

                    for j in jobs:
                        nk = make_norm_key(j["company"], j["title"])
                        is_db_dup = j["id"] in known_ids or nk in known_norm_keys
                        if not is_db_dup:
                            all_db_duplicate = False

                        if j["id"] in seen_in_run:
                            continue
                        seen_in_run.add(j["id"])

                        if not is_db_dup:
                            j["search_term"] = term
                            j["norm_key"] = nk
                            new_jobs.append(j)
                            new_on_page += 1
                            known_ids.add(j["id"])
                            known_norm_keys.add(nk)

                    log.info("  p%d (start=%d): %d listings, %d new", page, start, len(jobs), new_on_page)

                    if all_db_duplicate:
                        log.info("  All duplicates in DB — stopping pagination")
                        break

                    if len(jobs) < 10:
                        break  # partial page = last page

                    time.sleep(random.uniform(2.0, 3.5))

                time.sleep(random.uniform(2.0, 3.5))

        log.info("Total raw: %d | New: %d | Rate limited: %d/%d searches",
                 total_raw, len(new_jobs), rate_limited_count, len(SEARCH_TERMS) * len(LOCATIONS))

        # -- 2. Canary: 0 raw LinkedIn results across ALL searches = likely blocked --
        if total_raw == 0:
            msg = (
                "Scraper returned 0 results across all searches.\n"
                f"Rate limited: {rate_limited_count}/{len(SEARCH_TERMS) * len(LOCATIONS)} searches.\n"
                "Check if LinkedIn has blocked the runner IP or changed its API."
            )
            log.warning("CANARY: %s", msg)
            push_canary(msg)
            return

        if not new_jobs:
            log.info("No new jobs this run — done.")
            return

        # -- 3-6. Per-job: describe -> classify -> notify -> store --
        for job in new_jobs:
            log.info("Processing: '%s' @ %s [%s]",
                     job["title"], job["company"], job["id"])

            # 3a. Pre-filter: skip senior/management and internship/student
            # titles without hitting Claude.
            if _is_senior_role(job["title"]):
                log.info("  Pre-filter SKIP (executive/leadership title)")
                job["tier"] = "INELIGIBLE"
                job["reason"] = "Pre-filtered: executive/leadership keyword in title"
                insert_job(job)
                continue

            if _is_internship_or_student_title(job["title"]):
                log.info("  Pre-filter SKIP (internship/student marker in title)")
                job["tier"] = "INELIGIBLE"
                job["reason"] = "Pre-filtered: internship/co-op/student-program marker in title"
                insert_job(job)
                continue

            if process_job(job):
                notified += 1

        log.info("=== Run complete: %d new jobs, %d notified ===",
                 len(new_jobs), notified)

    finally:
        finish_run(
            run_id,
            total_raw=total_raw,
            new_jobs=len(new_jobs),
            notified=notified,
            rate_limited=rate_limited_count,
        )


if __name__ == "__main__":
    run()
