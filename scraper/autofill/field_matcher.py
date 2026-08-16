"""Matches a form field's label text to a known application_profile.yaml
field.

Fast path: regex/keyword matching against the common, predictable labels
every ATS uses (name, email, phone, school, etc.) — no API call needed.
Fallback: a Claude Haiku structured tool-call for labels that don't match
anything obvious (reusing the same tool-call pattern scraper/classifier.py
already uses).

Hard rule, everywhere in this module: the job is routing only — "does this
label correspond to one of the known profile fields, and which one" — never
authoring a plausible-sounding answer to a question the profile has no data
for. Anything that doesn't match a real stored value returns None and the
caller flags it for the human, full stop.
"""

import logging
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import ANTHROPIC_API_KEY

import anthropic

log = logging.getLogger(__name__)

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _yes_no(raw, predicate=None):
    """Maps a stored boolean to "Yes"/"No", but maps ABSENT to None.

    `"Yes" if raw else "No"` cannot tell a stored False from a missing key, so a
    profile with no work_authorization section silently answered "No" to
    "do you require sponsorship" — a definite answer to a question the profile
    never addressed. match_field treats any non-empty value as stored data and
    types it in, so this was authoring, in the one module whose whole contract
    is that it never does that. Absent now returns None, which match_field
    reports as unmapped for the human.
    """
    if raw is None or raw == "":
        return None
    return "Yes" if (predicate(raw) if predicate else bool(raw)) else "No"


def _flatten_profile(profile: dict) -> dict:
    """Flat field-key -> value map for matching. Keys here are what both the
    keyword rules and the Claude fallback route to."""
    p = profile.get("personal", {})
    addr = p.get("address", {})
    edu = profile.get("education", {})
    auth = profile.get("work_authorization", {})
    log_ = profile.get("logistics", {})
    eeo = profile.get("eeo_demographics", {})

    return {
        "first_name": p.get("preferred_first_name") or p.get("legal_first_name"),
        "legal_first_name": p.get("legal_first_name"),
        "middle_name": p.get("middle_name"),
        "last_name": p.get("last_name"),
        "email": p.get("email"),
        "phone": p.get("phone"),
        "address_street": addr.get("street"),
        "address_city": addr.get("city"),
        "address_state": addr.get("state"),
        "address_zip": addr.get("zip"),
        "address_country": addr.get("country"),
        "resides_in_us": _yes_no(addr.get("country"), lambda c: c == "United States"),
        "linkedin_url": p.get("linkedin_url"),
        "github_url": p.get("github_url"),
        "portfolio_url": p.get("portfolio_url"),
        "school": edu.get("school"),
        "degree": edu.get("degree"),
        "major": edu.get("major"),
        "graduation_month": edu.get("graduation_month"),
        "graduation_year": edu.get("graduation_year"),
        "us_citizen": _yes_no(auth.get("us_citizen")),
        "requires_sponsorship": _yes_no(auth.get("requires_sponsorship")),
        "desired_salary": log_.get("desired_salary"),
        "available_start_date": log_.get("available_start_date"),
        "willing_to_relocate": _yes_no(log_.get("willing_to_relocate")),
        "gender": eeo.get("gender"),
        "race_ethnicity": eeo.get("race_ethnicity"),
        "veteran_status": eeo.get("veteran_status"),
        "disability_status": eeo.get("disability_status"),
    }


