"""One-off backfill: populate apply_url, is_easy_apply, salary on existing jobs.

Run from the scraper directory:
    cd scraper && python backfill_apply_salary.py

Fetches the LinkedIn detail page for every job that hasn't been backfilled yet
and PATCHes only the three new columns — all existing data (status, tier, etc.)
is preserved.
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# See main.py for why: Windows console encoding can't print emoji/non-ASCII job data.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from linkedin import fetch_description
from db import get_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def run():
    client = get_client()

    # Fetch jobs not yet backfilled — missing either field means we haven't
    # successfully filled it in yet (a job can legitimately have no salary
    # forever, so this errs toward re-checking rather than skipping).
    result = (
        client.table("jobs")
        .select("id, title, company")
        .or_("apply_url.is.null,salary.is.null")
        .order("found_at", desc=True)
        .execute()
    )
    jobs = result.data or []
    log.info("Total jobs to backfill: %d", len(jobs))

    updated = 0
    failed = 0

    for i, job in enumerate(jobs, 1):
        job_id = job["id"]
        log.info("[%d/%d] %s @ %s", i, len(jobs), job["title"], job["company"])

        desc, _, apply_url, is_easy_apply, salary = fetch_description(job_id)

        # fetch_description returns the same all-empty tuple on a failed/
        # rate-limited request as it does for a real "nothing found" page, so
        # a missing description is our best signal the fetch didn't actually
        # succeed — don't let that clobber a previously-true is_easy_apply.
        patch = {}
        if desc is not None or apply_url or salary or is_easy_apply:
            patch["is_easy_apply"] = is_easy_apply
        if apply_url:
            patch["apply_url"] = apply_url
        if salary:
            patch["salary"] = salary

        if not patch:
            log.info("  → fetch failed / nothing new, skipping update")
            failed += 1
            continue

        try:
            client.table("jobs").update(patch).eq("id", job_id).execute()
            log.info(
                "  → easy_apply=%s | apply_url=%s | salary=%s",
                is_easy_apply,
                apply_url or "—",
                salary or "—",
            )
            updated += 1
        except Exception as exc:
            log.error("  DB update failed: %s", exc)
            failed += 1

    log.info("=== Backfill complete: %d updated, %d failed ===", updated, failed)


if __name__ == "__main__":
    run()
