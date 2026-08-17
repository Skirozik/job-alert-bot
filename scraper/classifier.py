"""Claude Haiku fit classifier.

Reads Candidate_Profile_and_Filters.md at startup and uses it as the rubric
for every classification call. Returns tier (APPLY / APPLY_CAVEAT /
INELIGIBLE), a one-line reason, and which resume variant to use.

The candidate profile + instructions are sent as a cached system prompt
(prompt caching), since they're identical on every call in a run — this cuts
input token cost substantially on the 2nd+ call. The response is forced
through a tool call with a JSON schema instead of asking for free-text JSON,
so there's no markdown-fence stripping or JSONDecodeError fallback path.

WHEN THE API IS UNAVAILABLE this returns _failed(), and the caller parks the
job as tier="PENDING" rather than storing a verdict or dropping the row —
see the comment above MAX_CLASSIFY_ATTEMPTS. A billing or auth failure also
trips a per-process breaker so the rest of the run costs zero API calls
instead of three attempts and two backoffs per job.
"""

import logging
import random
import re
import time
from typing import Optional

import anthropic
from config import ANTHROPIC_API_KEY, CANDIDATE_PROFILE_PATH
from salary_extraction import extract_salary

log = logging.getLogger(__name__)

# Deterministic backstop: a stack-heavy description can pull the model
# toward APPLY hard enough that it reasons right past an explicit statement
# like "the base salary range for this full-time position is..." — seen in
# practice even with an explicit rubric instruction to check internship
# status first. Regex can't judge nuance, but it can catch exact phrases
# perfectly, so use it as a hard override rather than relying on the model
# to always prioritize one sentence correctly under attention pressure from
# a long, well-matched job description.
_FULL_TIME_PHRASE_RE = re.compile(
    r"\bfull[\s-]time\s+(position|role|employee|employment|hire)\b|\bpermanent\s+(position|role|employee)\b",
    re.IGNORECASE,
)
_INTERNSHIP_WORD_RE = re.compile(r"\bintern(ship)?s?\b|\bco[\s-]?op\b", re.IGNORECASE)

# Deterministic backstop: some co-op postings are restricted to students
# currently enrolled at one specific partner university (e.g. "Comcast's
# Drexel Co-op Program"). The model has been observed to score these purely
# on stack/company fit and never mention the school restriction at all, even
# though it's the single dispositive fact for a candidate who doesn't attend
# that school. [A-Z] (not re.IGNORECASE) is deliberate here — it's what
# makes this safe: it's the signal "this is a real proper noun," not a
# generic phrase like "an accredited college or university."
CANDIDATE_SCHOOL = "Georgia State University"

_SCHOOL_NAME_RE = (
    r"(?:[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\s+(?:University|College)"
    r"|(?:University|College)\s+of\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})"
)

# A looser school name, for use ONLY where the surrounding text already proves
# a school is being named — "<School> Co-op:" or "Co-Op | <School>". It accepts
# forms _SCHOOL_NAME_RE cannot: "Georgia Tech", "MIT", "Georgia Institute of
# Technology". Too permissive to use on free description text, which is why it
# is confined to those two anchored patterns.
_SCHOOL_NAME_LOOSE_RE = (
    r"(?:[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\s+"
    r"(?:University|College|Tech|Institute(?:\s+of\s+Technology)?)"
    r"|(?:University|College)\s+of\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}"
    r"|[A-Z]{2,5}(?=\s*$|\s*[|,–—-]))"
)

# The co-op token, case-insensitive on BOTH halves.
#
# The old [Cc]o[\s-]?op was case sensitive on "op", so it did not match "Co-Op"
# — and every Analytic Partners posting spells it exactly that way. Five
# school-restricted co-ops sat at tier=APPLY purely because of the capital O.
_COOP_TOKEN = r"[Cc]o[\s-]?[Oo]p"

