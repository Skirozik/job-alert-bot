"""One-off backfill: fetch real descriptions for existing GitHub-sourced jobs
and re-classify the ones still awaiting a decision.

github_sources.py never fetched a description (only company/title/location/
apply_url from the tracker README tables), so every gh: job was classified
almost blind — a likely cause of inflated MAYBE rates. This re-fetches the
description from each job's ATS (see external_descriptions.py) and, for jobs
still in their default 'new' status (not yet applied/saved/dismissed by you),
re-classifies with the real description now available.

Run from the scraper directory:
    cd scraper && python backfill_github_descriptions.py
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
        .like("id", "gh:%")
        .is_("description", "null")
        .execute()
        .data
    )
    log.info("GitHub-sourced jobs missing a description: %d", len(jobs))

    fetched = 0
    no_description = 0
    reclassified = 0
    unchanged_tier = 0

    for i, job in enumerate(jobs, 1):
        log.info("[%d/%d] %s @ %s", i, len(jobs), job["title"], job["company"])

        desc = fetch_external_description(job.get("apply_url", ""))
        if not desc:
            log.info("  -> no description available (unrecognized/unfetchable ATS)")
            no_description += 1
            continue

        fetched += 1
        patch = {"description": desc}

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
