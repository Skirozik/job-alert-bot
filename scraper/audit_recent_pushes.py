"""READ-ONLY: what did the notification ledger actually claim in the last 72h,
and do any old-vocabulary tier rows (MAYBE/SKIP) still exist in the table?

WHY. The user reports ntfy pushes labelled MAYBE and missing APPLY_CAVEAT.
Nothing on master can *generate* tier=MAYBE -- the classifier enum is
APPLY/APPLY_CAVEAT/INELIGIBLE since 9bb39e6 -- but push bodies render the tier
string straight off the row (notifier.py: f"{emoji} {tier}"), so a row STORED
as MAYBE before the Aug 15 rename would push as "🟡 MAYBE" if any path ever
pushed it. This prints the ledger's own record of recent claims, the global
tier census, and how many recent APPLY_CAVEAT rows were found but never
notified (the "caveat pushes stopped arriving" measure).

Counts use count='exact' head-style queries: no rows are downloaded for the
census, so this does not reopen the egress hole the dedup work closed.

READ-ONLY. No update/delete/insert/upsert, no write RPC, and it never reads
sys.argv, so no flag can turn it into something that writes.

Run from the scraper directory:
    cd scraper && python audit_recent_pushes.py
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

# Fixed probe list rather than a fetch-everything-and-group pass, so the census
# costs a handful of count headers instead of a table download. The trailing
# total-vs-sum check catches any tier value not on this list.
TIERS = ("APPLY", "APPLY_CAVEAT", "INELIGIBLE", "PENDING", "MAYBE", "SKIP")
WINDOW_H = 72


def _count(q) -> int:
    res = q.limit(1).execute()
    return res.count or 0


def main() -> int:
    client = get_client()
    since = (datetime.now(timezone.utc) - timedelta(hours=WINDOW_H)).isoformat()

    print()
    print("TIER CENSUS (whole table, count headers only -- no rows downloaded)")
    total = _count(client.table("jobs").select("id", count="exact"))
    seen = 0
    for tier in TIERS:
        n = _count(client.table("jobs").select("id", count="exact").eq("tier", tier))
        seen += n
        flag = "   <- OLD VOCABULARY, pre-rename survivor" if tier in ("MAYBE", "SKIP") and n else ""
        print(f"  {tier:<14} {n:>7}{flag}")
    other = total - seen
    print(f"  {'(other)':<14} {other:>7}" + ("   <- tier values not on the probe list!" if other else ""))
    print(f"  {'total':<14} {total:>7}")

    print()
    print(f"CLAIMED FOR PUSH IN THE LAST {WINDOW_H}H (notified_at is the ledger the")
    print("claim function writes; every line here corresponds to a push decision)")
    rows = (client.table("jobs")
            .select("id,tier,status,company,title,notified_at")
            .gte("notified_at", since)
            .order("notified_at", desc=True)
            .limit(400).execute().data) or []
    by_tier = {}
    for r in rows:
        by_tier[r.get("tier")] = by_tier.get(r.get("tier"), 0) + 1
    for tier, n in sorted(by_tier.items(), key=lambda kv: -kv[1]):
        print(f"  {tier:<14} {n}")
    wrong = [r for r in rows if r.get("tier") not in ("APPLY", "APPLY_CAVEAT")]
    if wrong:
        print()
        print("  !! rows claimed at a tier the push gate should never pass:")
        for r in wrong[:25]:
            print(f"     {r.get('notified_at','')[:19]}  [{r.get('tier')}] "
                  f"{(r.get('company') or '?')[:24]} — {(r.get('title') or '')[:44]}")
    print()
    print("  most recent 20 claims, newest first:")
    for r in rows[:20]:
        print(f"     {r.get('notified_at','')[:19]}  [{r.get('tier')}] "
              f"{(r.get('company') or '?')[:24]} — {(r.get('title') or '')[:44]}")

    print()
    print(f"APPLY_CAVEAT FOUND IN THE LAST {WINDOW_H}H -- notified vs not. A large")
    print("'never notified' number means caveat pushes are being suppressed.")
    for tier in ("APPLY", "APPLY_CAVEAT"):
        found = _count(client.table("jobs").select("id", count="exact")
                       .eq("tier", tier).gte("found_at", since))
        silent = _count(client.table("jobs").select("id", count="exact")
                        .eq("tier", tier).gte("found_at", since).is_("notified_at", "null"))
        print(f"  {tier:<14} found {found:>4}   never notified {silent:>4}")
    silent_rows = (client.table("jobs")
                   .select("id,tier,status,company,title,found_at")
                   .eq("tier", "APPLY_CAVEAT").gte("found_at", since)
                   .is_("notified_at", "null")
                   .order("found_at", desc=True).limit(25).execute().data) or []
    for r in silent_rows:
        print(f"     {r.get('found_at','')[:19]}  [{r.get('status')}] "
              f"{(r.get('company') or '?')[:24]} — {(r.get('title') or '')[:44]}")

    print()
    print("Read-only. Nothing was written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
