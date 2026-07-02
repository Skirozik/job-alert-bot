"""One-off: demote already-active jobs that fail the new internship-title
pre-filter (see _is_non_internship_title in main.py).

Pure regex against the stored title — no Claude call, no rate limiting.
Skips GitHub-tracker-sourced jobs (id starts with "gh:"): those trackers are
internship-only by construction and some genuine internships there omit the
word "intern" in the title (e.g. a PhD quant-internship title), so gating
them here risks demoting a real internship with a false negative that
sticks forever (SKIP jobs are never re-classified).

Only touches jobs still in status='new'; anything applied/saved/dismissed is
left alone.

Run from the scraper directory:
    cd scraper && python recheck_intern_titles.py
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from main import _is_non_internship_title
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

    jobs = (
        client.table("jobs")
        .select("id, title, company, tier")
        .in_("tier", ["APPLY", "MAYBE"])
        .eq("status", "new")
        .execute()
        .data
    )
    log.info("Active APPLY/MAYBE jobs to check against title gate: %d", len(jobs))

    demoted = 0
    skipped_github = 0
    unchanged = 0

    for job in jobs:
        if job["id"].startswith("gh:"):
            skipped_github += 1
            continue

        if _is_non_internship_title(job["title"]):
            client.table("jobs").update({
                "tier": "SKIP",
                "reason": "Pre-filtered: no internship marker in title",
                "suggested_resume": "General",
            }).eq("id", job["id"]).execute()
            log.info("DEMOTED %s -> SKIP | %s @ %s", job["tier"], job["title"], job["company"])
            demoted += 1
        else:
            unchanged += 1

    log.info("=== Done: %d demoted, %d unchanged, %d GitHub-sourced (exempt) ===",
              demoted, unchanged, skipped_github)


if __name__ == "__main__":
    run()
