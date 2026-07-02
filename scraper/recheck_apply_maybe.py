"""One-off: re-run every current APPLY/MAYBE job through the classifier
against the current Candidate_Profile_and_Filters.md.

Use this whenever the rubric changes in a way that could reclassify already-
stored jobs (e.g. tightening a SKIP rule) — it's the mirror of
reclassify_skips.py, which re-checks SKIP jobs for promotion; this re-checks
APPLY/MAYBE jobs for demotion. Only touches jobs still in status='new';
anything you've already applied to, saved, or dismissed is left alone.

Run from the scraper directory:
    cd scraper && python recheck_apply_maybe.py
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
    log.info("APPLY/MAYBE jobs to recheck (status='new'): %d", len(jobs))

    demoted = 0
    promoted = 0
    unchanged = 0
    errors = 0

    for i, job in enumerate(jobs, 1):
        try:
            result = classify(job)
            new_tier = result.get("tier", job["tier"])

            if new_tier != job["tier"]:
                client.table("jobs").update({
                    "tier": new_tier,
                    "reason": result.get("reason", ""),
                    "suggested_resume": result.get("suggested_resume", "General"),
                }).eq("id", job["id"]).execute()

                direction = "DEMOTED" if _rank(new_tier) < _rank(job["tier"]) else "PROMOTED"
                if direction == "DEMOTED":
                    demoted += 1
                else:
                    promoted += 1
                log.info("[%d/%d] %s: %s -> %s | %s @ %s | %s",
                         i, len(jobs), direction, job["tier"], new_tier,
                         job["title"], job["company"], result.get("reason", ""))
            else:
                unchanged += 1

            time.sleep(0.3)  # stay well under Haiku rate limits

        except Exception as exc:
            log.error("Error on job %s: %s", job.get("id"), exc)
            errors += 1

    log.info("=== Done: %d demoted, %d promoted, %d unchanged, %d errors ===",
             demoted, promoted, unchanged, errors)


def _rank(tier: str) -> int:
    return {"SKIP": 0, "MAYBE": 1, "APPLY": 2}.get(tier, 1)


if __name__ == "__main__":
    run()
