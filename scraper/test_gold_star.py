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


def check(label, cond, why=""):
    global _fails
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
    }
    got = gs.star_reasons(job)
    check(c["name"], got == c["expected"], f"expected {c['expected']}, got {got}")

print("\n-- the Easy Apply gate is a gate, not a signal --")

strong = {"company": "Apple", "title": "iOS Engineer Intern",
          "salary": "$80.00 per hour", "suggested_resume": "Mobile"}
check("three signals star when applying externally",
      len(gs.star_reasons({**strong, "is_easy_apply": False})) == 3)
check("...and none of them survive Easy Apply",
      gs.star_reasons({**strong, "is_easy_apply": True}) == [],
      "a resume curated for an Easy Apply is effort that never reaches a human")

print("\n-- salary parsing --")

def sal(text):
    return gs.star_reasons({"company": "Nobody", "title": "Intern",
                            "salary": text, "is_easy_apply": False})

check("hourly above the bar", sal("$60/hr") == ["salary"])
check("hourly below the bar", sal("$18/hr") == [])
check("annual above the bar", sal("$150,000 per year") == ["salary"])
check("annual below the bar", sal("$40,000 per year") == [])
check("range uses the lower bound", sal("$20 - $90 per hour") == [],
      "starring on the ceiling is how a badge becomes noise")
check("no salary at all", sal(None) == [])
check("prose with no figure", sal("competitive compensation") == [])
check("a bare four-figure number is read as annual, not hourly",
      sal("$95,000") == ["salary"])

print("\n-- company normalisation mirrors db.norm_company --")

def comp(name):
    return gs.star_reasons({"company": name, "title": "Intern",
                            "salary": None, "is_easy_apply": False})

check("exact name", comp("Microsoft") == ["company"])
check("legal suffix stripped", comp("Stripe, Inc.") == ["company"])
check("leading 'The' stripped", comp("The Meta") == ["company"])
check("case insensitive", comp("nVIDIA") == ["company"])
check("an unlisted company does not star", comp("Obscure Widgets") == [])
check("a substring is not a match", comp("Applebee's") == [],
      "Apple is listed; Applebee's must not inherit its star")
check("empty company does not crash or match", comp("") == [])

print("\n-- reason_summary is human-readable for the push body --")

s = gs.reason_summary({"company": "Apple", "title": "iOS Intern", "salary": None,
                       "is_easy_apply": False, "suggested_resume": None})
check("names the signals in plain words", "top-tier company" in s and "mobile" in s, s)
check("an unstarred job summarises to nothing",
      gs.reason_summary({"company": "Nobody", "title": "Intern",
                         "salary": None, "is_easy_apply": False}) == "")

total = 1 + len(cases) + 2 + 8 + 7 + 2
print(f"\n{total - _fails} passed, {_fails} failed")
sys.exit(1 if _fails else 0)
