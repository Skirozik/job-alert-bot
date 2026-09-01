"""Requeue rows hijacked by the zombie Modal deployment for reclassification.

WHAT HAPPENED. modal_app.py's deployed copy of this scraper -- frozen at a
pre-Aug-15 snapshot, old APPLY/MAYBE/SKIP vocabulary, no notification ledger --
resumed when the Modal workspace's spend limit reset on the Sept 1 month
boundary. When it reached a new posting before the GitHub Actions watchers, it
inserted the row as MAYBE or SKIP. Those rows are then invisible: the dashboard
shows no MAYBE view, and the real pipeline sees "already stored" and never
re-classifies. A posting the current classifier would have pushed as APPLY or
APPLY_CAVEAT was silently lost.

WHAT THIS DOES. Flips the hijacked rows to tier='PENDING'. That is the entire
write. The existing PENDING drain (main.py retry_pending, built for the
classifier-outage recovery) then does the real work on the next scheduled run:
classifies each row with the CURRENT classifier, stores a current-vocabulary
tier, and pushes APPLY/APPLY_CAVEAT results through the claim ledger -- so a
job that deserves a ping still gets one, and a twin that already pinged is
suppressed by the sibling clause instead of pinging twice.

WHICH ROWS QUALIFY, exactly:
  - tier='MAYBE'  and status='new'                       (all of these are
    rogue writes -- audit_frozen_rows verified every MAYBE row in the table
    was created on/after 2026-09-01)
  - tier='SKIP'   and status='new' and found_at >= 2026-09-01  (the 6,672
    pre-rename SKIP rows are deliberately untouched -- they are history, and
    what to do with their label is a separate decision)

RUN THE ZOMBIE DOWN FIRST. If rogue rows keep appearing this script is a mop
fighting a running tap; the dry run warns when the freshest rogue row is less
than 30 minutes old.

Dry run by default -- prints what it would flip and writes nothing.
    cd scraper && python requeue_rogue_rows.py             # dry run
    cd scraper && python requeue_rogue_rows.py --execute   # actually requeue
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger(__name__)

ROGUE_EPOCH = "2026-09-01T00:00:00+00:00"   # the spend-limit reset


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def main() -> int:
    execute = "--execute" in sys.argv[1:]
    client = get_client()

    maybe = (client.table("jobs")
             .select("id,company,title,tier,status,found_at")
             .eq("tier", "MAYBE").eq("status", "new")
             .order("found_at").execute().data) or []
    skips = (client.table("jobs")
             .select("id,company,title,tier,status,found_at")
             .eq("tier", "SKIP").eq("status", "new").gte("found_at", ROGUE_EPOCH)
             .order("found_at").execute().data) or []
    rows = maybe + skips

    print()
    print(f"{'WOULD REQUEUE' if not execute else 'REQUEUEING'} {len(rows)} rogue row(s) "
          f"({len(maybe)} MAYBE, {len(skips)} SKIP-since-Sep-1) to PENDING")
    print("=" * 92)
    for r in rows:
        print(f"  {r.get('found_at','')[:19]}  [{r['tier']:<5}] "
              f"{(r.get('company') or '?')[:24]:<24} {(r.get('title') or '')[:44]}")

    fresh = max((f for f in (_parse(r.get("found_at")) for r in rows) if f), default=None)
    if fresh and datetime.now(timezone.utc) - fresh < timedelta(minutes=30):
        print()
        print(f"  ⚠  The freshest rogue row is only "
              f"{(datetime.now(timezone.utc) - fresh).seconds // 60} min old.")
        print("     The zombie Modal app may STILL BE RUNNING. Stop it on modal.com")
        print("     first, or this requeue will be mopping under a running tap.")

    if not execute:
        print()
        print("Dry run — nothing was written. Re-run with --execute (or dispatch the")
        print("maintenance task with confirm=true) to requeue.")
        return 0

    flipped = 0
    for r in rows:
        # One row at a time by primary key, so a partial failure is visible in
        # the log rather than a silent half-applied bulk update.
        client.table("jobs").update({"tier": "PENDING"}).eq("id", r["id"]) \
              .eq("tier", r["tier"]).eq("status", "new").execute()
        flipped += 1
    print()
    print(f"Requeued {flipped} row(s). The next scheduled scrape run's retry_pending")
    print("pass will classify them with the current classifier and push anything that")
    print("lands APPLY/APPLY_CAVEAT through the notification ledger (duplicates of")
    print("already-pushed twins are suppressed by the sibling clause).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
