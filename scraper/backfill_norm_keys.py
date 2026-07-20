"""One-off: recompute norm_key for every stored job using the CURRENT
norm_company()/norm_role() logic and correct any that drifted stale.

Why this exists: norm_key is computed once at insert time and never
revisited. When the normalization logic itself changes (e.g. commit
eaf58de, which reworked norm_role() for better cross-source dedup), every
row inserted before that change keeps its old, now-inconsistent norm_key
forever — so a since-changed job that SHOULD dedup-match an old row (exact
company+title repost, different id) silently doesn't, and a fresh
duplicate slips into the active queue instead of being caught.

Only ever touches the norm_key column — never tier/status/reason — so this
is safe to run at any time and cannot revert anything you've marked
applied/saved/dismissed or reclassify a job.

Run from the scraper directory:
    cd scraper && python backfill_norm_keys.py           # dry run (default)
    cd scraper && python backfill_norm_keys.py --apply   # write the fixes
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from db import get_client, make_norm_key

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def load_all_jobs(client) -> list[dict]:
    all_rows = []
    page_size = 1000
    offset = 0
    while True:
        result = (
            client.table("jobs")
            .select("id,title,company,norm_key")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        all_rows.extend(result.data)
        if len(result.data) < page_size:
            break
        offset += page_size
    return all_rows


def run(apply: bool):
    client = get_client()
    jobs = load_all_jobs(client)
    log.info("Loaded %d jobs", len(jobs))

    stale = []
    for job in jobs:
        current = make_norm_key(job["company"], job["title"])
        if current != job["norm_key"]:
            stale.append((job, current))

    log.info("%d/%d jobs have a stale norm_key", len(stale), len(jobs))

    if not apply:
        log.info("Dry run — showing first 20 examples, nothing written:")
        for job, new_key in stale[:20]:
            log.info("  %s | %r -> %r", job["id"], job["norm_key"], new_key)
        log.info("Re-run with --apply to write these %d corrections.", len(stale))
        return

    updated = 0
    errors = 0
    for job, new_key in stale:
        try:
            client.table("jobs").update({"norm_key": new_key}).eq("id", job["id"]).execute()
            updated += 1
        except Exception as exc:
            log.error("Failed to update norm_key for %s: %s", job["id"], exc)
            errors += 1

    log.info("=== Done: %d updated, %d errors ===", updated, errors)


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