# "Comcast's Drexel Co-op Program" — anchored on the possessive apostrophe so
# it never mis-captures a preceding capitalized phrase that has no possessive
# (e.g. "...Overview The Susquehanna Co-op Program..." has no "'s" at all).
_SCHOOL_COOP_PROGRAM_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z&.,-]*)['’]s\s+((?:[A-Z][A-Za-z]+\s+)??[A-Z][A-Za-z]+)\s+"
    + _COOP_TOKEN + r"\s+[Pp]rogram\b"
)
# "Technology Co-op with Drexel University" / "...AI Co-op with Drexel University in Bala Cynwyd..."
_SCHOOL_COOP_WITH_RE = re.compile(
    r"\b" + _COOP_TOKEN + r"\s+(?:[Pp]rogram\s+)?with\s+(" + _SCHOOL_NAME_RE + r")\b"
)
# "Drexel University Co-op: Software Engineering/Full stack development" —
# school first, role after. Seen live on SRI and AVEVA postings.
_SCHOOL_COOP_PREFIX_RE = re.compile(
    r"\b(" + _SCHOOL_NAME_LOOSE_RE + r")\s+" + _COOP_TOKEN + r"\s*[:\-–—]"
)
# "Software Engineer Co-Op | Northeastern University" — delimiter separated.
# Seen live on five Analytic Partners postings.
_SCHOOL_COOP_DELIM_RE = re.compile(
    r"\b" + _COOP_TOKEN + r"\s*[|/–—]\s*(" + _SCHOOL_NAME_LOOSE_RE + r")"
)
# "Drexel Co-op US" / "Intern- Drexel Co-op" — a school named WITHOUT the word
# University or College after it.
#
# Restricted to a known list rather than "any capitalised word before co-op".
# The permissive version matched "IBM co-op program" in an IBM posting's own
# description and ruled the candidate ineligible for a job they had already
# applied to — a false positive that silently removes real jobs, which is worse
# than the leak this override exists to close. Any company running a co-op
# programme would have tripped it.
#
# These are the schools whose same-school-only co-op programmes actually appear
# in this pipeline. Add to the list when a new one shows up; do not loosen the
# pattern.
_COOP_SCHOOL_WORDS = (
    "Drexel|Northeastern|Waterloo|Kettering|Purdue|Cincinnati|Wentworth"
    "|RIT|Rochester Institute|Georgia Tech|Virginia Tech|Cal Poly"
)
_SCHOOL_BARE_COOP_RE = re.compile(
    r"\b(" + _COOP_SCHOOL_WORDS + r")\s+" + _COOP_TOKEN + r"\b"
)
# "Currently pursuing a bachelor's degree from Drexel University, with a..."
_SCHOOL_ELIGIBILITY_RE = re.compile(
    r"\b[Pp]ursuing\s+(?:a|an|your)?\s*(?:[\w'’]+\s+)??degree\b"
    r"(?:\s+in\s+[A-Za-z][A-Za-z\s]{0,40}?)?\s+from\s+(" + _SCHOOL_NAME_RE + r")\b"
)

_SCHOOL_SUFFIX_RE = re.compile(r"\b(?:university|college)\b", re.IGNORECASE)


def _normalize_school(name: str) -> str:
    """'Drexel University' -> 'drexel'; 'Georgia State University' -> 'georgia
    state' — strips the generic University/College word (and stray 'of') so
    comparison is exact-but-form-insensitive, not brittle full-string or
    over-eager substring matching."""
    core = _SCHOOL_SUFFIX_RE.sub("", name)
    core = re.sub(r"\bof\b", "", core)
    core = re.sub(r"[^\w\s]", " ", core)
    return " ".join(core.lower().split())


_CANDIDATE_SCHOOL_CORE = _normalize_school(CANDIDATE_SCHOOL)

