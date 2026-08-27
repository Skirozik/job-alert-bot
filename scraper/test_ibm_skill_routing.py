"""Every IBM self-assessment label must route to exactly one skill key.

WHY: these labels overlap heavily by word. "Object- or component-oriented
programming in C++, C#, Java, Python or similar" contains "Python"; "Statistical
programming in Python or R" contains "programming in Python"; the UI-frameworks
question names React while the front-end question names JavaScript; and the
containers question lists Docker and Kubernetes in one order while the
microservices question lists them in the other.

_LABEL_MAP is an ORDERED list and first match wins, so a pattern that is too
loose does not fail loudly -- it silently steals another question's answer and
puts the wrong experience level on the application. That already happened once
with field ids (see the 10542-N note in ibm.py), which is why this family is
matched by wording at all.

This asserts BOTH directions: every label finds its own key, and no label is
claimed by a pattern that does not own it.

Offline: no network, no API key, no browser.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from autofill.platforms.ibm import _LABEL_MAP, _label_spec

Q = "What best describes your "

# Every label observed live, paired with the key it must route to.
CASES = [
    (Q + "level of experience in Agile Software Development?", "agile"),
    (Q + "level of experience in Containers (e.g., Kubernetes, Docker, etc.)?", "containers"),
    (Q + "level of experience in Continuous Integration/Continuous Delivery (CI/CD)?", "ci_cd"),
    (Q + "level of experience in Data structures and algorithms?", "data_structures"),
    (Q + "level of experience in Database management system software (e.g., Hadoop, MongoDB, SQL, etc.)?", "databases"),
    (Q + "level of experience in Debugging and troubleshooting?", "debugging"),
    (Q + "level of experience in File versioning software (e.g., Git and GitHub)?", "version_control"),
    (Q + "level of experience in Linux/Unix development?", "linux_unix"),
    (Q + "level of experience in Object- or component-oriented programming in C++, C#, Java, Python or similar?", "oop"),
    (Q + "level of experience in Programming and software development?", "programming"),
    (Q + "level of experience in Cloud environments such as AWS, Azure, IBM Cloud, etc.?", "cloud"),
    (Q + "level of experience in REST APIs?", "rest_apis"),
    (Q + "level of experience in UI frameworks such as Angular, React, Vue, etc.?", "ui_frameworks"),
    (Q + "level of experience in Web services?", "web_services"),
    (Q + "experience with microservices, Docker, Kubernetes or other cloud technologies?", "microservices"),
    (Q + "experience in front end development using JavaScript, HTML5, and CSS?", "frontend_dev"),
    # Seen on earlier requisitions.
    (Q + "level of experience in Programming in Python?", "programming_python"),
    (Q + "level of experience in Programming in SQL?", "programming_sql"),
    (Q + "level of experience in Statistical programming in Python or R?", "statistical_programming"),
    (Q + "level of experience in Automation testing frameworks (e.g., Selenium)?", "automation_testing"),
    (Q + "level of experience in Data Analytics (e.g., regressions, clustering)?", "data_analytics"),
    (Q + "level of experience in Data Science frameworks (e.g., Pandas)?", "data_science_frameworks"),
    (Q + "level of experience in Data warehousing (extract, transform, load)?", "data_warehousing"),
    (Q + "level of experience in Requirements analysis and system architecture?", "requirements_analysis"),
]

failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if not cond else ""))
    if not cond:
        failures.append(name)


print("-- each label routes to its own key --")
for label, expected in CASES:
    spec = _label_spec(label)
    got = (spec.key or "").rsplit(".", 1)[-1] if spec else None
    check(f"{expected:26} <- {label[26:76]}", got == expected, f"got {got!r}")

print("\n-- no pattern claims a label it does not own --")
for label, expected in CASES:
    owners = []
    for pattern, spec in _LABEL_MAP:
        if pattern.search(label):
            owners.append((spec.key or "").rsplit(".", 1)[-1])
    check(f"{expected:26} matched by exactly one pattern",
          len(owners) == 1, f"matched by {owners}")

print("\n-- every routed key has an answer in the profile --")
from autofill.profile_loader import load_profile
levels = (load_profile().get("ibm") or {}).get("skill_levels") or {}
for _, expected in CASES:
    v = levels.get(expected)
    check(f"profile has skill_levels.{expected}", bool(v), "missing or empty")

print("\n-- byte hygiene --")
for name in ("autofill/platforms/ibm.py", "test_ibm_skill_routing.py"):
    raw = (pathlib.Path(__file__).parent / name).read_bytes()
    bad = [hex(b) for b in (0x00, 0x08, 0x0B, 0x0C, 0x1B) if bytes([b]) in raw]
    check(f"{name} has no control characters", not bad, f"found {bad}")

print()
if failures:
    print(f"{len(failures)} FAILED")
    sys.exit(1)
print(f"all {len(CASES) * 3 + 2} skill-routing checks passed")
