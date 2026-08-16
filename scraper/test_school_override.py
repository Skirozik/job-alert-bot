"""Guards the school-specific override in classifier.py.

This override closes a leak that put jobs the candidate is categorically
ineligible for INTO the feed — same-school-only co-ops for schools he does not
attend. Nine such rows sat at tier=APPLY before it was fixed.

Both directions matter, and the second one more. A miss leaves an ineligible
job on the list, which costs a few seconds to dismiss. A false positive
silently removes a real job, and nothing downstream ever surfaces it again.

Run:  cd scraper && python test_school_override.py
"""

import sys

import classifier as C

_pass = _fail = 0


def check(name, cond, detail=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{f' — {detail}' if detail else ''}")


def tier(title, desc=""):
    return C._apply_school_specific_override(
        {"id": "test", "title": title, "description": desc},
        {"tier": "APPLY", "reason": ""},
    )["tier"]


# ── Must be blocked. Every one of these was live in the database at APPLY. ──
print("\n-- school-restricted co-ops must be ruled ineligible --")

# The restriction is in the TITLE and appears nowhere in the body. Susquehanna's
# 2,483-character description never contains "Drexel", "University" or
# "College", which is why reading only the description missed both of these.
for title in [
    "Equity Options AI Co-op with Drexel University",
    "Financial Reporting AI Co-op with Drexel University",
]:
    check(f"'…with Drexel University' — {title[:28]}…", tier(title) == "INELIGIBLE",
          "the school is named only in the title")

# School first, role after.
check("'<School> Co-op: <role>'",
      tier("Drexel University Co-op: Software Engineering/Full stack development") == "INELIGIBLE")

# A school named without the word University or College after it.
check("'Drexel Co-op' with no University suffix",
      tier("Software Developer Intern- Drexel Co-op US") == "INELIGIBLE")

# Pipe-delimited, and spelled "Co-Op" — the capital O is what the old
# case-sensitive [Cc]o[\s-]?op pattern could not match, and it accounts for all
# five of these on its own.
for school in ["Northeastern University", "University of Toronto", "McGill University",
               "University of Waterloo", "Georgia Tech"]:
    check(f"'Co-Op | {school}'",
          tier(f"Software Engineer Co-Op | {school}") == "INELIGIBLE",
          "capital 'O' in Co-Op, and Georgia Tech has no University/College suffix")


# ── Must stay actionable. A false positive here silently deletes a real job. ──
print("\n-- real internships must NOT be caught --")

check("the candidate's OWN school is not a restriction",
      tier("Software Engineer Co-Op | Georgia State University") == "APPLY",
      "blocking his own school would be the worst possible failure")

# "IBM co-op program" in an IBM posting's own description matched an earlier,
# looser version of the bare-school pattern and ruled him ineligible for a job
# he had already applied to. Any company running a co-op would have tripped it.
check("a COMPANY's own co-op programme is not a school restriction",
      tier("Software Developer Spring Co-op 2027",
           "IBM co-op program, open to any accredited college or university.") == "APPLY",
      "'IBM co-op' must not read as a school named IBM")
check("...nor another company's",
      tier("Software Engineer Intern", "Apple co-op program for all majors.") == "APPLY")

check("a generic co-op with no school named",
      tier("Summer Co-op Software Engineer", "Any accredited university.") == "APPLY")
check("'Engineering Co-op'", tier("Engineering Co-op", "Open to all students.") == "APPLY")
check("'Co-Op Developer Digital Product - 2027'",
      tier("Co-Op Developer Digital Product - 2027") == "APPLY",
      "IBM's own title format — Co-Op leads the title and names no school")
check("'Spring Co-op' is a season, not a school",
      tier("Site Reliability Engineer Spring Co-op 2027") == "APPLY")
check("'an accredited college or university' is not a named school",
      tier("Software Engineer Co-op",
           "Partnered with an accredited college or university.") == "APPLY",
      "the [A-Z] proper-noun guard in _SCHOOL_NAME_RE is what prevents this")
check("a hyphenated product name before Co-op",
      tier("Associate Application Developer - Adobe Experience - Co-op 2026") == "APPLY")


# ── The override only ever downgrades an actionable verdict. ──
print("\n-- the override never promotes --")
already = C._apply_school_specific_override(
    {"id": "t", "title": "Co-op with Drexel University", "description": ""},
    {"tier": "INELIGIBLE", "reason": "already out"},
)
check("an INELIGIBLE verdict is left alone", already["reason"] == "already out")

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
