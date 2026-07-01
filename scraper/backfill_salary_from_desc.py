"""Backfill salary from stored descriptions using regex — no HTTP requests.

Run from the scraper directory:
    cd scraper && python backfill_salary_from_desc.py
"""

import re
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from db import get_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# Salary patterns — ordered most-specific first
_PATTERNS = [
    # Range with $ and /hr or /hour: $20 - $30/hr, $20.50-$35/hour
    r'\$[\d,]+(?:\.\d+)?\s*[-–—to]+\s*\$[\d,]+(?:\.\d+)?\s*/\s*h(?:ou)?r',
    # Range with $ and per hour: $20 - $30 per hour
    r'\$[\d,]+(?:\.\d+)?\s*[-–—to]+\s*\$[\d,]+(?:\.\d+)?\s*per\s*hour',
    # Range with $ and /yr or annually: $80,000 - $120,000/yr
    r'\$[\d,]+(?:\.\d+)?\s*[-–—to]+\s*\$[\d,]+(?:\.\d+)?\s*(?:/\s*yr|per\s*year|annually)',
    # Plain dollar range (likely annual): $80,000 - $120,000
    r'\$[\d,]{5,}\s*[-–—to]+\s*\$[\d,]{5,}',
    # K range: $80K - $120K, $80k–$120k
    r'\$\d+[kK]\s*[-–—to]+\s*\$\d+[kK]',
    # Single value /hr: $25/hr, $25.50 / hour
    r'\$[\d,]+(?:\.\d+)?\s*/\s*h(?:ou)?r',
    # Single value per hour
    r'\$[\d,]+(?:\.\d+)?\s*per\s*hour',
    # Single K value: $120K, $120k
    r'\$\d+[kK]',
    # Single large dollar value (annual): $120,000
    r'\$[\d,]{5,}',
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]


def extract_salary(text: str) -> str | None:
    if not text:
        return None
    for pattern in _COMPILED:
        m = pattern.search(text)
        if m:
            return m.group(0).strip()
    return None


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
