"""One-off backfill: fetch missing descriptions for jobs currently tier
MAYBE/APPLY, and re-classify the ones still awaiting a decision.

Unlike backfill_github_descriptions.py (which only covers gh: jobs and
targets every missing description regardless of tier), this covers both
LinkedIn and GitHub-sourced jobs, scoped to MAYBE/APPLY specifically — the
active review queue, where a classification made without a description is
most likely to be wrong or unnecessarily hedged.

Run from the scraper directory:
    cd scraper && python backfill_maybe_apply_descriptions.py
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from classifier import classify
from external_descriptions import fetch_external_description
from linkedin import fetch_description as fetch_linkedin_description
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
        .in_("tier", ["MAYBE", "APPLY"])
        .is_("description", "null")
        .execute()
        .data
    )
    log.info("MAYBE/APPLY jobs missing a description: %d", len(jobs))

    fetched = 0
    no_description = 0
    reclassified = 0
    unchanged_tier = 0

    for i, job in enumerate(jobs, 1):
        log.info("[%d/%d] %s @ %s [%s]", i, len(jobs), job["title"], job["company"], job["id"])

        if job["id"].startswith("gh:"):
            desc = fetch_external_description(job.get("apply_url", ""))
        else:
            desc, _, apply_url, _, _ = fetch_linkedin_description(job["id"])
            if apply_url and not job.get("apply_url"):
                job["apply_url"] = apply_url  # picked up alongside the retry, store it too

        if not desc:
            log.info("  -> no description available")
            no_description += 1
            continue

        fetched += 1
        patch = {"description": desc}
        if job.get("apply_url"):
            patch["apply_url"] = job["apply_url"]

        if job.get("status", "new") == "new":
            job["description"] = desc
            result = classify(job)
            new_tier = result.get("tier", job.get("tier", "MAYBE"))
            patch["tier"] = new_tier
            patch["reason"] = result.get("reason", "")
            patch["suggested_resume"] = result.get("suggested_resume", "General")
            if result.get("salary"):
                patch["salary"] = result["salary"]

            if new_tier != job.get("tier"):
                log.info("  -> %d chars | %s -> %s | %s", len(desc), job.get("tier"), new_tier, result.get("reason", ""))
                reclassified += 1
            else:
                log.info("  -> %d chars | tier unchanged (%s)", len(desc), new_tier)
                unchanged_tier += 1
        else:
            log.info("  -> %d chars | status=%s, storing description only (not re-classifying)", len(desc), job["status"])

        try:
            client.table("jobs").update(patch).eq("id", job["id"]).execute()
        except Exception as exc:
            log.error("  DB update failed: %s", exc)

    log.info(
        "=== Done: %d descriptions fetched (%d re-classified, %d tier unchanged), %d not found ===",
        fetched, reclassified, unchanged_tier, no_description,
    )


if __name__ == "__main__":
    run()
