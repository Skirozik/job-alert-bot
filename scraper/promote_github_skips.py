"""One-off: promote already-stored GitHub-tracker-sourced jobs out of SKIP.

GitHub tracker sources (SimplifyJobs/speedyapply) are curated, internship-
only lists the user trusts completely and wants surfaced for a human
decision rather than auto-skipped — see _never_skip_github_sourced in
classifier.py, which enforces this going forward. This retroactively
applies the same policy to jobs already classified before that guard
existed.

Pure tier update — no reclassification, reason text is left as-is (it's
still useful context for why the bot flagged a concern, even though the
job is no longer hidden).

Only touches jobs still in status='new'; anything applied/saved/dismissed
is left alone.

Run from the scraper directory:
    cd scraper && python promote_github_skips.py
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

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
        .select("id, title, company")
        .like("id", "gh:%")
        .eq("tier", "SKIP")
        .eq("status", "new")
        .execute()
        .data
    )
    log.info("GitHub-sourced SKIP jobs to promote: %d", len(jobs))

    promoted = 0
    for job in jobs:
        client.table("jobs").update({"tier": "MAYBE"}).eq("id", job["id"]).execute()
        log.info("PROMOTED SKIP -> MAYBE | %s @ %s", job["title"], job["company"])
        promoted += 1

    log.info("=== Done: %d promoted ===", promoted)


if __name__ == "__main__":
    run()
