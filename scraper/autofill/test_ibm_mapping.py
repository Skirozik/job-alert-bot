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
    _EEO_OPTIONAL, _FIELD_MAP, _GENDER_OPTIONS, _eeo_has_value, _is_eeo_id,
    _is_repeat_row, _is_sample_id, _join_locations, _logical_fields, _lookup,
    _radio_option_id, _SOURCE_OPTIONS, validate_ibm_profile,
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
        },
        # Gender is required by IBM, so a "complete" profile carries one.
        # The others stay blank — blank means the tool leaves them alone.
        "eeo_demographics": {
            "gender": "Decline to identify",
            "race_ethnicity": "",
            "veteran_status": "",
            "disability_status": "",
        },
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

print("\n-- optional EEO fields are skipped; gender is NOT one of them --")
for fid in ["12705", "12706", "12707", "12708", "12709", "12710", "12711"]:
    check(f"{fid} is optional-EEO", _is_eeo_id(fid))
check("13602_gender is optional-EEO", _is_eeo_id("13602_gender"))
check("13602 is optional-EEO", _is_eeo_id("13602"))
check("12712 is NOT EEO", not _is_eeo_id("12712"))
check("1270 is NOT EEO", not _is_eeo_id("1270"))
check("10542-1 is NOT EEO", not _is_eeo_id("10542-1"))
check("127040 is NOT EEO", not _is_eeo_id("127040"), "prefix match must not over-reach")
check("12704 (gender) is NOT in the skip range", not _is_eeo_id("12704"),
      "IBM blocks Continue without gender — skipping it makes every step-60 advance fail")
check("12704 is a mapped field", "12704" in _FIELD_MAP)
check("gender routes from eeo_demographics.gender",
      _FIELD_MAP["12704"].key == "eeo_demographics.gender")
check("gender is marked required", _FIELD_MAP["12704"].required)

print("\n-- gender: routed, never authored --")
check("Male -> 92", _GENDER_OPTIONS["male"][0] == "92")
check("Female -> 93", _GENDER_OPTIONS["female"][0] == "93")
check("decline -> 94", _GENDER_OPTIONS["decline to identify"][0] == "94")
check("only the three documented option ids exist",
      {v[0] for v in _GENDER_OPTIONS.values()} == {"92", "93", "94"})
check("every gender option carries a label assertion",
      all(len(v) == 2 and v[1] for v in _GENDER_OPTIONS.values()),
      "a hardcoded option id with no label check is an unverifiable self-identification")

gp = _good_profile()
gp["eeo_demographics"] = {"gender": "", "race_ethnicity": "", "veteran_status": "",
                          "disability_status": ""}
rows_gender = [{"id": "12704_92", "name": "12704", "type": "radio", "tag": "input",
                "role": "", "value": "", "checked": False, "option_count": 0,
                "selected_text": "", "label": "Male", "required": False, "file_count": 0}]
rows_race = [{"id": "12706", "name": "12706", "type": "select-one", "tag": "select",
              "role": "combobox", "value": "", "checked": False, "option_count": 1,
              "selected_text": "", "label": "Race", "required": False, "file_count": 0}]
keys = {k for k, _, _ in _logical_fields(rows_gender + rows_race, gp)}
check("gender survives field collapsing with a blank profile", "12704" in keys,
      "it must still be offered, then reported when the profile has no value")
check("race is dropped when the profile is blank", "12706" not in keys)

gp["eeo_demographics"]["race_ethnicity"] = "Black"
keys = {k for k, _, _ in _logical_fields(rows_gender + rows_race, gp)}
check("race is kept once the profile carries a value", "12706" in keys)
check("_eeo_has_value returns the stored value",
      (_eeo_has_value(gp, "12706") or (None,) * 4)[3] == "Black")
check("_eeo_has_value is None for a blank", _eeo_has_value(gp, "12710") is None)
check("bare 'Black' is what IBM labels the option — nothing assumes the long form",
      "Black or African American" not in str(_EEO_OPTIONAL))

print("\n-- radio option ids --")
check("Yes -> _37", _radio_option_id("10480", True) == "10480_37")
check("No  -> _38", _radio_option_id("10480", False) == "10480_38")

print("\n-- _FIELD_MAP integrity --")
p = _good_profile()
bad_keys = [k for k, f in _FIELD_MAP.items()
            if f.key and _lookup(p, f.key) in (None, "")]