# Deterministic backstop: the model has been observed to write a reason that
# already correctly concludes a role requires grad-only enrollment
# ("candidate is a rising senior in a BS program, ineligible") and then
# return tier=MAYBE anyway — a reasoning/tier inconsistency, not a missing
# rubric instruction (Candidate_Profile_and_Filters.md already lists
# "advanced degree explicitly required" as a hard SKIP). Verified against
# every stored description before shipping: only fires when no
# bachelor's/undergraduate alternative is mentioned anywhere in the posting,
# so postings phrased as "Bachelor's or Master's" (the common case, and
# genuinely open to a BS candidate) never trigger this.
_GRAD_DEGREE_KEYWORD = r"(?:MS|M\.S\.|master'?s|Ph\.?D\.?|doctoral|graduate)"
_GRAD_ONLY_PHRASE_RE = re.compile(
    r"\b(?:pursuing|enrolled in|completing)\s+(?:an?\s+)?" + _GRAD_DEGREE_KEYWORD
    + r"(?:\s*(?:,|or|/)\s*" + _GRAD_DEGREE_KEYWORD + r"){0,2}\s+(?:degree|program)\b",
    re.IGNORECASE,
)
_UNDERGRAD_WORD_RE = re.compile(r"\bbachelor|undergraduate|\bB\.?S\.?\b", re.IGNORECASE)

_client: Optional[anthropic.Anthropic] = None
_profile: Optional[str] = None

MODEL = "claude-haiku-4-5-20251001"

# NEVER store a verdict the model did not produce.
#
# Confirmed the hard way on 2026-08-04, when an API outage stored 82 jobs as
# "MAYBE / Classifier error — review manually". 32 of them were actually APPLY,
# including four TikTok 2027 internships, Boeing and RTX, and they stayed
# buried indefinitely: dedup means a stored row is never looked at again.
#
# The original fix was to store nothing at all and let the next run rediscover
# the listing. That closed the fake-verdict hole but opened another: a LinkedIn
# job is only rediscoverable while it is inside LOOKBACK_SECONDS (6h) AND still
# ranked in the first ten pages, so an outage longer than a few hours lost those
# jobs permanently — seen, described, and then evaporated.
#
# So a failed job is now PARKED as tier="PENDING" (main.process_job) and retried
# automatically on later runs (main.retry_pending). PENDING is a QUEUE STATE,
# not a verdict: it is impossible to confuse with a classification, it never
# reaches the dashboard's actionable views, and it is guaranteed to get a real
# classification later. The invariant above is untouched.
MAX_CLASSIFY_ATTEMPTS = 3

# Tripped by a billing or auth failure, which no amount of retrying can fix.
# Once set, classify() returns immediately without touching the API — so an
# outage costs one failed call per run instead of 3 attempts x exponential
# backoff PER JOB, which on a full scrape is the difference between a run that
# finishes and one that hits the 20-minute Actions timeout.
#
# Per-process by design. Every scheduled run is a fresh process, so it cannot
# stick past a run and needs no reset logic.
_API_HARD_DOWN: Optional[str] = None


def _error_kind(exc: Exception) -> str:
    """Classify an SDK exception into what it means for retrying.

    Pure and exception-instance-only so it is testable without a network or a
    real key. Verified against anthropic 0.105.2.

      billing   — the account is out of credit. Anthropic returns HTTP 400 with
                  "Your credit balance is too low..." rather than a dedicated
                  exception class, so the message substring is the only signal
                  available. Matched case-insensitively, and deliberately not
                  pinned to the full sentence.
      auth      — bad or revoked key, or a key without access to the model.
      transient — rate limits, overloads, connection resets, 5xx. Worth retrying.
    """
    if isinstance(exc, anthropic.BadRequestError):
        message = str(getattr(exc, "message", "") or exc).lower()
        if "credit balance" in message or "insufficient credit" in message:
            return "billing"
        return "transient"
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return "auth"
    return "transient"


def _backoff_seconds(attempt: int) -> float:
    return 2.0 * (2 ** attempt) + random.uniform(0, 1.5)


