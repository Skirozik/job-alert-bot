"""gold_star rules, and their parity with web/lib/goldStar.ts.

Every `cases` entry in fixtures/star_rules.json is asserted here AND in
web/lib/__tests__/goldStar.test.mjs. That is the contract that matters: the
phone and the dashboard must agree about what is starred, or the badge is worse
than not having one.

Run: cd scraper && python test_gold_star.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import gold_star as gs

FIXTURE = Path(__file__).parent.parent / "web" / "lib" / "star_rules.json"

_fails = 0
_ran = 0


def check(label, cond, why=""):
    # Counted here, not totalled by hand at the bottom. The arithmetic literal
    # this replaced said 50 while 56 checks actually ran, and it had no way to
    # notice: every assertion added made it wronger, silently.
    global _fails, _ran
    _ran += 1
    if cond:
        print(f"  PASS  {label}")
    else:
        _fails += 1
        print(f"  FAIL  {label}" + (f"\n        {why}" if why else ""))


print("-- shared fixture: python must match the TypeScript rules exactly --")

data = json.loads(FIXTURE.read_text())
cases = data["cases"]
check("the fixture has cases", len(cases) > 0)

for c in cases:
    job = {
        "company": c["company"], "title": c["title"], "salary": c["salary"],
        "is_easy_apply": c["is_easy_apply"], "suggested_resume": c["suggested_resume"],
        "tier": c["tier"],
    }
    got = gs.star_reasons(job)
    check(c["name"], got == c["expected"], f"expected {c['expected']}, got {got}")

print("\n-- the Easy Apply gate is a gate, not a signal --")

strong = {"company": "Apple", "title": "iOS Engineer Intern", "tier": "APPLY",
          "salary": "$80.00 per hour", "suggested_resume": "Mobile"}
check("three signals star when applying externally",
      len(gs.star_reasons({**strong, "is_easy_apply": False})) == 3)
check("...and none of them survive Easy Apply",
      gs.star_reasons({**strong, "is_easy_apply": True}) == [],
      "a resume curated for an Easy Apply is effort that never reaches a human")

print("\n-- salary parsing --")

def sal(text):
    return gs.star_reasons({"company": "Nobody", "title": "Intern", "tier": "APPLY",
                            "salary": text, "is_easy_apply": False})

check("hourly above the bar", sal("$60/hr") == ["salary"])
check("hourly below the bar", sal("$18/hr") == [])
check("annual above the bar", sal("$150,000 per year") == ["salary"])
check("annual below the bar", sal("$40,000 per year") == [])
# Changed 2026-09-11: the band is judged on its MEDIAN. The floor sank every
# wide band regardless of its midpoint, and a wide band is how the best payers
# post -- IBM's "$61,200-$138,600" failed the bar on its rising-sophomore end.
check("range uses the median", sal("$20 - $90 per hour") == ["salary"],
      "$55/hr median clears the $35/hr bar")
check("a band under the bar at BOTH ends still does not star",
      sal("$20 - $28 per hour") == [])
check("monthly pay annualises", sal("$10,000/mo") == ["salary"])
check("weekly pay annualises", sal("$2,400/week") == ["salary"])
check("biweekly is not read as weekly", sal("$1,635 - $3,185 biweekly") == [])
check("a $0 floor never stars", sal("$0 - $200,000") == [])
check("an implausible figure is rejected", sal("$91,198 - $91,202/hr") == [])
check("no salary at all", sal(None) == [])
check("prose with no figure", sal("competitive compensation") == [])
check("a bare four-figure number is read as annual, not hourly",
      sal("$95,000") == ["salary"])

print("\n-- salary parsing: the NUMBER, where a verdict cannot see the bug --")


def ann(text):
    return gs._annual_salary(text)


def near(got, want):
    """Money to the cent. Medians introduce halves, and 25.45 * 2080 is not
    bit-identical to ((21.80 + 29.10) / 2) * 2080 in IEEE754."""
    return got is not None and abs(got - want) < 0.01


# The second bound of a range usually carries no "$" of its own. Requiring one
# found a single amount, whose median is itself -- so the band ranked at its
# FLOOR and the median rule was not in force for the commonest spelling at all.
# 123 distinct live strings; NVIDIA, SAP and Blockhouse each lost a star to it.
check("a bare second bound is the top of the band", near(ann("$20-71/hr"), 45.5 * 2080),
      f"got {ann('$20-71/hr')}")
check("a k written once governs both bounds", ann("$200-260k") == 230_000,
      f"got {ann('$200-260k')}")
check("...even against a decimal floor", ann("$39.7-72.8k/yr") == 56_250,
      f"got {ann('$39.7-72.8k/yr')}")

# A workload clause is not a pay unit. _PER_WEEK matched the "/week" inside
# "20 hrs/week" and multiplied an hourly rate by 52 instead of 2080.
check("a workload clause does not supply the unit",
      near(ann("$15.09/hr, up to 20 hrs/week"), 15.09 * 2080),
      f"got {ann('$15.09/hr, up to 20 hrs/week')}")
# ...but neither is it a rate to annualise when it is the ONLY unit present.
check("a lump sum beside a workload clause is not a weekly rate",
      ann("$3,840 stipend (14-16 weeks, 20 hrs/week)") is None,
      f"got {ann('$3,840 stipend (14-16 weeks, 20 hrs/week)')}")

# The amounts were cut at the adder and the unit was not, so a housing stipend
# written after "+" set the multiplier for the band in front of it.
check("an adder's unit does not override the band's",
      ann("$25.00/hr + $2,000/month housing stipend") == 52_000,
      f"got {ann('$25.00/hr + $2,000/month housing stipend')}")
# The reason the unit search still falls back to the whole string at all.
check("an adder still supplies the unit when the band has none",
      near(ann("$21.80-$29.10 plus $5.09/hr differential"), 25.45 * 2080),
      f"got {ann('$21.80-$29.10 plus $5.09/hr differential')}")

# A rate and its annualised restatement are one wage written twice. Medianed
# together they produced $50,775,280, which the artifact guard then rejected --
# so the star was dropped from exactly the well-paid postings the median rule
# was meant to catch.
check("an annualised restatement is not a second band",
      near(ann("$22.50-$29.00/hr ($46,800-$60,320 annualized equivalent)"), 25.75 * 2080),
      f"got {ann('$22.50-$29.00/hr ($46,800-$60,320 annualized equivalent)')}")
check("a restatement written the other way round reads the same",
      ann("$93,600/yr ($45/hr)") == 93_600, f"got {ann('$93,600/yr ($45/hr)')}")
check("a weekly rate restated hourly stays weekly",
      ann("$730/week (~$18.25/hr)") == 730 * 52, f"got {ann('$730/week (~$18.25/hr)')}")

# Units are chosen by POSITION, not by a fixed precedence -- the headline rate
# comes first and the restatement is parenthesised after it.
check("two units for one job resolve to the one stated first",
      ann("$1,000-$2,000 USD weekly / $21.00-$36.00/hr") == 1500 * 52,
      f"got {ann('$1,000-$2,000 USD weekly / $21.00-$36.00/hr')}")

# An hourly floor glued to an annual ceiling is two units, not one range.
check("a band spanning hourly to annual scale is malformed",
      ann("$17.98-$135,700") is None, f"got {ann('$17.98-$135,700')}")
check("...even when a unit is stated", ann("$37.22 - $150,000.00/yr") is None,
      f"got {ann('$37.22 - $150,000.00/yr')}")
check("a genuinely wide band is NOT malformed",
      ann("$1,500-$2,500/month") == 2000 * 12, f"got {ann('$1,500-$2,500/month')}")

# The spaced slash never matched, so 199 live rows never reached the hourly
# branch at all -- rescued by accident by the magnitude fallback, with the
# artifact guard bypassed for exactly the string its comment cites.
check("a spaced slash is still an hourly rate", near(ann("$45.00 / hour"), 45 * 2080),
      f"got {ann('$45.00 / hour')}")
check("an annual figure mislabelled hourly is rejected, either spelling",
      ann("$91,198 - $91,202 / hr") is None and ann("$91,198-$91,202/hr") is None,
      f"spaced {ann('$91,198 - $91,202 / hr')}, unspaced {ann('$91,198-$91,202/hr')}")

# Non-USD: returning None is the honest answer, and the symbol forms need a
# guard on the preceding character or "S$" matches inside "US$120,000".
check("a CAD band is not scored against a USD bar", ann("$68,250-$78,000 CAD") is None)
check("CA$ is caught too", ann("CA$40/hr - CA$45/hr") is None)
check("US$ is NOT Singapore dollars", ann("US$120,000") == 120_000,
      f"got {ann('US$120,000')}")
check("a trailing 's' is not a currency symbol",
      ann("$70,000 - $90,000 plus benefits") == 80_000,
      f"got {ann('$70,000 - $90,000 plus benefits')}")
check("USD in words is not foreign", ann("$120,000 - $150,000 USD") == 135_000)

# Noise the range-aware pattern would otherwise eat as a second bound.
check("a retirement plan is not the top of the band",
      ann("$60,000 - 401k match") == 60_000, f"got {ann('$60,000 - 401k match')}")
check("a bonus percentage is not a figure",
      ann("$55,000 - 15% bonus") == 55_000, f"got {ann('$55,000 - 15% bonus')}")

# The floor matters as much as the cap: a cap alone cannot see a wrong answer
# that lands inside the plausible range.
check("an implausible hourly rate is rejected", ann("$1,000/hr") is None)
check("a lump-sum stipend is not a salary", ann("$1,000") is None)

print("\n-- company normalisation mirrors db.norm_company --")

def comp(name):
    return gs.star_reasons({"company": name, "title": "Intern", "tier": "APPLY",
                            "salary": None, "is_easy_apply": False})

check("exact name", comp("Microsoft") == ["company"])
check("legal suffix stripped", comp("Stripe, Inc.") == ["company"])
check("leading 'The' stripped", comp("The Meta") == ["company"])
check("case insensitive", comp("nVIDIA") == ["company"])
check("an unlisted company does not star", comp("Obscure Widgets") == [])
check("a substring is not a match", comp("Applebee's") == [],
      "Apple is listed; Applebee's must not inherit its star")
check("empty company does not crash or match", comp("") == [])

print("\n-- the APPLY-only gate --")

check("an APPLY_CAVEAT job never stars",
      gs.star_reasons({**strong, "is_easy_apply": False, "tier": "APPLY_CAVEAT"}) == [],
      "a caveat job already carries a known reservation; tailoring for one is odd")

print("\n-- reason_summary is human-readable for the push body --")

s = gs.reason_summary({"company": "Apple", "title": "iOS Intern", "salary": None, "tier": "APPLY",
                       "is_easy_apply": False, "suggested_resume": None})
check("names the signals in plain words", "top-tier company" in s and "mobile" in s, s)
check("an unstarred job summarises to nothing",
      gs.reason_summary({"company": "Nobody", "title": "Intern", "tier": "APPLY",
                         "salary": None, "is_easy_apply": False}) == "")

print("\n-- fixtures/salary_corpus.json: the same numbers TypeScript asserts --")

# star_rules.json pins the star VERDICT; this pins the NUMBER. Two copies can
# agree a job is starred while disagreeing about what it pays, which is how
# jobView.ts came to be the only one reading "$45.00 / hr" as hourly.
# web/lib/__tests__/salaryParity.test.mjs asserts this same file against both
# TypeScript copies. Regenerate with scraper/dump_salary_corpus.py.
CORPUS = Path(__file__).parent.parent / "fixtures" / "salary_corpus.json"
corpus = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]
check("the corpus is large enough to be a corpus", len(corpus) >= 250,
      f"only {len(corpus)} entries")

_wrong = []
for _c in corpus:
    _got, _want = gs._annual_salary(_c["salary"]), _c["annual"]
    _agree = (_got is None and _want is None) or (
        _got is not None and _want is not None and abs(_got - _want) < 0.01)
    if not _agree:
        _wrong.append((_c["salary"], _want, _got))
check("gold_star.py matches every number in the corpus", not _wrong,
      f"{len(_wrong)} of {len(corpus)} differ. If the change was deliberate, "
      f"regenerate with dump_salary_corpus.py and READ the diff:\n        "
      + "\n        ".join(f"{a!r}  corpus {b}  gold_star {c}" for a, b, c in _wrong[:10]))

print(f"\n{_ran - _fails} passed, {_fails} failed")
sys.exit(1 if _fails else 0)
