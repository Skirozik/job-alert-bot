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

Apple goes first on every pass: its internships page is one request, and a new
posting there is classified and pushed (apple_watch.alert) before the ~1.5-minute
board sweep starts rather than after it.

Uses an ATS-specific run lock (db.start_run/finish_run). LinkedIn and GitHub
have their own locks, so a long ATS sweep cannot cancel a punctual source pass.
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import apple_watch
from ats_config import ATS_COMPANIES
from ats_sources import fetch_all_listings
import career_sitemaps
from db import (QuotaExceeded, find_unknown_candidates, job_norm_key, make_norm_key, start_run,
                finish_run, insert_job)
from main import process_job, _is_senior_role, _is_new_grad_role, _is_non_internship_title

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def _sweep(listings: list[dict], seen_ids: set[str], seen_norm_keys: set[str]) -> tuple[int, int]:
    """Dedup, pre-filter, classify and push one batch of listings. Returns (new, notified).
    seen_ids/seen_norm_keys carry across the batches of one pass, so a posting listed by two
    sources in the same pass (the Apple students page and Apple's sitemap) is handled once."""
    if not listings:
        return 0, 0

    # norm_key has to exist before the dedup call, because the answer is
    # computed server-side from both keys.
    for job in listings:
        job["norm_key"] = job_norm_key(job)

    # One bounded question instead of downloading the answer. The previous
    # load_dedup_index() pulled the entire jobs(id, norm_key) table -- 73
    # paginated requests, ~73,000 rows, ~5 MB -- on EVERY pass. At this
    # watcher's 5-minute cadence that was ~1.4 GB/day against a 5 GB quota,
    # i.e. roughly 8x the whole monthly allowance from this one line, and
    # it is what put the project over. Sending the candidates up is ingress
    # and is not billed; only the genuinely-new ids come back.
    unknown = find_unknown_candidates(listings)

    # Local sets for WITHIN-THIS-SWEEP duplicates only. This is a different
    # question from "is it stored", and the old code conflated the two by
    # mutating the downloaded index: a full board dump can list the same
    # norm_key twice (two postings of one role) with neither stored yet, and
    # without this both would be processed.
    new_jobs = notified = 0
    for job in listings:
        if job["id"] not in unknown:
            continue
        nk = job["norm_key"]
        if job["id"] in seen_ids or nk in seen_norm_keys:
            continue
        seen_ids.add(job["id"])
        seen_norm_keys.add(nk)

        # Same pre-filter as the main LinkedIn scan — a full company
        # board dump includes every level/role, not just internships.
        if _is_senior_role(job["title"]):
            job["tier"] = "INELIGIBLE"
            job["reason"] = "Pre-filtered: seniority keyword in title"
            job["suggested_resume"] = "General"
            insert_job(job)
            continue

        if _is_new_grad_role(job["title"]):
            job["tier"] = "INELIGIBLE"
            job["reason"] = "Pre-filtered: new grad / full-time role, not an internship"
            job["suggested_resume"] = "General"
            insert_job(job)
            continue

        if _is_non_internship_title(job["title"]):
            job["tier"] = "INELIGIBLE"
            job["reason"] = "Pre-filtered: no internship marker in title"
            job["suggested_resume"] = "General"
            insert_job(job)
            continue

        new_jobs += 1
        if process_job(job):
            notified += 1
    return new_jobs, notified


def run():
    run_id = start_run("ats")
    if run_id is None:
        log.warning("Another ATS pass appears to be in progress — skipping this one.")
        return

    new_jobs = 0
    notified = 0
    total_raw = 0
    seen_ids: set[str] = set()
    seen_norm_keys: set[str] = set()
    try:
        # Apple's student postings first, every pass: page 1 of its U.S.
        # internships page is one request, and a new posting is pushed before
        # the board sweep below rather than ~1.5 minutes after it. None of this
        # may cost the board sweep; a spent quota still stops the run.
        # Three separate guards: a failure classifying a new posting must not
        # hold back its 🍎 push, which then goes out without a verdict.
        apple = None
        try:
            apple = career_sitemaps.collect_apple_students(full=apple_watch.needs_full_read())
            apple_watch.record_students_read(apple.health)
        except QuotaExceeded:
            raise
        except Exception as exc:
            log.error("Apple students page skipped this pass: %s", exc)
            # A reader that crashes is a reader that cannot read: it counts toward the alarm.
            try:
                apple_watch.record_students_read(career_sitemaps.AppleRead(
                    outcome="unreadable", problem=f"reader error ({type(exc).__name__})"))
            except QuotaExceeded:
                raise
            except Exception as exc2:
                log.error("Apple page health not recorded: %s", exc2)
        if apple and apple.rows:
            try:
                n, k = _sweep(apple.rows, seen_ids, seen_norm_keys)
                new_jobs, notified, total_raw = new_jobs + n, notified + k, total_raw + len(apple.rows)
            except QuotaExceeded:
                raise
            except Exception as exc:
                log.error("Apple students: processing the new postings failed (%s); pushing them anyway", exc)
        if apple and apple.read:
            try:
                apple_watch.alert(apple.read.postings, apple.rows, on_students_page=True,
                                  complete=apple.read.complete)
            except QuotaExceeded:
                raise
            except Exception as exc:
                log.error("Apple alerts skipped this pass: %s", exc)

        listings = fetch_all_listings(ATS_COMPANIES)
        # Employers that only post on their own careers sites (Meta, Apple,
        # D. E. Shaw), read from their crawler sitemaps about once an hour.
        # A sitemap problem must never cost the board sweep; a spent quota
        # still stops the run.
        sitemap_rows: list[dict] = []
        apple_sitemap: dict = {}
        try:
            sitemap_rows = career_sitemaps.collect_listings(apple_sitemap=apple_sitemap)
        except QuotaExceeded:
            raise
        except Exception as exc:
            log.error("Career sitemaps skipped this pass: %s", exc)
        listings += sitemap_rows
        total_raw += len(listings)

        # A crash here still fails the run, as before -- but after the Apple
        # checks below, which must not wait for whatever broke the boards.
        sweep_error = None
        try:
            n, k = _sweep(listings, seen_ids, seen_norm_keys)
            new_jobs, notified = new_jobs + n, notified + k
        except QuotaExceeded:
            raise
        except Exception as exc:
            sweep_error = exc

        # Apple internship titles in the sitemap, which can carry ones the
        # internships page does not (every one, stored or not: alert() diffs
        # them against its own ledger), then the hourly proof that the Apple
        # reader still sees Apple's new postings. Internship titles only: the
        # sitemap adds 15-40 full-time Apple roles a day, and every requisition
        # handed to alert() is kept in its ledger, which is read on every pass.
        try:
            if "internships" in apple_sitemap:
                apple_watch.alert(apple_sitemap["internships"], sitemap_rows, on_students_page=False)
            apple_watch.shadow_check()
        except QuotaExceeded:
            raise
        except Exception as exc:
            log.error("Apple checks skipped this pass: %s", exc)
        if sweep_error:
            raise sweep_error

        log.info("=== ATS watch complete: %d companies, %d raw listings, %d new, %d notified ===",
                 len(ATS_COMPANIES), total_raw, new_jobs, notified)

    finally:
        finish_run(run_id, total_raw=total_raw, new_jobs=new_jobs, rate_limited=0, notified=notified)


if __name__ == "__main__":
    run()