check("every mapped key resolves in a complete profile", not bad_keys, str(bad_keys))

KINDS = {"yesno", "radio_label", "radio_map", "check", "select", "dynamic",
         "text", "textarea", "file"}
bad_kinds = [(k, f.kind) for k, f in _FIELD_MAP.items() if f.kind not in KINDS]
check("every kind is known", not bad_kinds, str(bad_kinds))

check("no mapped id is an optional-EEO id", not [k for k in _FIELD_MAP if _is_eeo_id(k)])
check("46593 (specialization) is mapped", "46593" in _FIELD_MAP)
check("46593 is a dynamic dropdown", _FIELD_MAP["46593"].kind == "dynamic")
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

print("\n-- termination: a filled field is never re-attempted in the same step --")
# The hang: fill_avature_dropdown reads back through all four sources and says
# "filled", while _is_answered uses only the trusted two and says "empty". The
# field is re-filled on every pass, filled_this_pass never reaches 0 so the
# early exit never fires, and _advance_step's retries multiply it. Each click
# scrolls its field into view — hence "stuck scrolling up and down".
import inspect as _inspect
from autofill.platforms import ibm as _ibm
_loop_src = _inspect.getsource(_ibm.fill_current_step)
check("the pass loop skips keys already filled this step",
      "if key in filled_keys" in _loop_src,
      "without this, fill/is-answered disagreement loops until the pass cap")
check("both fill sites record the key", _loop_src.count("filled_keys.add(key)") == 2,
      "a fill that is not recorded will be retried every pass")
check("failed fields are skipped too", "seen_unmapped" in _loop_src)
_adv_src = _inspect.getsource(__import__("autofill.autofill_ibm", fromlist=["x"])._advance_step)
check("the retry loop stops when a retry adds nothing new",
      "filled_before" in _adv_src,
      "otherwise each retry repeats the same work")

print("\n-- no control characters in any source (the \\b -> 0x08 trap) --")
# This repo has hit this four times now: db.py's norm_role, classifier.py's
# regexes, jobView.ts's splitLocations, and ibm.py's "^your name\b". Writing
# regex through a generator script puts "\b" inside a NON-raw Python string,
# where it becomes a literal 0x08 BACKSPACE before the file is ever written.
# The pattern then silently matches nothing, which looks exactly like a field
# the form did not render.
import pathlib as _pl
_ctrl = {b"\x08": r"\b backspace", b"\x0c": r"\f formfeed",
         b"\x07": r"\a bell", b"\x0b": r"\v vertical tab"}
_dirty = []
for _f in sorted(_pl.Path(__file__).parent.rglob("*.py")):
    _raw = _f.read_bytes()
    for _c, _name in _ctrl.items():
        if _c in _raw:
            _dirty.append(f"{_f.name}:{_raw[:_raw.index(_c)].count(chr(10).encode()) + 1} {_name}")
check("no stray control characters in autofill sources", not _dirty,
      "; ".join(_dirty) or "")

print("\n-- placeholder labels: a dropdown showing its placeholder is EMPTY --")
# Avature renders "Select an option". The old fixed set had "select" and
# "select..." but not that, so every dynamic dropdown read as already-answered,
# was skipped without appearing in either report list, and the form rejected
# the step for four required fields the tool silently declined to fill.
for text in ["Select an option", "Select...", "Select", "-- Select --",
             "Please select a value", "Please select an option", "Choose an option",
             "Choose...", "", "  ", "-", "--", "None", "N/A", "n/a"]:
    check(f"placeholder: {text!r}", is_placeholder_label(text))
for text in ["United States", "Georgia State University", "Computer Science",
             "Bachelor's Degree", "Job Board", "Male", "Black", "March",
             "Yes", "No", "Selected Employer", "Choose Financial Group"]:
    check(f"real value: {text!r}", not is_placeholder_label(text),
          "a real option must never read as a placeholder")

print("\n-- numeric ids need attribute selectors, not '#' --")
from autofill.platforms.ibm import _loc


class _SelPage:
    def __init__(self): self.seen = []
    def locator(self, sel): self.seen.append(sel); return sel


sp = _SelPage()
_loc(sp, "10480"); _loc(sp, "12704_93"); _loc(sp, "35979_month")
check("every selector is an [id=...] attribute selector",
      all(s.startswith('[id="') for s in sp.seen), str(sp.seen))
