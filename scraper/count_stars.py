"""READ-ONLY: how many jobs would the gold-star rules actually mark?

Scarcity is the whole feature. A star on 20% of a 1,550-row list is wallpaper --
it stops meaning "stop and spend an hour on this" and becomes one more column to
ignore. So measure before trusting the thresholds, and re-measure after any edit
to web/lib/star_rules.json.

RULE OF THUMB: if more than ~5% of To apply is starred, tighten the thresholds
or trim the company list before shipping.

READ THE ARRIVAL RATE, NOT ONLY THE PERCENTAGE. To apply is a backlog that has
been accumulating for weeks, but the star fires once, at push time, on a job the
day it is found. The number that decides whether the feature is usable is
"starred jobs per week", because that is how many custom resumes it is asking
for. A scary-looking percentage against a large backlog can still be a
manageable two or three a week, and a comfortable percentage can still be
unusable if they all land on one day.

No write path, no --apply, no --execute, and it never reads sys.argv -- so there
is no flag that could turn it into something that writes.

Run from the scraper directory:
    cd scraper && python count_stars.py
"""

import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from db import get_client
from gold_star import star_reasons

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger(__name__)

PAGE = 1000
COLS = "id,company,title,salary,is_easy_apply,suggested_resume,tier,status,found_at"


def _fetch_to_apply(client) -> list:
    """The same predicate the dashboard's To apply view uses: tier APPLY, status new."""
    rows, offset = [], 0
    while True:
        page = (client.table("jobs").select(COLS)
                # APPLY only -- the gold star deliberately excludes APPLY_CAVEAT, so
                # measuring against caveats too would report a rate against a
                # population the rule cannot mark.
                .eq("status", "new").eq("tier", "APPLY")
                .order("id").range(offset, offset + PAGE - 1).execute().data) or []
        rows.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
    return rows


def _weeks_ago(found_at):
    """How many whole weeks back this job was discovered, or None if unparseable."""
    if not found_at:
        return None
    try:
        # Supabase returns ISO with a trailing Z, which fromisoformat rejects
        # before Python 3.11.
        ts = datetime.fromisoformat(str(found_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - ts).days // 7)


def main() -> int:
    rows = _fetch_to_apply(get_client())
    log.info("Loaded %d rows from To apply (tier=APPLY only)", len(rows))

    starred, by_reason, companies, easy_blocked = [], Counter(), Counter(), 0
    per_week = Counter()
    for r in rows:
        reasons = star_reasons(r)
        if reasons:
            starred.append(r)
            for x in reasons:
                by_reason[x] += 1
            companies[r.get("company") or "?"] += 1
            week = _weeks_ago(r.get("found_at"))
            if week is not None:
                per_week[week] += 1
        elif r.get("is_easy_apply") and star_reasons({**r, "is_easy_apply": False}):
            # Would have starred but for the Easy Apply gate. Worth surfacing:
            # if this number is large the gate is doing most of the filtering,
            # which is a fact about the pipeline rather than about the rules.
            easy_blocked += 1

    pct = 100.0 * len(starred) / max(len(rows), 1)

    print()
    print("SUMMARY")
    print(f"  To apply rows ................... {len(rows)}")
    print(f"  Would be starred ................ {len(starred)}  ({pct:.1f}%)")
    print(f"  Blocked ONLY by the Easy Apply gate  {easy_blocked}")
    print()
    print("  by reason (a job can have several):")
    for reason, n in by_reason.most_common():
        print(f"    {reason:<10} {n}")
    print()
    print("  starred ARRIVALS per week -- this is the real workload, one custom")
    print("  resume each. The percentage above is against a backlog; this is not.")
    if per_week:
        for week in sorted(per_week):
            label = "this week" if week == 0 else f"{week} week(s) ago"
            print(f"    {label:<16} {per_week[week]:>4}  {'#' * min(per_week[week], 50)}")
        # Deliberately NOT a 4-week average. To apply only holds rows still
        # status=new, so every week that passes drains as jobs get applied to
        # or dismissed. Older buckets are survivors, not a record of what
        # arrived, and averaging across them understates the real rate. Week 0
        # is the least-drained bucket and so the only honest one.
        print(f"    -> ~{per_week.get(0, 0)} custom resumes in the last 7 days.")
        print("       Read that number, not an average across the older weeks --")
        print("       those look small only because they have been actioned away.")
    else:
        print("    (no parseable found_at timestamps)")
    print()
    print("  top starred companies:")
    for company, n in companies.most_common(15):
        print(f"    {n:>4}  {company}")
    print()
    if pct > 5:
        print(f"  ⚠  {pct:.1f}% is above the ~5% rule of thumb. Tighten the thresholds")
        print("     or trim the company list in web/lib/star_rules.json -- a star on")
        print("     everything is a star on nothing.")
    else:
        print(f"  {pct:.1f}% is within the ~5% rule of thumb.")
    print()
    print("Read-only. Nothing was written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
