"""One-off: compute target_key for every stored job.

Why this exists: target_key is the cross-source identity the notification
ledger uses to tell that a LinkedIn row, an ats: row and a gh: row are one
application (see migrations/20260829_notification_ledger.sql and
target_key.py). It is computed at insert time going forward, but every row
already in the table has NULL — and a NULL key makes no claim, so until this
runs the sibling check is blind to the entire history and the first days
still duplicate.

Only ever touches the target_key column — never tier/status/reason — so it is
safe to run at any time and cannot revert anything marked applied/saved/
dismissed, or reclassify a job.

Egress: reads four narrow columns, ~150 bytes/row against ~50k rows, so
roughly 7 MB one-time. That is deliberate given the 5 GB quota this project
has already blown once; do not widen the select to "*".

Run from the scraper directory:
    cd scraper && python backfill_target_keys.py           # dry run (default)
    cd scraper && python backfill_target_keys.py --apply   # write the keys
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from db import get_client
from target_key import definitive_target_key

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

PAGE = 1000
BATCH = 500


def load_rows(client) -> list[dict]:
    """Narrow paginated read. .order("id") is required, not cosmetic: a
    .range() walk with no ORDER BY is not a stable enumeration and can return
    a row in two pages or in none."""
    rows: list[dict] = []
    offset = 0
    while True:
        result = (
            client.table("jobs")
            .select("id,url,apply_url,is_easy_apply,target_key")
            .order("id")
            .range(offset, offset + PAGE - 1)
            .execute()
        )
        page = result.data or []
        rows.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
        log.info("  loaded %d rows...", len(rows))
    return rows


def main() -> int:
    apply = "--apply" in sys.argv
    client = get_client()

    log.info("Loading jobs...")
    rows = load_rows(client)
    log.info("Loaded %d rows", len(rows))

    updates: list[tuple[str, str]] = []
    identified = 0
    for row in rows:
        key = definitive_target_key(row)
        if key is None:
            continue
        identified += 1
        if row.get("target_key") != key:
            updates.append((row["id"], key))

    log.info("%d of %d rows have a provable application target (%.1f%%)",
             identified, len(rows), 100.0 * identified / max(len(rows), 1))
    log.info("%d rows need their target_key written", len(updates))

    by_prefix: dict = {}
    for _, key in updates:
        prefix = key.split(":", 1)[0]
        by_prefix[prefix] = by_prefix.get(prefix, 0) + 1
    if by_prefix:
        log.info("  by platform: %s",
                 ", ".join(f"{n} {p}" for p, n in sorted(by_prefix.items(), key=lambda kv: -kv[1])))

    if not apply:
        log.info("DRY RUN — nothing written. Re-run with --apply to write.")
        for job_id, key in updates[:15]:
            log.info("  would set %s -> %s", job_id, key)
        return 0

    written = 0
    for i in range(0, len(updates), BATCH):
        for job_id, key in updates[i:i + BATCH]:
            try:
                client.table("jobs").update({"target_key": key}).eq("id", job_id).execute()
                written += 1
            except Exception as exc:
                log.error("  failed for %s: %s", job_id, exc)
        log.info("  written %d/%d", written, len(updates))

    log.info("Done: %d target_keys written", written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
