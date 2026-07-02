"""One-off: re-run suggested_resume for all active APPLY/MAYBE jobs against
the current 4-resume guidance (Mobile/AI/Frontend/General).

Only updates suggested_resume — tier and reason are left untouched, since
this is meant to run independently of a tier recheck (e.g. right after
tightening the resume-matching rubric, without wanting to re-litigate tier
decisions that were already reviewed).

Run from the scraper directory:
    cd scraper && python recheck_resume.py
"""

import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from classifier import classify
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
        .select("*")
        .in_("tier", ["APPLY", "MAYBE"])
        .eq("status", "new")
        .execute()
        .data
    )
    log.info("Active APPLY/MAYBE jobs to recheck resume for: %d", len(jobs))

    changed = 0
    unchanged = 0
    errors = 0

    for i, job in enumerate(jobs, 1):
        try:
            result = classify(job)
            new_resume = result.get("suggested_resume", "General")

            if new_resume != job.get("suggested_resume"):
                client.table("jobs").update({"suggested_resume": new_resume}).eq("id", job["id"]).execute()
                log.info("[%d/%d] %s -> %s | %s @ %s",
                         i, len(jobs), job.get("suggested_resume"), new_resume, job["title"], job["company"])
                changed += 1
            else:
                unchanged += 1

            time.sleep(0.3)

        except Exception as exc:
            log.error("Error on job %s: %s", job.get("id"), exc)
            errors += 1

    log.info("=== Done: %d changed, %d unchanged, %d errors ===", changed, unchanged, errors)


if __name__ == "__main__":
    run()
