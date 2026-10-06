"""Archive review jobs that were never acted on and have aged out.

A job stays in the dashboard's review queue (status 'new', tier APPLY or
APPLY_CAVEAT) until it is applied to, saved or dismissed. Untouched ones piled
up: on 2026-10-06 the queue held 6,535 rows going back to August, every one
re-downloaded on each dashboard open, though most of those postings had long
closed. This moves rows found more than ARCHIVE_AFTER_DAYS ago to status
'archived', which the dashboard never loads.

Nothing is deleted. The rows stay in the table, so dedup still recognises them
(a repost is not re-classified or re-pushed) and the notification ledger still
counts them as already shown. Only status changes, and only on rows that are
still 'new': anything you applied to, saved or dismissed is untouched.

Run from the scraper directory (the digest workflow runs it with --apply):
    python archive_stale.py            # dry run: how many would be archived
    python archive_stale.py --apply    # archive them
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

from postgrest import CountMethod, ReturnMethod

from db import QuotaExceeded, _raise_if_quota, get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

ARCHIVE_AFTER_DAYS = 45
REVIEW_TIERS = ["APPLY", "APPLY_CAVEAT"]


def cutoff(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=ARCHIVE_AFTER_DAYS)).isoformat()


def _stale(query, before: str):
    """The one filter both the count and the update use, so they cannot differ."""
    return query.eq("status", "new").in_("tier", REVIEW_TIERS).lt("found_at", before)


def count_stale(client, before: str) -> int:
    query = client.table("jobs").select("id", count=CountMethod.exact).limit(1)
    return _stale(query, before).execute().count or 0


def archive_stale(client, before: str) -> int:
    query = client.table("jobs").update(
        {"status": "archived"}, returning=ReturnMethod.minimal, count=CountMethod.exact)
    return _stale(query, before).execute().count or 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="archive (default is a dry run)")
    args = ap.parse_args()

    before = cutoff()
    try:
        client = get_client()
        if not args.apply:
            log.info("DRY RUN: %d review jobs found before %s would be archived",
                     count_stale(client, before), before[:10])
            return 0
        log.info("Archived %d review jobs found before %s", archive_stale(client, before), before[:10])
        return 0
    except QuotaExceeded:
        log.error("Supabase quota exhausted: nothing archived")
        return 1
    except Exception as exc:
        _raise_if_quota(exc)
        raise


if __name__ == "__main__":
    sys.exit(main())