check("no '#' selectors are generated", not any(s.startswith("#") for s in sp.seen),
      "CSS identifiers cannot start with a digit; '#10480' raises SyntaxError")
import pathlib
_ibm_src = pathlib.Path(__file__).with_name("platforms").joinpath("ibm.py").read_text(encoding="utf-8")
check("no 'locator(f\"#' remains in ibm.py", 'locator(f"#' not in _ibm_src)

print("\n-- self-identification is routed, never authored --")
from autofill.platforms.ibm import _DISABILITY_OPTIONS, _match_option, _eeo_spec, _fill_one


class _Radio:
    def __init__(self, ok=True): self.ok, self.checked = ok, False
    def count(self): return 1
    def check(self): self.checked = True
    def is_checked(self): return self.checked


class _RadioPage:
    def __init__(self): self.clicked = None
    def locator(self, sel):
        self.clicked = sel
        return _Radio()


def _rows(group, opts):
    return [{"id": f"{group}_{o}", "name": group, "type": "radio", "tag": "input",
             "role": "", "value": "", "checked": False, "option_count": 0,
             "selected_text": "", "label": lab, "required": False, "file_count": 0}
            for o, lab in opts]


DIS_ROWS = _rows("12709", [("126", "Yes, I have a disability"),
                           ("127", "No, I do not have a disability"),
                           ("128", "I do not wish to answer")])

check("12709 is a radio_map, not a radio_label",
      _EEO_OPTIONAL["12709"][1] == "radio_map",
      "radio_label with option_label='.' clicked the FIRST option regardless of value")

pg = _RadioPage()
ok, note = _fill_one(pg, "12709", _eeo_spec("12709"), "No, I do not have a disability", DIS_ROWS)
check("a stored 'No' clicks the NO option", ok and pg.clicked == '[id="12709_127"]',
      f"clicked {pg.clicked}")
pg = _RadioPage()
ok, _ = _fill_one(pg, "12709", _eeo_spec("12709"), "Yes", DIS_ROWS)
check("a stored 'Yes' clicks the YES option", ok and pg.clicked == '[id="12709_127"]' or pg.clicked == '[id="12709_126"]')
check("...specifically 126", pg.clicked == '[id="12709_126"]', f"clicked {pg.clicked}")
pg = _RadioPage()
ok, note = _fill_one(pg, "12709", _eeo_spec("12709"), "banana", DIS_ROWS)
check("an unrecognized value is refused, not guessed", not ok and pg.clicked is None, note)

# The label assertion: ids swapped relative to what was measured.
SWAPPED = _rows("12709", [("126", "No, I do not have a disability"),
                          ("127", "Yes, I have a disability"),
                          ("128", "I do not wish to answer")])
pg = _RadioPage()
ok, note = _fill_one(pg, "12709", _eeo_spec("12709"), "No", SWAPPED)
check("a label that contradicts the option id refuses to click",
      not ok and pg.clicked is None,
      "this is the guard against the portal's ids differing from what was measured")

print("\n-- gender carries the same label assertion --")
GEN_ROWS = _rows("12704", [("92", "Male"), ("93", "Female"), ("94", "Decline to identify")])
pg = _RadioPage()
ok, _ = _fill_one(pg, "12704", _FIELD_MAP["12704"], "Female", GEN_ROWS)
check("Female clicks 93", ok and pg.clicked == '[id="12704_93"]', f"clicked {pg.clicked}")
GEN_SWAPPED = _rows("12704", [("92", "Female"), ("93", "Male"), ("94", "Decline")])
pg = _RadioPage()
ok, note = _fill_one(pg, "12704", _FIELD_MAP["12704"], "Female", GEN_SWAPPED)
check("swapped gender ids are caught before clicking", not ok and pg.clicked is None, note)
check("'male' pattern does not match 'female'",
      _match_option(_GENDER_OPTIONS, "male")[0] == "92"
      and _match_option(_GENDER_OPTIONS, "female")[0] == "93")
check("a verbose stored value still resolves by prefix",
      _match_option(_DISABILITY_OPTIONS,
                    "No, I do not have a disability and have not had one")[0] == "127")

