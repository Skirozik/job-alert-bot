"""LinkedIn IT/cybersecurity internship alert scraper — main entry point
(Hassan persona).

Fork of scraper/main.py. Like the original this is an INTERNSHIP search, so
the title pre-filter keeps the original's shape (require an internship
marker, reject seniority and new-grad titles) rather than the inverted one
in scraper_beyonce/main.py.

Two structural differences from the original:

  1. No GitHub-tracker or direct-ATS sources — those are curated CS/SWE
     internship lists with no IT-support/cybersecurity equivalent, so this
     pipeline is LinkedIn-only.
  2. The seniority list is narrower — see _SENIOR_SIGNALS below.

Run:  cd LinkedIn_Job_Bot/scraper_hassan && python main.py
Env:  set variables in ../.env.hassan (local) or the repo's GitHub Actions
      secrets (deployed — see .github/workflows/scrape_hassan.yml).

Flow per run:
  0. Acquire a run-lock in Supabase (guards against overlapping runs)
  1. Search LinkedIn (12 terms x 1 location: Washington, DC), paginating up
     to MAX_PAGES_PER_SEARCH per search. Dedup runs against an in-memory
     index loaded once at the start of the run.
  2. Canary check — 0 raw LinkedIn results across all searches = broken
  3. Fetch description for each new job (separate detail request)
  4. Classify with Claude Haiku against Hassan_Candidate_Profile_and_Filters.md
  5. Store all results in Supabase (including SKIP — never re-classified)
  6. ntfy.sh push for APPLY and MAYBE, only once storage succeeded
"""

import logging
import re
import sys
import time
import random
from pathlib import Path

# Load .env.hassan from repo root for local development
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env.hassan")

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

# Title-level pre-filter: skip before hitting Claude if the title is clearly
# not an entry-level internship. Saves a description fetch and an API call.
#
# NARROWER than scraper/main.py's list, which also carries "staff", "lead",
# and "executive". Those three appear inside legitimate entry-level titles
# and, when blanket-matched, drop real roles before Claude ever sees them —
# logged as a plausible-looking "Pre-filtered: seniority keyword in title",
# so the miss is invisible on the dashboard. That failure was confirmed live
# on the Beyonce fork (see scraper_beyonce/main.py), where "executive"
# blocked "Executive Assistant" and "lead" blocked "Lead Patient Access
# Representative". The positive internship gate below already excludes most
# senior titles on its own, so these three earn nothing here.
#
# Matched as whole words/phrases via \b, which stops SUBSTRING matches
# ("architect" will not match "Solutions Architecture Intern", "fellow" will
# not match "Fellowship") but does NOT protect a title that genuinely
# contains the word as its own token.
_SENIOR_SIGNALS = [
    "senior", "sr", "principal",
    "director", "manager", "head of", "vp", "vice president",
    "architect", "fellow", "distinguished",
]

_NEW_GRAD_SIGNALS = [
    "new grad",
    "new graduate",
    "new college grad",
    "college grad",
    "ncg",
    "university grad",
    "university graduate",
    "recent grad",
    "recent graduate",
]

_SENIOR_RE = re.compile(r"\b(?:" + "|".join(re.escape(s) for s in _SENIOR_SIGNALS) + r")\b")
_NEW_GRAD_RE = re.compile(r"\b(?:" + "|".join(re.escape(s) for s in _NEW_GRAD_SIGNALS) + r")\b")


def _is_senior_role(title: str) -> bool:
    return bool(_SENIOR_RE.search(title.lower()))


def _is_new_grad_role(title: str) -> bool:
    # He graduates May 2028. New-grad programs hire people finishing their
    # degree now, so they are never a fit regardless of how well the stack
    # matches.
    return bool(_NEW_GRAD_RE.search(title.lower()))


# There is deliberately NO positive internship gate here, unlike
# scraper/main.py's _is_non_internship_title.
#
# This persona wants internships AND entry-level full-time/part-time IT work.
# Requiring "intern" in the title dropped 2,515 of his first 3,136 scraped
# jobs before the classifier ever saw them — including 739 entry-level IT
# roles in the DC metro, which is exactly what he originally asked for
# ("IT Support Specialist", "Desktop Support", "Junior System Admin").
# Measured 2026-08-07: 739 discarded vs 10 surfaced.
#
# Employment type is now judged by the rubric on the full description rather
# than guessed from the title, which is the right place for it — "IT
# Specialist II" and "Help Desk Technician" are legitimate targets, and no
# title regex can tell an entry-level one from a five-years-experience one.
#
# The seniority gate above therefore carries more weight now: without the
# internship requirement, senior titles reach the classifier unless caught
# there. _EXPERIENCED_LEVEL_RE below adds the level suffixes that imply
# real experience ("Engineer II", "Analyst III") — cheap to filter on the
# title, and it keeps them from costing a Claude call each.
_EXPERIENCED_LEVEL_RE = re.compile(r"\b(?:ii|iii|iv|v)\b|\blevel\s*[2-5]\b|\bl[2-5]\b", re.I)


def _is_experienced_level(title: str) -> bool:
    return bool(_EXPERIENCED_LEVEL_RE.search(title or ""))


MAX_PAGES_PER_SEARCH = 5  # 50 results max per search term/location pair —
# a single-metro IT-internship search is inherently lower-volume than the
# original's nationwide SWE search; loosen if a term regularly hits this cap.


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
    job["is_easy_apply"] = job.get("is_easy_apply", False) or is_easy_apply
    if apply_url:
        job["apply_url"] = apply_url
    if salary_li:
        job["salary"] = salary_li
        log.info("  Salary (LinkedIn): %s", salary_li)

    # Classify
    result = classify(job)
    job["tier"] = result.get("tier", "MAYBE")
    job["reason"] = result.get("reason", "")
    if not job.get("salary") and result.get("salary"):
        job["salary"] = result["salary"]
    log.info("  -> %s | %s", job["tier"], job["reason"])

    # Store in Supabase first (including SKIP — prevents re-classification)
    stored = insert_job(job)

    # Push notification for APPLY and MAYBE — only if it was actually
    # persisted, so a DB hiccup doesn't cause the same job to be
    # re-classified and re-notified every run until the write succeeds.
    if job["tier"] in ("APPLY", "MAYBE") and stored:
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
                        # A single empty page isn't reliable evidence that
                        # pagination is done — the endpoint is flaky.
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

            # 3a. Pre-filter: reject seniority, new-grad, and non-internship
            # titles without hitting Claude.
            if _is_senior_role(job["title"]):
                log.info("  Pre-filter SKIP (seniority title)")
                job["tier"] = "SKIP"
                job["reason"] = "Pre-filtered: seniority keyword in title"
                insert_job(job)
                continue

            if _is_new_grad_role(job["title"]):
                log.info("  Pre-filter SKIP (new grad program)")
                job["tier"] = "SKIP"
                job["reason"] = "Pre-filtered: new grad program, he graduates May 2028"
                insert_job(job)
                continue

            if _is_experienced_level(job["title"]):
                log.info("  Pre-filter SKIP (experienced level suffix in title)")
                job["tier"] = "SKIP"
                job["reason"] = "Pre-filtered: level suffix implies experience (II/III/IV)"
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
