"""READ-ONLY: how many jobs would the gold-star rules actually mark?

Scarcity is the whole feature. A star on 20% of a 1,550-row list is wallpaper --
it stops meaning "stop and spend an hour on this" and becomes one more column to
ignore. So measure before trusting the thresholds, and re-measure after any edit
to web/lib/star_rules.json.

RULE OF THUMB: if more than ~5% of To apply is starred, tighten the thresholds
or trim the company list before shipping.

No write path, no --apply, no --execute, and it never reads sys.argv -- so there
is no flag that could turn it into something that writes.

Run from the scraper directory:
    cd scraper && python count_stars.py
"""

import logging
import sys
from collections import Counter
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
COLS = "id,company,title,salary,is_easy_apply,suggested_resume,tier,status"


def _fetch_to_apply(client) -> list:
    """The same predicate the dashboard's To apply view uses."""
    rows, offset = [], 0
    while True:
        page = (client.table("jobs").select(COLS)
                .eq("status", "new").in_("tier", ["APPLY", "APPLY_CAVEAT"])
                .order("id").range(offset, offset + PAGE - 1).execute().data) or []
        rows.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
    return rows


def main() -> int:
    rows = _fetch_to_apply(get_client())
    log.info("Loaded %d rows from To apply", len(rows))

    starred, by_reason, companies, easy_blocked = [], Counter(), Counter(), 0
    for r in rows:
        reasons = star_reasons(r)
        if reasons:
            starred.append(r)
            for x in reasons:
                by_reason[x] += 1
            companies[r.get("company") or "?"] += 1
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
