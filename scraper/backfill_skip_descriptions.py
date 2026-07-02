"""One-off: fetch missing descriptions for SKIP-tier jobs and re-classify.

Mirrors backfill_maybe_apply_descriptions.py but scoped to SKIP instead of
APPLY/MAYBE — a 100-job random sample found several SKIP jobs that were
classified blind (no description ever fetched, likely lost to rate
limiting during the original scrape) and hedged into a cautious SKIP on
title alone. LinkedIn-sourced only: gh: jobs can no longer be SKIP tier at
all (see _never_skip_github_sourced in classifier.py) — any that still are
predate that fix and were already retroactively promoted.

Only reclassifies jobs still in status='new'.

Run from the scraper directory:
    cd scraper && python backfill_skip_descriptions.py
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from classifier import classify
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

    jobs = (
        client.table("jobs")
        .select("id, title, company, location, apply_url, status, tier")
        .eq("tier", "SKIP")
        .eq("status", "new")
        .is_("description", "null")
        .execute()
        .data
    )
    jobs = [j for j in jobs if not j["id"].startswith("gh:")]
    log.info("LinkedIn SKIP jobs missing a description: %d", len(jobs))

    fetched = 0
    no_description = 0
    promoted = 0
    unchanged = 0

    for i, job in enumerate(jobs, 1):
        log.info("[%d/%d] %s @ %s", i, len(jobs), job["title"], job["company"])

        desc, _, apply_url, _, _ = fetch_description(job["id"])
        if not desc:
            no_description += 1
            continue

        fetched += 1
        patch = {"description": desc}
        if apply_url:
            patch["apply_url"] = apply_url

        job["description"] = desc
        result = classify(job)
        new_tier = result.get("tier", job["tier"])
        patch["tier"] = new_tier
        patch["reason"] = result.get("reason", "")
        patch["suggested_resume"] = result.get("suggested_resume", "General")
        if result.get("salary"):
            patch["salary"] = result["salary"]

        if new_tier != "SKIP":
            log.info("  -> PROMOTED SKIP -> %s | %s", new_tier, result.get("reason", ""))
            promoted += 1
        else:
            unchanged += 1

        try:
            client.table("jobs").update(patch).eq("id", job["id"]).execute()
        except Exception as exc:
            log.error("  DB update failed: %s", exc)

    log.info(
        "=== Done: %d descriptions fetched (%d promoted, %d stayed SKIP), %d not found ===",
        fetched, promoted, unchanged, no_description,
    )


if __name__ == "__main__":
    run()