print("\n-- radio_label refuses to default to the first option --")
pg = _RadioPage()
bad = _FIELD_MAP["20527"]._replace(option_label=None)
ok, note = _fill_one(pg, "20527", bad, True, _rows("20527", [("1", "I agree"), ("2", "I disagree")]))
check("a radio_label with no option_label refuses rather than clicking first",
      not ok and pg.clicked is None, note)

print("\n-- field_matcher never invents an answer from a missing key --")
from autofill.field_matcher import _flatten_profile
flat = _flatten_profile({"personal": {}, "education": {}})
for k in ("us_citizen", "requires_sponsorship", "willing_to_relocate", "resides_in_us"):
    check(f"absent {k} -> None, not a confident 'No'", flat[k] is None,
          f"got {flat[k]!r}; match_field would have typed that into a real form")
flat = _flatten_profile({"work_authorization": {"us_citizen": True, "requires_sponsorship": False},
                         "logistics": {"willing_to_relocate": False},
                         "personal": {"address": {"country": "United States"}}})
check("a stored True still yields Yes", flat["us_citizen"] == "Yes")
check("a stored False still yields No", flat["requires_sponsorship"] == "No")
check("a stored False relocate yields No", flat["willing_to_relocate"] == "No")
check("resides_in_us still computed when country is present", flat["resides_in_us"] == "Yes")

print("\n-- an optional near-miss field is left alone, not filled with a neighbour --")
# Observed live on IBM step 40%: "Address line 2" was empty and optional, and
# the street rule (\baddress.*line) matched it, so the tool typed line 1's
# street into it. A duplicated address on a real application, and exactly the
# authoring field_matcher promises never to do.
from autofill.field_matcher import match_field as _mf
_addr = {"personal": {"address": {"street": "1177 Sells Ave SW", "city": "Atlanta",
                                  "state": "GA", "zip": "30310", "country": "United States"}}}
for label in ["Address line 1", "Address Line 1 *", "Street Address"]:
    check(f"{label!r} still fills", _mf(label, _addr) is not None)
for label in ["Address line 2", "Address Line 2", "Address line 2 (optional)",
              "Address 2", "Apt", "Apartment", "Suite", "Unit Number", "Floor"]:
    check(f"{label!r} is left alone", _mf(label, _addr) is None,
          "the profile has no apartment/suite, so the honest answer is to not fill it")
for label, key in [("City", "address_city"), ("State/Province", "address_state"),
                   ("Country", "address_country"), ("Zip", "address_zip")]:
    got = _mf(label, _addr)
    check(f"{label!r} still routes to {key}", got is not None and got[0] == key)

print("\n-- long option labels match on their leading sentence, unambiguously --")
from autofill.widgets import _option_variants
_IBM_SKILL_OPTIONS = [
    "No Experience.",
    "Limited Experience. I need additional direction and/or support to demonstrate this skill.",
    "Demonstrated Experience. I am proficient in performing this skill across routine or predictable situations with little direction / support.",
    "Extensive Experience. I am proficient in performing this skill across a variety of situations & settings. I need help with this skill only in unusually complex situations.",
    "Expert. I have mastered this skill. I could instruct and advise others - colleagues and supervisors could consult my expertise in complex situations.",
]


def _hits(want):
    return [o for o in _IBM_SKILL_OPTIONS if norm(want) in _option_variants(o)]


for want in ["No Experience", "Limited Experience", "Demonstrated Experience",
             "Extensive Experience", "Expert", "expert", "EXTENSIVE EXPERIENCE"]:
    check(f"{want!r} resolves to exactly one option", len(_hits(want)) == 1,
          f"matched {len(_hits(want))}")
check("'Expert' does not also match 'Extensive Experience'",
      _hits("Expert") == [_IBM_SKILL_OPTIONS[4]])
check("a bare 'Experience' matches nothing", len(_hits("Experience")) == 0,
      "an ambiguous stem must not silently pick one")
check("the substring trap still holds",
      norm("United States") not in _option_variants("United States Minor Outlying Islands"),
      "leading-sentence variants must not degrade into prefix matching")

print("\n-- self-assessment questions are never answered from the profile --")
# Live: "What best describes your level of experience in File versioning
# software (e.g., Git and GitHub)?" contains "GitHub", so the github_url rule
# fired and a URL was typed into a proficiency dropdown. The profile stores no
# skill ratings, so inventing one is the purest form of authoring.
_skills = {"personal": {"github_url": "https://github.com/Skirozik",
                        "linkedin_url": "https://linkedin.com/in/x", "email": "a@b.c"}}
