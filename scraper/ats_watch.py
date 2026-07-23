"""Fast-path direct-ATS watcher.

The main scan (main.py) and the GitHub-tracker fast path (github_watch.py)
both eventually catch a new posting, but LinkedIn syndication itself has a
real, vendor-documented lag (ATS vendors' own docs cite 6-48+ hours for the
free/default sync path). This module skips that lag entirely for a curated
list of ~50 companies (see ats_config.py) by polling each one's own ATS
job-board API directly — the same public JSON endpoint that company's own
careers page calls. The moment a posting is live on the company's board,
it's fetchable here, with no LinkedIn indexing delay in between.

Unlike github_watch.py, there's no cheap "did anything change" pre-check to
do first (a company's job list is already small, single-board JSON — not a
multi-hundred-row README) — every poll just re-fetches all ~50 boards and
diffs the returned job ids against the in-memory dedup index, same as the
GitHub-tracker step in main.py's own run() does.

Uses the same run-lock as the other two scan paths (db.start_run/finish_run)
so none of the three can double-process the same freshly-added job.
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from ats_config import ATS_COMPANIES
from ats_sources import fetch_all_listings
from db import load_dedup_index, make_norm_key, start_run, finish_run, insert_job
from main import process_job, _is_senior_role, _is_new_grad_role, _is_non_internship_title

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def run():
    run_id = start_run()
    if run_id is None:
        log.warning("Another run appears to be in progress — skipping this ATS-watch pass "
                     "(the main scan or GitHub watcher will pick up the same job(s) regardless).")
        return

    new_jobs = 0
    notified = 0
    total_raw = 0
    try:
        known_ids, known_norm_keys = load_dedup_index()

        listings = fetch_all_listings(ATS_COMPANIES)
        total_raw = len(listings)

        for job in listings:
            nk = make_norm_key(job["company"], job["title"])
            if job["id"] in known_ids or nk in known_norm_keys:
                continue
            known_ids.add(job["id"])
            known_norm_keys.add(nk)
            job["norm_key"] = nk

            # Same pre-filter as the main LinkedIn scan — a full company
            # board dump includes every level/role, not just internships.
            if _is_senior_role(job["title"]):
                job["tier"] = "SKIP"
                job["reason"] = "Pre-filtered: seniority keyword in title"
                job["suggested_resume"] = "General"
                insert_job(job)
                continue

            if _is_new_grad_role(job["title"]):
                job["tier"] = "SKIP"
                job["reason"] = "Pre-filtered: new grad / full-time role, not an internship"
                job["suggested_resume"] = "General"
                insert_job(job)
                continue

            if _is_non_internship_title(job["title"]):
                job["tier"] = "SKIP"
                job["reason"] = "Pre-filtered: no internship marker in title"
                job["suggested_resume"] = "General"
                insert_job(job)
                continue

            new_jobs += 1
            if process_job(job):
                notified += 1

        log.info("=== ATS watch complete: %d companies, %d raw listings, %d new, %d notified ===",
                 len(ATS_COMPANIES), total_raw, new_jobs, notified)

    finally:
        finish_run(run_id, total_raw=total_raw, new_jobs=new_jobs, rate_limited=0, notified=notified)


if __name__ == "__main__":
    run()
