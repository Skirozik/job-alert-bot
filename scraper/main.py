"""LinkedIn internship job alert scraper — main entry point.

Run:  cd LinkedIn_Job_Bot/scraper && python main.py
Env:  set variables in ../.env (local) or GitHub repo secrets (CI).

Flow per run:
  1. Search LinkedIn (5 terms × 2 locations)
  2. Canary check — 0 raw results across all searches = something is broken
  3. Dedup against Supabase
  4. Fetch description for each new job (separate detail request)
  5. Classify with Claude Haiku against Candidate_Profile_and_Filters.md
  6. ntfy.sh push for APPLY and MAYBE
  7. Store all results in Supabase (including SKIP — so they're never re-classified)
"""

import logging
import sys
import time
import random
from pathlib import Path

# Load .env from repo root for local development
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from config import SEARCH_TERMS, LOCATIONS, LOOKBACK_SECONDS
from linkedin import fetch_listings, fetch_description
from classifier import classify
from notifier import push_job, push_canary
from db import is_duplicate, insert_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# Title-level pre-filter: skip before hitting Claude if the title is clearly
# a senior/non-intern role. Saves description fetches and API calls.
_SENIOR_SIGNALS = frozenset([
    "senior", "sr.", " sr ", "staff ", "lead ", " lead,", "principal",
    "director", "manager", "head of", "vp ", "vice president",
    "architect", "fellow", "distinguished", "executive",
])

def _is_senior_role(title: str) -> bool:
    t = " " + title.lower() + " "
    return any(signal in t for signal in _SENIOR_SIGNALS)


def run():
    log.info("=== Job scraper starting — %d terms × %d locations ===",
             len(SEARCH_TERMS), len(LOCATIONS))

    # ── 1. Fetch raw listings ───────────────────────────────────────────────
    all_raw: list[dict] = []
    rate_limited_count = 0

    for term in SEARCH_TERMS:
        for location in LOCATIONS:
            log.info("Searching: '%s' in %s", term, location)
            jobs, err = fetch_listings(term, location, LOOKBACK_SECONDS)

            if err == "rate_limited":
                rate_limited_count += 1
                log.warning("  Rate limited — skipping")
            elif err:
                log.error("  Error: %s", err)
            else:
                log.info("  Got %d listings", len(jobs))
                for j in jobs:
                    j["search_term"] = term
                all_raw.extend(jobs)

            time.sleep(random.uniform(2.0, 3.5))

    log.info("Total raw listings: %d (rate limited: %d/%d searches)",
             len(all_raw), rate_limited_count, len(SEARCH_TERMS) * len(LOCATIONS))

    # ── 2. Canary: 0 results across ALL searches = likely blocked ──────────
    if not all_raw:
        msg = (
            "Scraper returned 0 results across all searches.\n"
            f"Rate limited: {rate_limited_count}/{len(SEARCH_TERMS) * len(LOCATIONS)} searches.\n"
            "Check if LinkedIn has blocked the runner IP or changed its API."
        )
        log.warning("CANARY: %s", msg)
        push_canary(msg)
        sys.exit(0)

    # ── 3. Dedup ────────────────────────────────────────────────────────────
    seen_in_run: set[str] = set()
    new_jobs: list[dict] = []

    for job in all_raw:
        if job["id"] in seen_in_run:
            continue  # deduplicate within this run before hitting Supabase
        seen_in_run.add(job["id"])

        if not is_duplicate(job["id"], job["company"], job["title"]):
            new_jobs.append(job)

    log.info("New jobs after dedup: %d / %d", len(new_jobs), len(seen_in_run))

    if not new_jobs:
        log.info("No new jobs this run — done.")
        return

    # ── 4–7. Per-job: describe → classify → notify → store ─────────────────
    notified = 0

    for job in new_jobs:
        log.info("Processing: '%s' @ %s [%s]",
                 job["title"], job["company"], job["id"])

        # 4a. Pre-filter: skip senior/non-intern titles without hitting Claude
        if _is_senior_role(job["title"]):
            log.info("  Pre-filter SKIP (senior title)")
            job["tier"] = "SKIP"
            job["reason"] = "Pre-filtered: seniority keyword in title"
            job["suggested_resume"] = "General"
            insert_job(job)
            continue

        # 4b. Fetch description (adds a delay internally)
        desc = fetch_description(job["id"])
        if desc:
            job["description"] = desc
            log.info("  Description: %d chars", len(desc))
        else:
            log.info("  No description — classifying on title/company/location")

        # 5c. Classify
        result = classify(job)
        job["tier"] = result.get("tier", "MAYBE")
        job["reason"] = result.get("reason", "")
        job["suggested_resume"] = result.get("suggested_resume", "General")
        log.info("  → %s | %s | Resume: %s",
                 job["tier"], job["reason"], job["suggested_resume"])

        # 6. Push notification for APPLY and MAYBE
        if job["tier"] in ("APPLY", "MAYBE"):
            push_job(job)
            notified += 1

        # 7. Store in Supabase (including SKIP — prevents re-classification)
        insert_job(job)

    log.info("=== Run complete: %d new jobs, %d notified ===",
             len(new_jobs), notified)


if __name__ == "__main__":
    run()
