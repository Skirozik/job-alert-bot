"""Pure-function tests for the IBM filler — no browser, no profile, no network.

These cover the logic that decides WHICH fields get touched, which is where a
silent regression does real damage: skipping a real question reads exactly like
a form that didn't ask it, and filling a -sample template row corrupts the
submission with no visible error.

Run:  cd scraper && python -m autofill.test_ibm_mapping
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autofill.platforms.ibm import (
    _FIELD_MAP, _is_eeo_id, _is_repeat_row, _is_sample_id, _join_locations,
    _lookup, _radio_option_id, _SOURCE_OPTIONS, validate_ibm_profile,
)
from autofill.platforms.dispatch import detect_platform, get_filler
from autofill.widgets import is_placeholder_label, norm

_pass = _fail = 0


def check(name, cond, detail=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{f' — {detail}' if detail else ''}")


def _good_profile():
    return {
        "ibm": {
            "source": "Job Board",
            "work_authorized": True,
            "requires_sponsorship": False,
            "attended_university": True,
            "consider_other_locations": True,
            "residence_differs_from_application": False,
            "worked_at_ibm_before": False,
            "privacy_agree": True,
            "certify_information_accurate": True,
            "university_country": "United States",
            "university": "Georgia State University",
            "degree": "Bachelor's Degree",
            "specialization": "Computer Science",
            "willing_to_relocate": "Yes",
            "hybrid_requirement": "Yes",
            "resident_of_china_or_south_korea": "No",
            "birth_month": "March",
            "birth_day": "14",
            "available_start_month_year": "May 2027",
            "location_1": "Atlanta, GA",
            "location_2": "Austin, TX",
            "location_3": "Research Triangle Park, NC",
        }
    }


print("\n-- sample-row detection (filling one corrupts the submission) --")
check("37299-1-sample is a sample row", _is_sample_id("37299-1-sample"))
check("9017-3-sample is a sample row", _is_sample_id("9017-3-sample"))
check("37299-1-2 is NOT a sample row", not _is_sample_id("37299-1-2"))
check("10542-1 is NOT a sample row", not _is_sample_id("10542-1"))
check("plain id is NOT a sample row", not _is_sample_id("10480"))

print("\n-- repeat-row parsing (THE trap: 10542-1 is a normal field) --")
check("37299-7-2 parses as a repeat row", _is_repeat_row("37299-7-2") == ("37299", "7", "2"))
check("9017-1-1 parses as a repeat row", _is_repeat_row("9017-1-1") == ("9017", "1", "1"))
check("10542-1 is NOT a repeat row", _is_repeat_row("10542-1") is None,
      "if this regresses, relocation/locations/start-date are silently skipped")
check("10542-2 is NOT a repeat row", _is_repeat_row("10542-2") is None)
check("10542-3 is NOT a repeat row", _is_repeat_row("10542-3") is None)
check("35979_month is NOT a repeat row", _is_repeat_row("35979_month") is None)
check("37299-7-N is NOT a repeat row (non-numeric row)", _is_repeat_row("37299-7-N") is None)

print("\n-- EEO block is skipped entirely --")
for fid in ["12704", "12705", "12706", "12707", "12708", "12709", "12710", "12711"]:
    check(f"{fid} is EEO", _is_eeo_id(fid))
check("13602_gender is EEO", _is_eeo_id("13602_gender"))
check("13602 is EEO", _is_eeo_id("13602"))
check("12712 is NOT EEO", not _is_eeo_id("12712"))
check("1270 is NOT EEO", not _is_eeo_id("1270"))
check("10542-1 is NOT EEO", not _is_eeo_id("10542-1"))
check("127040 is NOT EEO", not _is_eeo_id("127040"), "prefix match must not over-reach")

print("\n-- radio option ids --")
check("Yes -> _37", _radio_option_id("10480", True) == "10480_37")
check("No  -> _38", _radio_option_id("10480", False) == "10480_38")

print("\n-- _FIELD_MAP integrity --")
p = _good_profile()
bad_keys = [k for k, f in _FIELD_MAP.items()
            if f.key and _lookup(p, f.key) in (None, "")]
check("every mapped key resolves in a complete profile", not bad_keys, str(bad_keys))

KINDS = {"yesno", "radio_label", "check", "select", "dynamic", "text", "textarea", "file"}
bad_kinds = [(k, f.kind) for k, f in _FIELD_MAP.items() if f.kind not in KINDS]
check("every kind is known", not bad_kinds, str(bad_kinds))

check("no mapped id is an EEO id", not [k for k in _FIELD_MAP if _is_eeo_id(k)])
check("no mapped id is a sample id", not [k for k in _FIELD_MAP if _is_sample_id(k)])
check("32766 (hidden dependent) is not mapped", "32766" not in _FIELD_MAP)
check("every field with no key has a resolver",
      not [k for k, f in _FIELD_MAP.items() if not f.key and f.resolver is None])
check("20527 resolves its option by label, not a hardcoded id",
      _FIELD_MAP["20527"].option_label is not None and "20527_1043734" not in _FIELD_MAP)

print("\n-- validate_ibm_profile: the YAML gotchas --")
check("a complete profile validates clean", validate_ibm_profile(_good_profile()) == [])
check("a missing ibm: block is reported", len(validate_ibm_profile({})) == 1)

bad = _good_profile()
bad["ibm"]["willing_to_relocate"] = True     # unquoted `Yes:` in YAML
probs = validate_ibm_profile(bad)
check("unquoted `Yes` (bool where option text belongs) is rejected",
      any("willing_to_relocate" in x for x in probs))
check("...and the message explains the quoting fix",
      any("Quote it" in x for x in probs), str(probs))

bad = _good_profile()
bad["ibm"]["work_authorized"] = "Yes"        # string where a bool belongs
check("string where a radio bool belongs is rejected",
      any("work_authorized" in x for x in validate_ibm_profile(bad)))

bad = _good_profile()
bad["ibm"]["source"] = "job board"           # wrong case
probs = validate_ibm_profile(bad)
check("a source outside the closed list is rejected (case matters)",
      any("ibm.source" in x for x in probs))

bad = _good_profile()
del bad["ibm"]["birth_day"]
check("a missing required key is reported",
      any("birth_day" in x for x in validate_ibm_profile(bad)))

check("'Job Board' is in the closed list", "Job Board" in _SOURCE_OPTIONS)

print("\n-- location textarea resolver --")
check("joins all three with ', '",
      _join_locations(_good_profile()) == "Atlanta, GA, Austin, TX, Research Triangle Park, NC")
part = _good_profile()
part["ibm"]["location_2"] = ""
check("skips empties", _join_locations(part) == "Atlanta, GA, Research Triangle Park, NC")
check("empty profile yields empty string", _join_locations({"ibm": {}}) == "")

print("\n-- dispatch routing --")
check("careers.ibm.com -> ibm",
      detect_platform("https://careers.ibm.com/en_US/careers/JobApplication?jobId=127789") == "ibm")
check("acme.avature.net is NOT routed to ibm",
      detect_platform("https://acme.avature.net/careers/ApplicationMethods") != "ibm",
      "a non-IBM Avature tenant would get IBM's field ids typed into it")
check("acme.avature.net -> avature",
      detect_platform("https://acme.avature.net/careers") == "avature")
check("avature.net.evil.example is neither",
      detect_platform("https://avature.net.evil.example/x") not in ("ibm", "avature"))
check("greenhouse still routes (regression)",
      detect_platform("https://job-boards.greenhouse.io/acme/jobs/123") == "greenhouse")
check("empty url -> unknown", detect_platform("") == "unknown")
check("get_filler('ibm') returns a callable", callable(get_filler("ibm")))
check("get_filler('avature') is None (not supported yet)", get_filler("avature") is None)

print("\n-- label normalization (the substring trap) --")
check("norm collapses whitespace and case", norm("  United   States \n") == "united states")
check("'United States' != 'United States Minor Outlying Islands'",
      norm("United States") != norm("United States Minor Outlying Islands"),
      "exact equality is what stops the arrow-key failure this module exists to prevent")
check("placeholder labels detected", is_placeholder_label("Select..."))
check("'-' is a placeholder", is_placeholder_label(" - "))
check("a real label is not a placeholder", not is_placeholder_label("Georgia State University"))

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
