"""Backfill salary from stored descriptions using regex — no HTTP requests.

Run from the scraper directory:
    cd scraper && python backfill_salary_from_desc.py
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# See main.py for why: Windows console encoding can't print emoji/non-ASCII job data.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from db import get_client
from salary_extraction import extract_salary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def run():
    client = get_client()

    result = (
        client.table("jobs")
        .select("id, title, description")
        .not_.is_("description", "null")
        .is_("salary", "null")
        .execute()
    )
    jobs = result.data or []
    log.info("Jobs with description but no salary: %d", len(jobs))

    found = 0
    skipped = 0

    for job in jobs:
        salary = extract_salary(job.get("description") or "")
        if not salary:
            skipped += 1
            continue

        try:
            client.table("jobs").update({"salary": salary}).eq("id", job["id"]).execute()
            log.info("%-60s → %s", job["title"][:60], salary)
            found += 1
        except Exception as exc:
            log.error("DB update failed for %s: %s", job["id"], exc)

    log.info("=== Done: %d salaries extracted, %d no salary found ===", found, skipped)


if __name__ == "__main__":
    run()