def _failed(detail: str, kind: str = "transient") -> dict:
    """Signals 'this job has no verdict'. Callers must check result["failed"]
    BEFORE reading tier — the tier here is a placeholder, not a judgment.

    The caller parks the job as PENDING rather than dropping it. `failed_kind`
    tells it which: billing/auth mean the outage is account-wide and worth a
    canary alert, transient/malformed mean this one job hiccuped and will
    quietly retry.
    """
    return {
        "failed": True,
        "failed_kind": kind,
        "tier": "APPLY_CAVEAT",
        "reason": f"Classifier error — parked for retry ({detail[:120]})",
        "suggested_resume": "General",
    }
MAX_TOKENS = 400

_VALID_TIERS = ("APPLY", "APPLY_CAVEAT", "INELIGIBLE")

_CLASSIFY_TOOL = {
    "name": "classify_job",
    "description": "Record the classification of an internship job posting against the candidate profile.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tier": {
                "type": "string",
                "enum": list(_VALID_TIERS),
                "description": (
                    "Fit tier. APPLY = clean fit, no caveat worth mentioning. APPLY_CAVEAT = worth applying, but with exactly one specific reservation the candidate should know about before spending time on it; the reason field must state that caveat in under 12 words. INELIGIBLE = a HARD block only, meaning the candidate literally cannot be hired: graduation date outside the posting's stated window, a security clearance he does not already hold, MS/PhD required, 2+ years professional experience as a hard requirement, not actually an internship (new grad or full-time), a school-specific program he is not eligible for, unpaid full-time, or work authorization he lacks (he is a US citizen, so sponsorship is never a blocker). Anything that is a judgment call rather than a hard rule is APPLY_CAVEAT, never INELIGIBLE."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "For APPLY: one short sentence on the match. For APPLY_CAVEAT: the caveat itself, under 12 words, naming the specific reservation (e.g. 'strong C++ + Unreal, no game projects' or 'prefers 3.5 GPA'). For INELIGIBLE: one sentence naming which hard block applies."
                ),
            },
            "suggested_resume": {
                "type": "string",
                "enum": ["Mobile", "AI", "Frontend", "General"],
                "description": "Which of the candidate's 4 resume variants best fits this "
                               "specific role, based on the actual responsibilities and stack "
                               "described in the posting — not just title keywords.",
            },
            "salary": {
                "type": "string",
                "description": "Salary if mentioned in the description, e.g. '$20-30/hr' or "
                               "'$85,000-$110,000/yr'. Empty string if not mentioned.",
            },
        },
        "required": ["tier", "reason", "suggested_resume"],
    },
}


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY must be set")
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _get_profile() -> str:
    global _profile
    if _profile is None:
        with open(CANDIDATE_PROFILE_PATH, "r", encoding="utf-8") as f:
            _profile = f.read()
    return _profile