for label in [
    "What best describes your level of experience in File versioning software (e.g., Git and GitHub)?",
    "What best describes your level of experience in Programming and software development?",
    "What best describes your level of experience in Database management system software (e.g., Hadoop, MongoDB, SQL, etc.)?",
    "Years of experience with Python",
    "Rate your proficiency in Java",
]:
    check(f"{label[:44]!r}... left alone", _mf(label, _skills) is None)
for label, key in [("GitHub URL", "github_url"), ("LinkedIn URL", "linkedin_url"),
                   ("Email", "email")]:
    got = _mf(label, _skills)
    check(f"{label!r} still routes", got is not None and got[0] == key,
          "the guard must not swallow the real profile fields")

print("\n-- dynamic EEO fields read as answered (no re-fill every pass) --")
check("_eeo_spec gives 12706 a dynamic kind", _eeo_spec("12706").kind == "dynamic",
      "without this _is_answered falls through to the plain-select branch")
check("_eeo_spec gives 12710 a dynamic kind", _eeo_spec("12710").kind == "dynamic")
check("_eeo_spec is None for a non-EEO id", _eeo_spec("10480") is None)

print("\n-- target resolution: jobId or --url --")
from autofill.autofill_ibm import _resolve_target
url, label = _resolve_target("127789", None)
check("a jobId builds the JobApplication url", url.endswith("JobApplication?jobId=127789"))
check("...and labels it", label == "ibm-127789")
url, label = _resolve_target(None, "https://careers.ibm.com/en_US/careers/ApplicationMethods?jobId=129258")
check("--url is used verbatim", url.endswith("ApplicationMethods?jobId=129258"),
      "a constructed url cannot reach ApplicationMethods or session-specific paths")
check("--url still recovers the jobId for labelling", label == "ibm-129258")
check("--url with no jobId still works", _resolve_target(None, "https://careers.ibm.com/x")[0] is not None)
check("a non-http --url is rejected", _resolve_target(None, "javascript:alert(1)")[0] is None)
check("a bare word is rejected", _resolve_target("notanumber", None)[0] is None)
check("no argument at all is rejected", _resolve_target(None, None)[0] is None)

print("\n-- am I on the application form? --")
from autofill.autofill_ibm import _looks_like_application_form


class _UrlOnlyPage:
    """No DOM. Forces the URL check to stand on its own — which it must, since
    step 1 (Talent Network) carries none of _FIELD_MAP's ids and a content-only
    test wrongly reported 'you are not logged in' with the form on screen."""
    def __init__(self, url): self.url = url
    def evaluate(self, *a, **k): raise Exception("no DOM available")


for label, url, expect in [
    ("JobApplication step 1", "https://careers.ibm.com/en_US/careers/JobApplication?jobId=129227", True),
    ("ApplicationMethods",    "https://careers.ibm.com/en_US/careers/ApplicationMethods?jobId=129227", True),
    ("JobApplicationSummary", "https://careers.ibm.com/en_US/careers/JobApplicationSummary?jobId=1", True),
    ("login redirect",        "https://login.ibm.com/authsvc/mtfim/sps/authsvc", False),
    ("JobDetail (not the app)", "https://careers.ibm.com/en_US/careers/JobDetail?jobId=129227", False),
]:
    check(f"{label} -> {expect}", _looks_like_application_form(_UrlOnlyPage(url)) is expect,
          "an unauthenticated request is REDIRECTED off the application path, so "
          "staying on it is the proof — no DOM content required")

print("\n-- terminal page detected by URL, not button text --")
from autofill.autofill_ibm import _is_terminal_page


class _FakePage:
    def __init__(self, url):
        self.url = url


check("JobApplicationSummary is terminal",
      _is_terminal_page(_FakePage("https://careers.ibm.com/en_US/careers/JobApplicationSummary?jobId=1")))
check("JobApplication is NOT terminal",
      not _is_terminal_page(_FakePage("https://careers.ibm.com/en_US/careers/JobApplication?jobId=1")))
check("case is ignored",
      _is_terminal_page(_FakePage("https://careers.ibm.com/EN_US/CAREERS/jobapplicationsummary?jobId=1")))

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
