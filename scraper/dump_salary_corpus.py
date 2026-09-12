"""Regenerate fixtures/salary_corpus.json -- the cross-language salary contract.

WHY THIS EXISTS. There are three copies of the salary parser: gold_star.py
drives the phone push, web/lib/goldStar.ts drives the dashboard's gold star, and
web/lib/jobView.ts drives the Salary column sort. star_rules.json already stops
the two STAR copies disagreeing about a verdict, but a verdict is coarse: two
implementations can agree that a job is starred while disagreeing wildly about
what it pays, and that is exactly how jobView drifted -- it was the only copy
that read "$45.00 / hr" as hourly. This file pins the NUMBER.

WHAT IT SELECTS. Salary strings only. No company, no title, no id, no URL. This
repo is public, and a list of pay strings leaks nothing while a list of rows
leaks the search. A stratified sample rather than all ~2,200 distinct live
strings: every shape family is represented, so the corpus cannot decay into
hundreds of variations on "$25/hr", and the file stays readable enough that a
human actually reviews the diff when it changes.

Read-only, like count_stars.py and audit_star_gaps.py. Prints JSON to stdout;
the human redirects it, reads the diff, and commits:

    cd scraper && python dump_salary_corpus.py > ../fixtures/salary_corpus.json

REGENERATING AFTER A PARSER CHANGE IS THE POINT AT WHICH YOU REVIEW IT. The
expected numbers come from THIS repo's gold_star.py, so a regeneration blesses
whatever the parser currently does. Read every changed line before committing,
or the fixture stops being a test and becomes a rubber stamp.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import gold_star as gs
from db import get_client

# One bucket per shape the parser has to get right. Order matters: a string is
# filed under the FIRST family it matches, so the specific families come before
# the general ones and "hourly" does not swallow every restatement.
FAMILIES = [
    ("non_usd",      re.compile(r"\b(?:CAD|AUD|NZD|SGD|HKD|MXN|EUR|GBP|INR)\b|CA\$|A\$|NZ\$", re.I)),
    ("restatement",  re.compile(r"\$[^$]*\$.*\b(?:annualiz|annualis|equivalent|approx)", re.I)),
    ("workload",     re.compile(r"\d[\d\s.-]*\s*(?:hrs?|hours)\s*(?:/|per\s+)\s*(?:wk|week)", re.I)),
    ("adder",        re.compile(r"\b(?:plus|additional)\b|\+", re.I)),
    ("bare_bound",   re.compile(r"\$\s*[\d,]+(?:\.\d+)?\s*[-–—]\s*\d")),
    ("k_suffix",     re.compile(r"\$\s*[\d,.]+\s*k\b", re.I)),
    ("biweekly",     gs._BIWEEKLY),
    ("weekly",       gs._PER_WEEK),
    ("monthly",      gs._PER_MONTH),
    ("hourly",       gs._HOURLY_HINT),
    ("yearly",       gs._PER_YEAR),
    ("unlabelled",   re.compile(r"\$")),
    ("no_figure",    re.compile(r"")),
]
PER_FAMILY = 30

# Shapes that are rare or absent in today's table but that the parser must keep
# getting right -- every one is a defect this contract was written to close, or
# a regression a later "simplification" would plausibly reintroduce.
SYNTHETIC = [
    "$20-71/hr",                                            # bare second bound
    "$200-260k",                                            # k governs both bounds
    "$39.7–72.8k/yr",
    "$91,198 - $91,202 / hr",                               # spaced slash, artifact
    "$45.00 / hour",
    "US$120,000",                                           # not Singapore dollars
    "$70,000 - $90,000 plus benefits",                      # trailing s is not S$
    "$120,000 - $150,000 USD",
    "$60,000 - 401k match",                                 # a retirement plan
    "$80,000-$100,000, 401(k), PTO",
    "$55,000 - 15% bonus",                                  # a percentage
    "$730/week (~$18.25/hr)",                               # weekly-first restatement
    "$93,600/yr ($45/hr)",                                  # yearly-first restatement
    "$45/hr (approximately $93,600/yr annualized)",         # hourly-first
    "$25.00/hr + $2,000/month housing stipend",             # adder's unit must not win
    "$21.80–$29.10 plus $5.09/hr differential",             # adder carries the only unit
    "$17.98-$135,700",                                      # two units as one range
    "$37.22 - $150,000.00/yr",
    "$1,000/hr",                                            # implausible rate
    "$3,840 stipend (14-16 weeks, 20 hrs/week)",            # a total, not a rate
    "$0 - $200,000",                                        # placeholder floor
    "$900 - $1,200",                                        # magnitude fallback on the floor
    "$500-$2,000 annually",                                 # explicit unit beats magnitude
    "$1,635.00 biweekly",                                   # not weekly
    "$1,000-$2,000 USD weekly / $21.00-$36.00/hr",          # two units, first one wins
]


def main() -> int:
    client = get_client()
    seen, page, size = set(), 0, 1000
    while True:
        rows = (client.table("jobs").select("salary")
                .not_.is_("salary", "null")
                .order("id").range(page * size, page * size + size - 1).execute()).data
        seen.update(r["salary"] for r in rows if (r.get("salary") or "").strip())
        if len(rows) < size:
            break
        page += 1

    picked, counts = [], {name: 0 for name, _ in FAMILIES}
    for s in sorted(seen):
        for name, pattern in FAMILIES:
            if pattern.search(s):
                if counts[name] < PER_FAMILY:
                    counts[name] += 1
                    picked.append(s)
                break

    for s in SYNTHETIC:
        if s not in picked:
            picked.append(s)

    out = {
        "_comment": [
            "Cross-language salary contract. Regenerate with:",
            "  cd scraper && python dump_salary_corpus.py > ../fixtures/salary_corpus.json",
            "",
            "Asserted by web/lib/__tests__/salaryParity.test.mjs (goldStar.ts and",
            "jobView.ts, against each other AND against these numbers) and by",
            "scraper/test_gold_star.py (gold_star.py against these numbers).",
            "",
            "star_rules.json pins the star VERDICT; this pins the NUMBER. Two copies",
            "can agree a job is starred while disagreeing about what it pays, which",
            "is how jobView.ts came to be the only one reading '$45.00 / hr' as",
            "hourly. `annual` of null means 'states no pay we can judge'.",
            "",
            "Salary strings only, never a row: this repo is public.",
            "",
            "The numbers come from this repo's own gold_star.py, so regenerating",
            "blesses whatever the parser currently does. Read the diff before you",
            "commit it, or this stops being a test.",
        ],
        "families": counts,
        "cases": [{"salary": s, "annual": gs._annual_salary(s)} for s in picked],
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    print(f"{len(picked)} strings across {len(counts)} families", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
