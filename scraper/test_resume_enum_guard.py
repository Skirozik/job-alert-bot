"""The suggested_resume enum is guidance to the model, not a constraint.

classify_job's input_schema has always declared suggested_resume as an enum of
Mobile | AI | Frontend | General. A live check on 2026-08-23 found the DB
holding "N/A" (39 rows) and "1Password" (1 row -- the model echoing the company
name into the field). `tier` was validated on the way out of the tool call and
this was not, so whatever came back was stored verbatim and then leaked into
web/types/job.ts, where '1Password' and 'N/A' had been ADDED to the
SuggestedResume union to make the bad data typecheck.

Runs offline: the Anthropic client is replaced with a stub, so no API key, no
network, no cost.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import classifier

failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if not cond else ""))
    if not cond:
        failures.append(name)


class _Block:
    type = "tool_use"

    def __init__(self, payload):
        self.input = payload


class _Resp:
    def __init__(self, payload):
        self.content = [_Block(payload)]


class _Messages:
    def __init__(self, payload):
        self._payload = payload

    def create(self, **_kw):
        return _Resp(self._payload)


class _FakeClient:
    def __init__(self, payload):
        self.messages = _Messages(payload)


def classify_with(payload):
    """Run classify() against a stubbed model response."""
    classifier._client = _FakeClient(payload)
    classifier._API_HARD_DOWN = None
    # A real description keeps _apply_title_only_override out of the way; this
    # test is about the resume field, not the title-only rule.
    return classifier.classify({
        "id": "test:1", "title": "Software Engineer Intern",
        "company": "Example Corp", "location": "Atlanta, GA",
        "description": "Build web apps in Python and React. " * 20,
    })


base = {"tier": "APPLY", "reason": "Python and React named in the posting.", "salary": ""}

r = classify_with({**base, "suggested_resume": "N/A"})
check("'N/A' is coerced to General", r["suggested_resume"] == "General", r.get("suggested_resume"))
check("...and the tier is untouched", r["tier"] == "APPLY", r.get("tier"))
check("...and the reason is untouched", r["reason"] == base["reason"])

r = classify_with({**base, "suggested_resume": "1Password"})
check("a company name is coerced", r["suggested_resume"] == "General", r.get("suggested_resume"))

r = classify_with({k: v for k, v in base.items()})
check("a MISSING field is coerced", r["suggested_resume"] == "General", r.get("suggested_resume"))

r = classify_with({**base, "suggested_resume": None})
check("None is coerced", r["suggested_resume"] == "General", r.get("suggested_resume"))

r = classify_with({**base, "suggested_resume": "general"})
check("wrong case is coerced (the enum is exact)", r["suggested_resume"] == "General", r.get("suggested_resume"))

for good in ("Mobile", "AI", "Frontend", "General"):
    r = classify_with({**base, "suggested_resume": good})
    check(f"'{good}' passes through untouched", r["suggested_resume"] == good, r.get("suggested_resume"))

check("_VALID_RESUMES matches the tool schema enum",
      set(classifier._VALID_RESUMES) ==
      set(classifier._CLASSIFY_TOOL["input_schema"]["properties"]["suggested_resume"]["enum"]),
      f"{classifier._VALID_RESUMES} vs schema")

raw = pathlib.Path(__file__).read_bytes()
check("no control characters in this file",
      not any(bytes([b]) in raw for b in (0x00, 0x08, 0x0B, 0x0C, 0x1B)))

print()
if failures:
    print(f"{len(failures)} FAILED: {', '.join(failures)}")
    sys.exit(1)
print("all resume-enum checks passed")