def _system_prompt() -> list[dict]:
    text = f"""You evaluate internship job postings for a specific candidate.
Use the classify_job tool to record your evaluation.

CANDIDATE PROFILE AND FILTERS:
{_get_profile()}"""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def classify(job: dict) -> dict:
    """Classify a job posting against the candidate profile.

    Returns dict with keys: tier, reason, suggested_resume, salary.
    Falls back to MAYBE + manual review note on any error.
    """
    user_prompt = f"""JOB POSTING:
Title: {job.get("title", "")}
Company: {job.get("company", "")}
Location: {job.get("location", "")}
Description: {job.get("description") or "(not available — classify on title/company/location only)"}"""

    global _API_HARD_DOWN

    # Breaker: a billing or auth failure earlier in this run means every
    # further call would fail identically. Return without touching the API.
    if _API_HARD_DOWN is not None:
        return _failed(f"API unavailable ({_API_HARD_DOWN}) — skipped without calling",
                       _API_HARD_DOWN)

    last_exc = None
    for attempt in range(MAX_CLASSIFY_ATTEMPTS):
        try:
            resp = _get_client().messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                # Pinned. Classification is a judgment call that must be
                # reproducible: at the API default, identical input produced
                # different verdicts ~20-30% of the time on borderline jobs.
                # Two Oracle postings with byte-identical 9,505-char
                # descriptions once classified SKIP and APPLY in the same pass.
                temperature=0,
                system=_system_prompt(),
                tools=[_CLASSIFY_TOOL],
                tool_choice={"type": "tool", "name": "classify_job"},
                messages=[{"role": "user", "content": user_prompt}],
            )

            tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
            if tool_use is None:
                # A malformed response is not retryable in any useful way, but
                # it IS a failure — don't invent a verdict for it.
                log.error("Classifier returned no tool_use block for job %s", job.get("id"))
                return _failed("no tool_use block in response", "malformed")

            result = dict(tool_use.input)

            if result.get("tier") not in _VALID_TIERS:
                log.warning("Unexpected tier '%s' for job %s — defaulting to APPLY_CAVEAT", result.get("tier"), job.get("id"))
                result["tier"] = "APPLY_CAVEAT"

            result = _apply_full_time_override(job, result)
            result = _apply_school_specific_override(job, result)
            result = _apply_advanced_degree_override(job, result)
            result = _apply_non_us_override(job, result)
            result = _apply_salary_fallback(job, result)
            result = _never_skip_github_sourced(job, result)

            return result

        except Exception as exc:
            last_exc = exc
            kind = _error_kind(exc)

            # Billing and auth are account-wide and permanent for this run.
            # Retrying them is pure wasted wall-clock, and on a full scrape
            # that waste is what pushes a run past the Actions timeout.
            if kind in ("billing", "auth"):
                if _API_HARD_DOWN is None:
                    log.error("Classifier is hard down (%s) — parking the rest of this run "
                              "without further API calls: %s", kind, exc)
                    _API_HARD_DOWN = kind
                return _failed(str(exc), kind)

            if attempt < MAX_CLASSIFY_ATTEMPTS - 1:
                backoff = _backoff_seconds(attempt)
                log.warning("Classifier attempt %d/%d failed for job %s (%s) — retrying in %.1fs",
                            attempt + 1, MAX_CLASSIFY_ATTEMPTS, job.get("id"), exc, backoff)
                time.sleep(backoff)
            else:
                log.error("Classifier failed for job %s after %d attempts: %s",
                          job.get("id"), MAX_CLASSIFY_ATTEMPTS, exc)

    return _failed(str(last_exc), "transient")


def _apply_full_time_override(job: dict, result: dict) -> dict:
    """Force SKIP if the description explicitly says "full-time position"
    (etc.) and never mentions internship/co-op anywhere — regardless of how
    the model scored stack fit. Never fires on postings that also mention
    internship/co-op (e.g. "may convert to full-time after graduation" is a
    normal, desirable internship perk, not a full-time posting)."""
    if result.get("tier") not in ("APPLY", "APPLY_CAVEAT"):
        return result

    desc = job.get("description") or ""
    if _FULL_TIME_PHRASE_RE.search(desc) and not _INTERNSHIP_WORD_RE.search(desc):
        log.info("  Full-time override: job %s described as full-time with no internship language",
                  job.get("id"))
        result["tier"] = "INELIGIBLE"
        result["reason"] = (
            "Overridden: description explicitly states this is a full-time/permanent "
            "position, with no internship/co-op language anywhere in the posting."
        )

    return result


