"""READ-ONLY: how many stars a week would each candidate tightening produce?

WHY THIS EXISTS. count_stars answers "what does the current rule mark", which is
enough to notice a problem and useless for fixing one. After the company list
grew to 121 it marked 110 of 478 To-apply rows, and 58 of those arrived in the
last 7 days. 58 hand-written resumes a week is not a workload, so the rule needs
tightening -- but "tighten it" has at least four independent levers and no
intuition about which one is doing the damage. This prints the arrival rate under
each, so the choice is made against numbers.

WHY 7 DAYS AND NOT A 4-WEEK AVERAGE. To apply only holds rows still status=new.
Every week that passes, more of that week's jobs get applied to or dismissed and
leave the queue, so older buckets look smaller than they really were. That is
survivorship, not a falling arrival rate -- averaging across it understates the
true number. The last 7 days is the least-drained bucket and therefore the only
honest one. It still understates slightly, which is the safe direction.

THE FOUR LEVERS:

  pay bar     51 of the 110 star on salary alone. The bar is $35/hr, chosen by
              the user; the original plan said $45.
  per-company The company list stars EVERY open posting at a listed employer.
              American Express has 10 and BNY has 10 -- but nobody writes ten
              custom resumes for one company, they write one or two. This caps
              stars per company, keeping the earliest-found.
  list size   121 companies, up from 106.
  reason mix  what company-only or salary-only would each cost.

READ-ONLY. No update/delete/insert/upsert/rpc, and it never reads sys.argv, so
no flag can turn it into something that writes.

Run from the scraper directory:
    cd scraper && python star_whatif.py
"""

import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from db import get_client
import gold_star

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger(__name__)

PAGE = 1000
COLS = "id,company,title,salary,is_easy_apply,suggested_resume,tier,status,found_at"

# A week of full-time hours. $35/hr -> $72,800, which is the annual figure
# already in star_rules.json, so the variants stay consistent with the shipped
# pair rather than inventing a second conversion.
HOURS_PER_YEAR = 40 * 52


def _fetch(client) -> list:
    rows, offset = [], 0
    while True:
        page = (client.table("jobs").select(COLS)
                .eq("status", "new").eq("tier", "APPLY")
                .order("id").range(offset, offset + PAGE - 1).execute().data) or []
        rows.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
    return rows


def _parse(found_at):
    if not found_at:
        return None
    try:
        ts = datetime.fromisoformat(str(found_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def evaluate(rows, hourly=None, per_company=None, reasons_allowed=None):
    """Stars under one variant. Pure apart from the module-global rule swap,
    which is restored before returning so variants cannot leak into each other.

    hourly           -- override the pay bar (annual derived, not guessed)
    per_company      -- keep at most N stars per normalised company
    reasons_allowed  -- restrict which signals may fire
    """
    base = gold_star._load()
    saved = dict(base["thresholds"])
    if hourly is not None:
        base["thresholds"] = {"hourly": hourly, "annual": hourly * HOURS_PER_YEAR}
    try:
        hits = []
        for r in rows:
            rs = gold_star.star_reasons(r)
            if reasons_allowed is not None:
                rs = [x for x in rs if x in reasons_allowed]
            if rs:
                hits.append((r, rs))
    finally:
        base["thresholds"] = saved

    if per_company is not None:
        # Earliest-found wins, so the cap is deterministic and independent of
        # the order the DB happened to return rows in.
        hits.sort(key=lambda h: (str(h[0].get("found_at") or ""), h[0]["id"]))
        seen, capped = Counter(), []
        for row, rs in hits:
            key = gold_star._norm_company(row.get("company"))
            if seen[key] < per_company:
                seen[key] += 1
                capped.append((row, rs))
        hits = capped
    return hits


def main() -> int:
    rows = _fetch(get_client())
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent = [r for r in rows if (_parse(r.get("found_at")) or cutoff) >= cutoff]
    log.info("Loaded %d To-apply rows; %d found in the last 7 days", len(rows), len(recent))

    variants = [
        ("current ($35/hr, no cap)",            dict()),
        ("pay bar $40/hr",                      dict(hourly=40)),
        ("pay bar $45/hr (the original plan)",  dict(hourly=45)),
        ("pay bar $50/hr",                      dict(hourly=50)),
        ("max 2 stars per company",             dict(per_company=2)),
        ("max 1 star per company",              dict(per_company=1)),
        ("$45/hr + max 2 per company",          dict(hourly=45, per_company=2)),
        ("$45/hr + max 1 per company",          dict(hourly=45, per_company=1)),
        ("company signal only",                 dict(reasons_allowed={"company"})),
        ("salary signal only",                  dict(reasons_allowed={"salary"})),
    ]

    print()
    print("=" * 78)
    print("STARS IN THE LAST 7 DAYS  =  custom resumes that week would have asked for")
    print("=" * 78)
    print(f"  {'variant':<38} {'week':>5} {'backlog':>8}   {'':<20}")
    print("  " + "-" * 74)
    for label, kw in variants:
        week = len(evaluate(recent, **kw))
        allrows = len(evaluate(rows, **kw))
        bar = "#" * min(week, 40)
        print(f"  {label:<38} {week:>5} {allrows:>8}   {bar}")

    print()
    print("  A sustainable target is roughly 3-6 a week: one custom resume is 30-60")
    print("  minutes, so 5 is already most of an evening. Pick the first variant")
    print("  that lands in that band rather than the one that looks tidiest.")
    print()

    # The per-company cap is the lever with the least intuition behind it, so
    # show what it actually drops rather than asking anyone to trust the number.
    by_company = defaultdict(list)
    for row, _ in evaluate(recent):
        by_company[row.get("company") or "?"].append(row)
    heavy = sorted(((c, v) for c, v in by_company.items() if len(v) > 2),
                   key=lambda kv: -len(kv[1]))
    if heavy:
        print("  Companies with 3+ starred postings THIS WEEK -- the case for the cap.")
        print("  Every one of these asks for N custom resumes at a single employer:")
        for company, jobs in heavy[:12]:
            print(f"    {len(jobs):>3}  {company}")
            for j in jobs[:3]:
                print(f"           - {(j.get('title') or '')[:62]}")
            if len(jobs) > 3:
                print(f"           ... and {len(jobs) - 3} more")
    print()
    print("Read-only. Nothing was written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
