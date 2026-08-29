"""definitive_target_key must agree with web/lib/dupes.ts canonicalTargetKey.

Both read fixtures/canonical_target_keys.json. The point of the shared file is
that the cross-source identity cannot drift between the two languages without
a red test in both suites -- see the matching block in
web/lib/__tests__/dupes.test.mjs.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from target_key import definitive_target_key, application_href

FIXTURES = Path(__file__).parent.parent / "fixtures" / "canonical_target_keys.json"

_fails = 0


def check(label, cond, why=""):
    global _fails
    if cond:
        print(f"  PASS  {label}")
    else:
        _fails += 1
        print(f"  FAIL  {label}" + (f"\n        {why}" if why else ""))


print("-- shared fixture: python must match the TypeScript definitive keys --")

cases = json.loads(FIXTURES.read_text())["cases"]
check("the fixture is not empty", len(cases) > 0)

for case in cases:
    job = {
        "id": "fixture",
        "url": case["url"],
        "apply_url": case["apply_url"],
        "is_easy_apply": case["is_easy_apply"],
    }
    got = definitive_target_key(job)
    check(case["name"], got == case["expected"],
          f"expected {case['expected']!r}, got {got!r}")

print("\n-- application_href mirrors dupes.ts, which is what links the sources --")

check("Easy Apply uses url even when apply_url is set",
      application_href({"url": "u", "apply_url": "a", "is_easy_apply": True}) == "u",
      "the application really does happen on LinkedIn for Easy Apply")
check("otherwise apply_url wins",
      application_href({"url": "u", "apply_url": "a", "is_easy_apply": False}) == "a",
      "this is what makes a LinkedIn row resolve to the ATS req key")
check("falling back to url when apply_url is absent",
      application_href({"url": "u", "apply_url": None, "is_easy_apply": False}) == "u")
check("missing keys do not raise",
      application_href({}) == "")

print("\n-- None means 'no claim', never 'matches other unknowns' --")

a = definitive_target_key({"url": "https://weird.example/careers/1", "is_easy_apply": False})
b = definitive_target_key({"url": "https://other.example/careers/2", "is_easy_apply": False})
check("two unidentifiable rows both yield None", a is None and b is None)
check("...which the SQL sibling check treats as no match, since = NULL is never true",
      a is None,
      "if this returned a shared sentinel string, unrelated rows would suppress each other")

print(f"\n{len(cases) + 6 - _fails} passed, {_fails} failed")
sys.exit(1 if _fails else 0)