def _apply_school_specific_override(job: dict, result: dict) -> dict:
    """Force SKIP if the description names a specific partner university as a
    hard enrollment requirement (a same-school-only co-op, e.g. "Comcast's
    Drexel Co-op Program") and that school isn't the candidate's own (Georgia
    State University). The model has been observed to never mention this
    restriction in its own reasoning even though it's the single dispositive
    fact, so this is a deterministic catch rather than a rubric instruction.
    Runs before _never_skip_github_sourced so a gh:-sourced posting still
    gets the same never-auto-SKIP protection every other override respects."""
    if result.get("tier") not in ("APPLY", "APPLY_CAVEAT"):
        return result

    # TITLE FIRST, then the description. Candidate_Profile_and_Filters.md says
    # outright that "the restriction is frequently in the TITLE rather than the
    # body", and it was right: Susquehanna's "Equity Options AI Co-op with
    # Drexel University" never mentions Drexel, University or College anywhere
    # in its 2,483-character description. Reading only the description left
    # those at tier=APPLY for a candidate categorically ineligible for them.
    #
    # Only THIS override reads the title. The full-time and advanced-degree
    # overrides deliberately do not — their phrases had zero title hits across
    # a thousand titles, and folding the title into the full-time one would
    # invert its behaviour via _INTERNSHIP_WORD_RE.
    haystack = f"{job.get('title') or ''}\n{job.get('description') or ''}"
    match = (
        _SCHOOL_COOP_PROGRAM_RE.search(haystack)
        or _SCHOOL_COOP_WITH_RE.search(haystack)
        or _SCHOOL_COOP_PREFIX_RE.search(haystack)
        or _SCHOOL_COOP_DELIM_RE.search(haystack)
        or _SCHOOL_ELIGIBILITY_RE.search(haystack)
        or _SCHOOL_BARE_COOP_RE.search(haystack)
    )
    if not match:
        return result

    school = match.group(1).strip()
    if _normalize_school(school) == _CANDIDATE_SCHOOL_CORE:
        return result  # candidate's own school named — not a mismatch

    log.info("  School-specific override: job %s restricted to %s (candidate attends %s)",
              job.get("id"), school, CANDIDATE_SCHOOL)
    result["tier"] = "INELIGIBLE"
    result["hard_ineligible"] = True
    result["reason"] = (
        f"Overridden: this co-op is restricted to students currently enrolled at "
        f"{school}, not {CANDIDATE_SCHOOL}."
    )
    return result


def _apply_advanced_degree_override(job: dict, result: dict) -> dict:
    """Force SKIP if the description requires current enrollment in a
    graduate-level program (MS/PhD/doctoral) with no bachelor's/undergraduate
    alternative mentioned anywhere in the posting. See module-level comment
    above _GRAD_ONLY_PHRASE_RE for why this exists as a deterministic catch
    rather than a rubric instruction."""
    if result.get("tier") not in ("APPLY", "APPLY_CAVEAT"):
        return result

    desc = job.get("description") or ""
    if _GRAD_ONLY_PHRASE_RE.search(desc) and not _UNDERGRAD_WORD_RE.search(desc):
        log.info("  Advanced-degree override: job %s requires grad-only enrollment", job.get("id"))
        result["tier"] = "INELIGIBLE"
        result["hard_ineligible"] = True
        result["reason"] = (
            "Overridden: description requires current enrollment in a graduate-level "
            "(MS/PhD) program, with no bachelor's/undergraduate alternative mentioned — "
            "candidate is a BS student."
        )

    return result


def _apply_salary_fallback(job: dict, result: dict) -> dict:
    """The model doesn't reliably notice every stated salary, especially
    when it's phrased unusually (e.g. "$ 25.00 to $40.00 per Hour") or the
    description is long — fall back to the same regex extractor used for
    backfilling already-stored jobs when the model's own extraction is empty."""
    if result.get("salary"):
        return result
    salary = extract_salary(job.get("description") or "")
    if salary:
        result["salary"] = salary
    return result


