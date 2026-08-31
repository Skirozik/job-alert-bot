"""READ-ONLY: which companies in To apply are NOT on the gold-star list?

WHY: the star's company signal is a HAND-WRITTEN list, so its failures are
silent omissions. Wells Fargo was missing while Goldman Sachs, Morgan Stanley,
JPMorgan Chase, Capital One, American Express, Visa, Mastercard and BlackRock
were all present -- an arbitrary gap, discovered only because someone noticed.
This lists every gap at once so they can be reviewed together.

DELIBERATELY COMPLETE, NOT TOP-N. Ranking by posting volume would bury exactly
the companies most worth catching: a firm with two openings can matter more than
one with eighty, and this project has already dismissed TikTok and ByteDance
*for* being high-volume. A top-N report would have hidden Wells Fargo behind the
noise it exists to find.

WHAT THIS CANNOT DO, stated plainly: no amount of looking at this database can
tell you a company is prestigious or pays well. Prestige is external knowledge.
The report surfaces WHO IS THERE and what evidence exists in the row (stated
salary, whether it would already star on another signal); deciding who belongs
is a judgement call the data cannot make.

No write path, no --execute, and it never reads sys.argv for anything but the
optional --grep filter, which only narrows the printing.

    cd scraper && python audit_star_gaps.py
    cd scraper && python audit_star_gaps.py --grep wells
"""

import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from db import get_client
from gold_star import _norm_company, _starred_companies, _salary_clears_bar, star_reasons

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger(__name__)

PAGE = 1000


def main() -> int:
    grep = None
    for i, a in enumerate(sys.argv):
        if a == "--grep" and i + 1 < len(sys.argv):
            grep = sys.argv[i + 1].lower()

    client = get_client()
    rows, offset = [], 0
    while True:
        page = (client.table("jobs")
                .select("id,company,title,salary,is_easy_apply,suggested_resume,tier,status")
                .eq("status", "new").eq("tier", "APPLY")
                .order("id").range(offset, offset + PAGE - 1).execute().data) or []
        rows.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
    log.info("Loaded %d To-apply rows (tier=APPLY, status=new)", len(rows))

    starred_set = _starred_companies()
    gaps = defaultdict(lambda: {"n": 0, "salaries": [], "would_star": 0, "titles": []})

    for r in rows:
        raw = (r.get("company") or "?").strip()
        if _norm_company(raw) in starred_set:
            continue
        g = gaps[raw]
        g["n"] += 1
        if r.get("salary"):
            g["salaries"].append(r["salary"])
        if star_reasons(r):
            g["would_star"] += 1
        if len(g["titles"]) < 2:
            g["titles"].append((r.get("title") or "")[:44])

    log.info("%d distinct companies in To apply are NOT on the star list", len(gaps))

    shown = sorted(gaps.items(), key=lambda kv: (-kv[1]["n"], kv[0].lower()))
    if grep:
        shown = [(c, g) for c, g in shown if grep in c.lower()]
        print(f"\nFiltered to names containing {grep!r}: {len(shown)} match(es)\n")

    print()
    print(f"{'JOBS':>5}  {'PAY?':<5}  COMPANY")
    print("-" * 76)
    for company, g in shown:
        pay = "$" if g["salaries"] else ""
        star = " *already stars on another signal" if g["would_star"] else ""
        print(f"{g['n']:>5}  {pay:<5}  {company}{star}")
        if g["salaries"]:
            print(f"{'':>12}  e.g. {g['salaries'][0][:52]}")

    # The canary this report was built to answer.
    wells = [c for c in gaps if "wells" in c.lower() or "fargo" in c.lower()]
    print()
    print("=" * 76)
    if wells:
        print("WELLS FARGO CHECK: found in the gap list ->")
        for c in wells:
            g = gaps[c]
            print(f"   {g['n']} job(s) as {c!r}")
            for t in g["titles"]:
                print(f"      - {t}")
    else:
        print("WELLS FARGO CHECK: NOT PRESENT in To apply at all.")
        print("   Meaning it is missing from the star list AND has no open tier=APPLY")
        print("   row right now -- so no audit of this database could have surfaced it.")
        print("   That is the limit of a data-driven audit, not a bug in it.")
    print("=" * 76)
    print()
    print("Read-only. Nothing was written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
