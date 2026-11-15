#!/usr/bin/env python3
"""The pure co-op pre-filter. Offline: no network, no database.

Every title below is a real `title` value from the jobs table (2026-11-14),
except where a comment says otherwise.

    python test_coop_filter.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main
from main import _is_pure_coop_title

failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if not cond else ""))
    if not cond:
        failures.append(name)


# ---- blocked: a co-op with no internship word, during a school term -------
for t in (
    "Software Engineering Co-op (Summer/Fall)",            # RTX, pushed 2026-11-15
    "Spring 2027 Digital Workflow Automation Co-op (January - June)",
    "Software Developer Spring Co-Op 2027 (Bellevue)",
    "Spring & Summer 2027 Software Engineering Co-op",
    "Software Engineering Co-op",                          # no term named at all
    "Co-op, IT - Software Engineering - ATL (Spring 2027)",
    "Software Engineering Co-op_Fall 2027",                # underscore separator
    "FALL 2026: DIGITAL/SHARKS, PERFORMANCE DATA CO-OP, MARKETPLACE INTELLIGENCE (AUG",
    "Software Engineer Co-Op | Northeastern University",
    "Data Co-Ops",
    "Applications Development Cooperative Education",
    "COOPERATIVE EDUCATION STUDENT – LOAD FORECASTING",
    "Software Co Op - Fall 2027",                          # spaced spelling, made up
):
    check(f"blocked: {t!r}", _is_pure_coop_title(t))

# ---- kept -----------------------------------------------------------------
for t in (
    # combos can be taken as a summer internship
    "Software Engineering Intern/Co-op - Summer 2027",
    "Internship/Co-op - Fall 2027",
    "Data Engineering Cooperative Education Intern",
    "Co-op/Internship, Hardware Engineering (Spring 2027)",
    # summer-only co-ops cannot overlap a school term
    "2027 Summer Software Engineer Co-op",
    "Software Developer Co-Op - Summer 2027 - Indefinite Work Authorization Required",
    "Quality Data Science Co-Op - Summer 2027",
    # "co-op"-looking words that are not co-ops
    "Software Engineer, Cooperative AI",
    "Engineering Manager, Cooperative Systems",
    "Director of Security Cooperation & U.S. Defense Programs",
    "Electrical Engineer Intern, Cooper Lighting Solutions - Spring 2027",
):
    check(f"kept: {t!r}", not _is_pure_coop_title(t))

check("one-word 'Coop' is a co-op", _is_pure_coop_title("Coop Software Developer"))
check("'cooperative' alone is not", not _is_pure_coop_title("Cooperative Robotics Engineer"))
check("ordinary internship untouched", not _is_pure_coop_title("Software Engineer Intern - Summer 2027"))
check("empty title", not _is_pure_coop_title(""))

# ---- the gate in process_job ---------------------------------------------
# A pure co-op must be stored INELIGIBLE before any page fetch, model call or
# push; anything else must get past the gate.
stored, called = [], []
saved = {n: getattr(main, n) for n in ("get_job_row", "insert_job", "fetch_description",
                                       "classify", "push_job", "_is_blocked_company")}
main.get_job_row = lambda job_id: None
main.insert_job = lambda job: stored.append(dict(job))
main._is_blocked_company = lambda c: False


def _stop(*a, **k):
    called.append("past the gate")
    raise RuntimeError("past the gate")


main.fetch_description = _stop
main.classify = _stop
main.push_job = _stop
try:
    job = {"id": "ats:test-coop", "title": "Software Engineering Co-op (Summer/Fall)",
           "company": "RTX", "location": "Remote"}
    sent = main.process_job(job)
    check("pure co-op: no push", sent is False)
    check("pure co-op: stored once", len(stored) == 1)
    check("pure co-op: stored INELIGIBLE with the co-op reason",
          stored and stored[0]["tier"] == "INELIGIBLE"
          and stored[0]["reason"] == "Pre-filtered: co-op with no internship option")
    check("pure co-op: no fetch, model call or push", not called)

    stored.clear(); called.clear()
    combo = {"id": "ats:test-combo", "title": "Software Engineering Intern/Co-op - Fall 2027",
             "company": "AMD", "location": "Austin, TX"}
    try:
        main.process_job(combo)
    except RuntimeError:
        pass
    check("intern/co-op combo goes past the gate", called and not stored)
finally:
    for n, f in saved.items():
        setattr(main, n, f)

print()
if failures:
    print(f"{len(failures)} FAILED: {', '.join(sorted(set(failures)))}")
    sys.exit(1)
print("all co-op filter checks passed")
