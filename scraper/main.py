"""LinkedIn internship job alert scraper — main entry point.

Run:  cd LinkedIn_Job_Bot/scraper && python main.py
Env:  set variables in ../.env (local) or GitHub repo secrets (CI).

Flow per run:
  0. Acquire a run-lock in Supabase (guards against two schedulers overlapping)
  1. Search LinkedIn (5 terms × 2 locations), paginating up to MAX_PAGES per search,
     plus a supplementary fetch from tracked GitHub internship-list repos.
     Dedup runs against an in-memory index loaded once at the start of the run
     (LinkedIn returns newest-first, so once a full page is all true DB
     duplicates, nothing deeper can be new either).
  2. Canary check — 0 raw LinkedIn results across all searches = something is broken
  3. Fetch description for each new job (separate detail request)
  4. Classify with Claude Haiku against Candidate_Profile_and_Filters.md
  5. Store all results in Supabase (including SKIP — so they're never re-classified)
  6. ntfy.sh push for APPLY and MAYBE, only once storage succeeded
"""

import logging
import re
import sys
import time
import random
from pathlib import Path

# Load .env from repo root for local development
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# Job titles/reasons can contain emoji or other non-ASCII characters. On
# Windows, stdout defaults to the system code page (e.g. cp1252) rather than
# UTF-8, which makes logging raise (and silently swallow) an encoding error
# on every such line. GitHub Actions/Linux runners already default to UTF-8,
# so this is a no-op there.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config import SEARCH_TERMS, LOCATIONS, LOOKBACK_SECONDS
from linkedin import fetch_listings, fetch_description
from github_sources import fetch_github_listings
from external_descriptions import fetch_external_description
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
# a senior/non-intern role. Saves description fetches and API calls.
# Matched as whole words/phrases (via \b) so e.g. "architect" doesn't match
# "Solutions Architecture Intern" and "fellow" doesn't match "Fellowship".
_SENIOR_SIGNALS = [
    "senior", "sr", "staff", "lead", "principal",
    "director", "manager", "head of", "vp", "vice president",
    "architect", "fellow", "distinguished", "executive",
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
    return bool(_NEW_GRAD_RE.search(title.lower()))


MAX_PAGES_PER_SEARCH = 5  # 50 results max per search term/location pair


def run():
    log.info("=== Job scraper starting — %d terms × %d locations ===",
             len(SEARCH_TERMS), len(LOCATIONS))

    # ── 0. Run-lock: skip if another scheduler's run looks still in progress ──
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
        # ── 1. Fetch + dedup, paginating until an all-duplicate page ────────
        # Dedup index is loaded once (one bulk query) instead of 2 Supabase
        # calls per listing. LinkedIn returns newest-first, so once a full
        # page is all *true DB duplicates*, everything deeper is older and
        # already stored — stop paginating. (Distinct from "already queued
        # this run under another search term", which used to be conflated
        # with a DB duplicate and could cut pagination short too early.)
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
                        log.info("  p%d: 0 listings — done", page)
                        break

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

        # ── 2. Canary: 0 raw LinkedIn results across ALL searches = likely blocked ──
        if total_raw == 0:
            msg = (
                "Scraper returned 0 results across all searches.\n"
                f"Rate limited: {rate_limited_count}/{len(SEARCH_TERMS) * len(LOCATIONS)} searches.\n"
                "Check if LinkedIn has blocked the runner IP or changed its API."
            )
            log.warning("CANARY: %s", msg)
            push_canary(msg)
            return

        # ── 1b. Supplementary GitHub-tracker sources (no rate limiting) ──────
        # Kept out of the canary/total_raw check above — it exists to detect
        # LinkedIn-specific blocking, and mixing in another source would mask it.
        for j in fetch_github_listings():
            if j["id"] in seen_in_run:
                continue
            seen_in_run.add(j["id"])
            nk = make_norm_key(j["company"], j["title"])
            if j["id"] in known_ids or nk in known_norm_keys:
                continue
            known_ids.add(j["id"])
            known_norm_keys.add(nk)
            j["norm_key"] = nk
            new_jobs.append(j)

        if not new_jobs:
            log.info("No new jobs this run — done.")
            return

        # ── 3–6. Per-job: describe → classify → notify → store ──────────────
        for job in new_jobs:
            log.info("Processing: '%s' @ %s [%s]",
                     job["title"], job["company"], job["id"])

            # 3a. Pre-filter: skip senior/non-intern titles without hitting Claude
            if _is_senior_role(job["title"]):
                log.info("  Pre-filter SKIP (senior title)")
                job["tier"] = "SKIP"
                job["reason"] = "Pre-filtered: seniority keyword in title"
                job["suggested_resume"] = "General"
                insert_job(job)
                continue

            if _is_new_grad_role(job["title"]):
                log.info("  Pre-filter SKIP (new grad / full-time role)")
                job["tier"] = "SKIP"
                job["reason"] = "Pre-filtered: new grad / full-time role, not an internship"
                job["suggested_resume"] = "General"
                insert_job(job)
                continue

            # 3b. Fetch description + logo + apply info. LinkedIn jobs get a
            # full detail-page fetch; GitHub-sourced jobs already carry their
            # own apply_url/location and get a description from the ATS API.
            if not job["id"].startswith("gh:"):
                desc, logo_url, apply_url, is_easy_apply, salary_li = fetch_description(job["id"])
                if desc:
                    job["description"] = desc
                    log.info("  Description: %d chars", len(desc))
                else:
                    log.info("  No description — classifying on title/company/location")
                if logo_url:
                    job["logo_url"] = logo_url
                    log.info("  Logo: %s", logo_url)
                # Card-level detection (linkedin.py) can catch cases the detail
                # page misses (and vice versa on rate limit) — keep True from either.
                job["is_easy_apply"] = job.get("is_easy_apply", False) or is_easy_apply
                if apply_url:
                    job["apply_url"] = apply_url
                    log.info("  Apply URL: %s", apply_url)
                if salary_li:
                    job["salary"] = salary_li
                    log.info("  Salary (LinkedIn): %s", salary_li)
            else:
                desc = fetch_external_description(job.get("apply_url", ""))
                if desc:
                    job["description"] = desc
                    log.info("  GitHub source — fetched description: %d chars", len(desc))
                else:
                    log.info("  GitHub source — no description available (unrecognized/unfetchable ATS)")

            # 4. Classify
            result = classify(job)
            job["tier"] = result.get("tier", "MAYBE")
            job["reason"] = result.get("reason", "")
            job["suggested_resume"] = result.get("suggested_resume", "General")
            if not job.get("salary") and result.get("salary"):
                job["salary"] = result["salary"]
                log.info("  Salary (Claude): %s", job["salary"])
            log.info("  → %s | %s | Resume: %s",
                     job["tier"], job["reason"], job["suggested_resume"])

            # 5. Store in Supabase first (including SKIP — prevents re-classification)
            stored = insert_job(job)

            # 6. Push notification for APPLY and MAYBE — only if it was actually
            #    persisted, so a DB hiccup doesn't cause the same job to be
            #    re-classified and re-notified every run until the write succeeds.
            if job["tier"] in ("APPLY", "MAYBE") and stored:
                push_job(job)
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
