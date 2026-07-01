"""One-off script: re-classify all SKIP jobs using the updated candidate profile.

Uses descriptions already stored in Supabase — no new LinkedIn requests.
Preserves job status (applied/saved/dismissed). Only updates tier/reason/suggested_resume.
"""

import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

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

    skips = client.table("jobs").select("*").eq("tier", "SKIP").execute().data
    log.info("Found %d SKIP jobs to re-classify", len(skips))

    promoted = 0
    unchanged = 0
    errors = 0

    for job in skips:
        try:
            result = classify(job)
            new_tier = result.get("tier", "SKIP")

            if new_tier != "SKIP":
                client.table("jobs").update({
                    "tier": new_tier,
                    "reason": result.get("reason", ""),
                    "suggested_resume": result.get("suggested_resume", "General"),
                }).eq("id", job["id"]).execute()

                log.info("PROMOTED %s → %s | %s @ %s | %s",
                         new_tier, job["title"], job["company"], job["id"],
                         result.get("reason", ""))
                promoted += 1
            else:
                unchanged += 1

            time.sleep(0.3)  # stay well under Haiku rate limits

        except Exception as exc:
            log.error("Error on job %s: %s", job.get("id"), exc)
            errors += 1

    log.info("=== Done: %d promoted, %d still SKIP, %d errors ===",
             promoted, unchanged, errors)


if __name__ == "__main__":
    run()