# Ordered most-specific first — e.g. "preferred first name" must be checked
# before the bare "first name" pattern.
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"preferred.*first.*name", re.I), "first_name"),
    (re.compile(r"legal.*first.*name", re.I), "legal_first_name"),
    (re.compile(r"\bfirst.*name\b", re.I), "first_name"),
    (re.compile(r"\bmiddle.*name\b", re.I), "middle_name"),
    (re.compile(r"\blast.*name\b|\bsurname\b|\bfamily.*name\b", re.I), "last_name"),
    (re.compile(r"\be-?mail\b", re.I), "email"),
    (re.compile(r"\bphone\b|\bmobile.*number\b|\btelephone\b", re.I), "phone"),
    (re.compile(r"\bstreet\b|\baddress.*line|\bhome.*address\b", re.I), "address_street"),
    # NB: the rule above matches "Address line 2" as well as "Address line 1".
    # _NEVER_MATCH (checked before this table) is what keeps the second line
    # from being filled with the street.
    (re.compile(r"\bcity\b", re.I), "address_city"),
    (re.compile(r"\bstate\b|\bprovince\b", re.I), "address_state"),
    (re.compile(r"\bzip\b|\bpostal.*code\b", re.I), "address_zip"),
    (re.compile(r"reside.*united states|reside.*u\.?s\.?\b|located.*united states", re.I), "resides_in_us"),
    (re.compile(r"\bcountry\b", re.I), "address_country"),
    (re.compile(r"linkedin", re.I), "linkedin_url"),
    (re.compile(r"github", re.I), "github_url"),
    (re.compile(r"portfolio|personal.*website|website\b", re.I), "portfolio_url"),
    (re.compile(r"\bschool\b|\buniversity\b|\bcollege\b", re.I), "school"),
    (re.compile(r"\bdegree\b", re.I), "degree"),
    (re.compile(r"major|field.*of.*study|discipline", re.I), "major"),
    (re.compile(r"graduation.*date|grad.*date|when.*graduat", re.I), "graduation_month"),
    (re.compile(r"sponsor(ship)?", re.I), "requires_sponsorship"),
    (re.compile(r"citizen|authorized.*work|work.*authorization", re.I), "us_citizen"),
    (re.compile(r"desired.*salary|salary.*expect|compensation.*expect", re.I), "desired_salary"),
    (re.compile(r"start.*date|available.*start|availability", re.I), "available_start_date"),
    (re.compile(r"relocat", re.I), "willing_to_relocate"),
    (re.compile(r"\bgender\b|\bsex\b", re.I), "gender"),
    (re.compile(r"race|ethnicity", re.I), "race_ethnicity"),
    (re.compile(r"veteran", re.I), "veteran_status"),
    (re.compile(r"disability", re.I), "disability_status"),
]

_FIELD_MATCH_TOOL = {
    "name": "match_field",
    "description": "Decide whether a job application form field's label corresponds to one of the known profile fields.",
    "input_schema": {
        "type": "object",
        "properties": {
            "matched_field": {
                "type": "string",
                "description": "The matching field key, or empty string if this label doesn't clearly correspond to any known field "
                               "(e.g. a free-text essay question, a company-specific screening question, anything requiring judgment). "
                               "Never guess — only match if genuinely confident.",
            },
        },
        "required": ["matched_field"],
    },
}


# Labels that must NEVER be routed, whatever the rules or the LLM think.
#
# These are fields the profile has no datum for, whose labels are near-misses
# for one it does. "Address line 2" is the case that bit: the street rule
# matches "address.*line", so an empty optional second line was filled with the
# street from line 1 — a duplicated address on a real application, and squarely
# the authoring this module promises never to do. The profile has no apartment
# or suite, so the honest answer is to leave it alone.
#
# Scoped tightly on purpose: `\bunit\b` alone would swallow "Business Unit",
# so a unit number must be spelled as one.
_NEVER_MATCH = re.compile(
    r"address\s*(?:line\s*)?(?:2|two|ii)\b"
    r"|\baddress\s*2\b"
    r"|\bapt\.?\b|\bapartment\b|\bsuite\b|\bste\.?\s*#"
    r"|\bunit\s*(?:#|no\.?|number)\b"
    r"|\bfloor\b|\bbuilding\s*(?:name|number|#)",
    re.I,
)


def match_field(label: str, profile: dict) -> Optional[tuple[str, str]]:
    """Returns (field_key, value) if the label maps to a known, non-empty
    profile field. Returns None if unmapped — caller must flag these for
    the human, never guess or leave silently blank."""
    flat = _flatten_profile(profile)
    label_clean = label.strip()
    if not label_clean:
        return None

    # Checked BEFORE the rules and before the LLM, so neither can route these.
    if _NEVER_MATCH.search(label_clean):
        return None

    for pattern, field_key in _RULES:
        if pattern.search(label_clean):
            value = flat.get(field_key)
            if value not in (None, ""):
                return field_key, str(value)
            return None  # matched a field we recognize but have no data for — flag, don't guess

    # Fallback: ask Claude to route (never author) for anything the rules missed
    try:
        known_fields = [k for k, v in flat.items() if v not in (None, "")]
        resp = _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            tools=[_FIELD_MATCH_TOOL],
            tool_choice={"type": "tool", "name": "match_field"},
            messages=[{
                "role": "user",
                "content": f"Form field label: \"{label_clean}\"\n\n"
                           f"Known profile fields with data: {', '.join(known_fields)}\n\n"
                           "Does this label clearly correspond to one of these fields? Only match if genuinely "
                           "confident — this is for auto-filling a real job application, not guessing.",
            }],
        )
        tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
        if tool_use:
            field_key = tool_use.input.get("matched_field", "")
            value = flat.get(field_key)
            if value not in (None, ""):
                return field_key, str(value)
    except Exception as exc:
        log.warning("Field matcher LLM fallback failed for label %r: %s", label_clean, exc)

    return None