# Non-US locations are a FACTUAL test, not a judgment call, and the model does
# not reliably apply them. Measured 2026-08-16: after the rubric was updated to
# name non-US roles as a hard block, 60 of 70 stored foreign-location jobs still
# came back actionable on a re-run — and the reasons showed why. For "Sales and
# Trading Intern @ Jane Street, London UK" the model reasoned about the role
# being a trading job and never evaluated the location at all. Synthetic
# boundary cases passed while real postings failed, because real postings give
# it more interesting things to talk about.
#
# So this is deterministic, in the same spirit as the school-specific and
# advanced-degree overrides above: the candidate is a US citizen with no right
# to work abroad, which makes a foreign posting exactly as hard a block as a
# clearance he does not hold.
#
# The test is deliberately asymmetric — ANY US signal wins. A US state code, or
# the words United States/USA/Remote-US, means not blocked, which correctly
# handles both the false-friend cities (Dublin OH, Delhi MI, London KY, Paris
# TX, Berlin NH) and multi-site postings like "New York, NY / London, UK".
_US_STATE_RE = re.compile(
    r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|"
    r"NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC|PR)\b"
)
_US_WORD_RE = re.compile(r"united states|\bU\.?S\.?A?\b|remote[\s,-]*(?:us|usa|united states)", re.I)
_FOREIGN_RE = re.compile(
    r"\b(?:india|canada|united kingdom|england|scotland|wales|ireland|germany|france|spain|italy|"
    r"portugal|netherlands|belgium|poland|czech|hungary|romania|sweden|norway|denmark|finland|"
    r"switzerland|austria|greece|turkey|israel|egypt|nigeria|kenya|south africa|uae|qatar|"
    r"saudi|singapore|malaysia|thailand|vietnam|philippines|indonesia|japan|korea|china|"
    r"hong kong|taiwan|australia|new zealand|brazil|argentina|chile|colombia|peru|mexico)\b"
    r"|\b(?:london|manchester|edinburgh|dublin|cambridge|oxford|bristol|leeds|glasgow|"
    r"toronto|vancouver|montreal|ottawa|calgary|waterloo|"
    r"bengaluru|bangalore|hyderabad|mumbai|delhi|pune|chennai|noida|gurgaon|indore|kolkata|"
    r"berlin|munich|hamburg|frankfurt|paris|lyon|madrid|barcelona|lisbon|amsterdam|"
    r"rotterdam|brussels|zurich|geneva|vienna|prague|warsaw|budapest|bucharest|stockholm|"
    r"oslo|copenhagen|helsinki|dubai|tel aviv|shanghai|beijing|shenzhen|tokyo|osaka|seoul|"
    r"sydney|melbourne|auckland|sao paulo|bogota)\b",
    re.I,
)


def _apply_non_us_override(job: dict, result: dict) -> dict:
    """Force INELIGIBLE when the posting's location is outside the US and no US
    option is offered. ANY US signal exempts the posting."""
    if result.get("tier") not in ("APPLY", "APPLY_CAVEAT"):
        return result
    loc = job.get("location") or ""
    if not loc:
        return result
    if _US_STATE_RE.search(loc) or _US_WORD_RE.search(loc):
        return result
    m = _FOREIGN_RE.search(loc)
    if not m:
        return result
    log.info("  Non-US override: job %s located in %r", job.get("id"), loc[:40])
    result["tier"] = "INELIGIBLE"
    result["hard_ineligible"] = True
    result["reason"] = (
        f"Overridden: this role is based in {m.group(0).title()} with no US or US-remote "
        f"option stated; candidate is a US citizen without the right to work there."
    )
    return result


def _never_skip_github_sourced(job: dict, result: dict) -> dict:
    """GitHub tracker sources (SimplifyJobs/speedyapply) are curated,
    internship-only lists the user trusts completely — never auto-SKIP one on
    a judgment call. Leave it in APPLY or MAYBE for a human decision. Runs
    last so it overrides the rubric and the full-time override.

    EXCEPTION: hard ineligibility is not a judgment call. If an override
    established that the candidate literally cannot hold the role — it demands
    a graduate program he isn't in, or a partner university he doesn't attend —
    resurrecting it to MAYBE just parks an impossible job in the queue forever.
    Those overrides set result["hard_ineligible"], and this respects it.

    Found by test_current_classifications.py, which reported a PhD-only posting
    and a Drexel-only co-op as "still wrong" run after run: the overrides were
    firing correctly and this function was undoing them."""
    if not job.get("id", "").startswith("gh:"):
        return result
    if result.get("tier") != "INELIGIBLE":
        return result
    if result.get("hard_ineligible"):
        log.info("  gh: job %s stays SKIP — hard ineligibility, not a judgment call", job.get("id"))
        return result
    result["tier"] = "APPLY_CAVEAT"
    return result
