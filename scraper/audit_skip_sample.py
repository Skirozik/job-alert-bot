"""One-off: randomly sample SKIP jobs and re-run them against the current
rubric, to check whether the same under-confidence bias that misclassified
several MAYBE jobs as stretches (instead of APPLY) also caused genuinely
good fits to be wrongly SKIPped.

Full backlog is in the thousands — this checks a bounded random sample
rather than the whole thing (fast, still catches a systemic bias if one
exists; not meant to catch every individual mistake).

Only touches jobs still in status='new'; anything applied/saved/dismissed
is left alone (skips can't be dismissed via the UI, but sync_applied_from_
tracker.py or manual DB edits could set one).

Run from the scraper directory:
    cd scraper && python audit_skip_sample.py [SAMPLE_SIZE]
"""

import logging
import random
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

DEFAULT_SAMPLE_SIZE = 100


def run():
    sample_size = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SAMPLE_SIZE
    client = get_client()

    all_skips = (
        client.table("jobs")
        .select("id")
        .eq("tier", "SKIP")
        .eq("status", "new")
        .execute()
        .data
    )
    log.info("Total SKIP jobs (status='new'): %d", len(all_skips))

    sample_ids = [r["id"] for r in random.sample(all_skips, min(sample_size, len(all_skips)))]
    jobs = []
    for i in range(0, len(sample_ids), 50):
        batch_ids = sample_ids[i:i + 50]
        rows = client.table("jobs").select("*").in_("id", batch_ids).execute().data
        jobs.extend(rows)
    log.info("Sampled %d jobs to recheck", len(jobs))

    promoted = 0
    unchanged = 0
    errors = 0

    for i, job in enumerate(jobs, 1):
        try:
            result = classify(job)
            new_tier = result.get("tier", "SKIP")

            if new_tier != "SKIP":
                client.table("jobs").update({
                    "tier": new_tier,
                    "reason": result.get("reason", ""),
                    "suggested_resume": result.get("suggested_resume", "General"),
                }).eq("id", job["id"]).execute()

                log.info("[%d/%d] PROMOTED SKIP -> %s | %s @ %s | %s",
                         i, len(jobs), new_tier, job["title"], job["company"], result.get("reason", ""))
                promoted += 1
            else:
                unchanged += 1

            time.sleep(0.3)

        except Exception as exc:
            log.error("Error on job %s: %s", job.get("id"), exc)
            errors += 1

    log.info("=== Done: %d/%d sampled jobs promoted out of SKIP, %d unchanged, %d errors ===",
              promoted, len(jobs), unchanged, errors)
    if promoted:
        promotion_rate = promoted / len(jobs)
        log.info("Sample promotion rate: %.1f%% — if this holds across the full %d-job SKIP "
                  "backlog, roughly %d more jobs may be worth a full sweep.",
                  promotion_rate * 100, len(all_skips), int(promotion_rate * len(all_skips)))


if __name__ == "__main__":
    run()
