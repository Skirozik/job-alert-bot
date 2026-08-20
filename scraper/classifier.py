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
                    "For APPLY: one short sentence on the match. GROUNDING RULE — name only technologies, tools, or responsibilities that appear VERBATIM in this posting's text. Do not infer a stack from the company, the job title, or what a role like this usually involves, and never restate the candidate's own skills as though the posting asked for them. If the posting names no specific technology, say 'title-level match only' rather than inventing one. A reason that names a technology absent from the posting is a failure even when the tier is right. For APPLY_CAVEAT: the caveat itself, under 12 words, naming the specific reservation (e.g. 'strong C++ + Unreal, no game projects' or 'prefers 3.5 GPA'). For INELIGIBLE: one sentence naming which hard block applies."
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

            result = _apply_returning_intern_override(job, result)
            result = _apply_full_time_override(job, result)
            result = _apply_school_specific_override(job, result)
            result = _apply_advanced_degree_override(job, result)
            result = _apply_non_us_override(job, result)
            result = _apply_salary_fallback(job, result)
            result = _never_skip_github_sourced(job, result)
            result = _apply_title_only_override(job, result)

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


# ============================================================================
# Deterministic backstop: INSIDER-GROUP ELIGIBILITY GATES.
#
# Built from gh:957897d315475c83 (RTX "Software Engineer Intern - Spring 2027",
# stored APPLY -> false push notification). Its description contains the
# dispositive sentence "This requisition is for an RTX intern returning for an
# internship in 2027." buried at the END of the Security Clearance block, some
# 4,000 characters into a posting that is otherwise a perfect stack match. The
# TITLE is clean, so the rubric's "the restriction is frequently in the TITLE,
# so read the title for it explicitly" instruction does not help here — the
# model read past one declarative sentence under attention pressure, which is
# the same failure mode the full-time / school / advanced-degree overrides
# above already exist for.
#
# A sweep of all 58,995 stored rows found 82 postings carrying a gate of this
# family and 24 of them were still live at APPLY / APPLY_CAVEAT, so these
# patterns describe the whole shape rather than the one RTX sentence:
# intern-status gates (RTX, Truist, HNTB, Williams, Travelers, Blue Origin,
# IBM, Oracle, Northrop, Baker Tilly, Kearney, Regions, Target, Accenture
# Federal, Sam's Club), military-status gates (DoD SkillBridge, HOH Fellows,
# Oracle's OVIP veteran program), pre-selected-cohort gates (PhoenixTeam,
# Scale AI's ICML attendees) and region-of-university gates (SIG's Hong
# Kong/Singapore programme).
#
# THE SAFETY RULE FOR EVERYTHING BELOW (see _SCHOOL_BARE_COOP_RE's comment: a
# too-permissive pattern matched "IBM co-op program" inside an IBM posting and
# hid a job the candidate had already applied to): a false positive is worse
# than a leak. Every pattern here was run against all 58,995 stored titles and
# descriptions; the combined match set is 82 rows and every one was read by
# hand and confirmed to be a real gate. Each pattern's own match set was also
# measured in isolation (first-match-wins otherwise hides a pattern's true
# false-positive surface), and is quoted in its comment below.
#
# SCOPE IS LOAD-BEARING. Three of these phrases are a gate in a title and
# ordinary prose in a body, so title-scoping is what keeps them safe:
#   - "intern conversion" in a BODY hits Notion's "Head of Early Career
#     Recruiting", where "university recruiting, internship conversion,
#     emerging pipelines" is a list of that job's duties.
#   - "returning ... intern" in a BODY hits 27 rows, ALL boilerplate:
#     "returning to school after the internship" (AWS, Anduril, Abbott,
#     Rivian), "return offer" (MeshyAI), "earn a return internship" (Optiver).
#     It is the single most common sentence in the whole intern corpus.
#   - "SkillBridge" anywhere in a BODY matches Penn State ARL's ordinary,
#     open-to-everyone "Research and Development Engineer Intern", whose body
#     merely says ARL "is an authorized DoD SkillBridge partner". Restricting
#     the token to the TITLE keeps all 42 real SkillBridge rows (Rise8, Two
#     Six and Black Cape, the three live leaks, all name it in the title) and
#     drops that false positive.
# ============================================================================

# Descriptions arrive with literal "&#xa;" where newlines should be, and some
# ATS sources arrive as raw HTML ("<p><strong>This posting is for ..."). Every
# pattern below spans several words, so it must not be broken by an entity or a
# tag sitting mid-phrase. Flatten once, match many times.
#
# Block-level markup collapses to " . " and NOT to a space. That period is a
# safety device, not cosmetics: the gaps in the sentence patterns below are
# [^.] runs, and the "<qualifier> interns only" patterns cannot cross a period
# either. Collapsing a </li> or an "&#xa;" to a bare space would let a bullet
# ending in "...our 2026 summer interns" weld itself onto the next block's
# extremely common "Only those selected for an interview will be contacted."
# and manufacture a gate out of two innocent sentences. Verified over the whole
# corpus: sentence-stop and bare-space flattening produce an IDENTICAL 82-row
# match set, so the period costs nothing and closes that class outright.
_MARKUP_BLOCK_RE = re.compile(
    r"&#xa;|&#xd;|[\r\n]|<\s*/?\s*(?:p|div|li|ul|ol|br|tr|td|th|h[1-6]|section|table)\b[^>]{0,200}>",
    re.IGNORECASE,
)
_MARKUP_INLINE_RE = re.compile(r"&#x9;|&nbsp;|&#160;|<[^>]{1,200}>|\s+", re.IGNORECASE)


def _flatten_markup(text: str) -> str:
    """'Interns&#xa;ONLY' -> 'Interns . ONLY'; '<strong>Interns</strong> only'
    -> 'Interns only'. Block markup becomes a sentence stop, inline markup and
    runs of whitespace become a single space."""
    return _MARKUP_INLINE_RE.sub(" ", _MARKUP_BLOCK_RE.sub(" . ", text)).strip()


# --- TITLE-SCOPED GATES (never run against a description; see above) ---------

# "Intern Conversion: Software Developer" (IBM), "Research Extern Intern
# conversion" (IBM — note the lowercase 'c', which is why this is IGNORECASE),
# "2026 Intern Conversion - Aerospace Software Apps Engineer I" (Blue Origin),
# "Full-time Intern Conversion" (Oracle), "(BT Summer Intern Conversions Only)"
# (Baker Tilly). A conversion req is by definition open only to the company's
# own interns. 11 titles corpus-wide, every one a gate; three of them have an
# EMPTY or entirely ordinary description, so the title is the only evidence
# that exists.
_TITLE_CONVERSION_RE = re.compile(r"\bintern(?:ship)?\s+conversions?\b", re.IGNORECASE)

# "2027 Returning Intern Software Engineer" (Northrop, description length 0),
# "Returning Intern: Software Developer" (IBM), "Returning Summer Analyst"
# (Accenture Federal — 'Analyst', not 'Intern', so a rule keyed on "returning
# intern" alone misses it, and its description is empty too). 3 titles corpus
# wide, all gates. The optional filler is restricted to seasons and years
# rather than \w+ so "returning to school"-style phrasing can never be reached
# through it. "student" is deliberately NOT in the noun list: in university
# usage a "returning student" is someone resuming their own studies, not a
# company's former hire, and no corpus row needs it.
_TITLE_RETURNING_RE = re.compile(
    r"\breturning\s+(?:(?:summer|winter|fall|spring|20\d\d)\s+){0,2}"
    r"(?:intern|co[\s-]?op|analyst|associate|scholar|trainee)s?\b",
    re.IGNORECASE,
)

# DoD SkillBridge is, by statute, open only to service members inside their
# final 180 days of active duty. 42 corpus titles name it and the candidate can
# satisfy none of them. \s? absorbs "Skill Bridge"; IGNORECASE absorbs the four
# spellings actually seen (SkillBridge, Skillbridge, skillbridge, and Newport
# News' all-caps SKILLBRIDGE).
_TITLE_SKILLBRIDGE_RE = re.compile(r"\bskill\s?bridge\b", re.IGNORECASE)

# "Internship - Active Duty Only" (Aura Health). Same military-status gate
# stated without the SkillBridge brand name. 1 corpus title.
_TITLE_MILITARY_ONLY_RE = re.compile(
    r"\b(?:active[\s-]?duty|military|veterans?|service\s?members?)\s+only\b", re.IGNORECASE
)

# Oracle's OVIP: "OCI Software Engineer Intern - OVIP". 6 corpus titles, all
# Oracle veteran-program reqs. This is only ever used as the required second
# factor for _VETERAN_PROGRAM_RE below — never on its own, because "Military
# Intern" alone could name an ordinary defense-sector internship.
_TITLE_VETERAN_PROGRAM_RE = re.compile(
    r"\bOVIP\b|\b(?:veterans?|military)\s+(?:internship|intern|fellowship)\b", re.IGNORECASE
)


# --- TEXT-SCOPED GATES (title + description, after flattening) ---------------

# "This requisition is open to 2026 Truist Interns only." / "For current/former
# HNTB Interns ONLY." / "2026 Regions Interns Only." / "Current Interns Only-"
# (the Target posting the rubric already records as a confirmed live miss).
#
# The leading current|former|...|20\d\d token is what makes this safe. The bare
# phrase "interns only" occurs in exactly 5 rows of 58,995 and all 5 are real
# gates, so there is no benign in-corpus exercise of this pattern at all —
# which makes it the least corpus-validated pattern in the set, and is why it
# carries the extra _PERK_CONTEXT_RE guard below. The optional [/&,] branch is
# for HNTB's "current/former"; the {0,3} filler carries the company name.
_INTERNS_ONLY_RE = re.compile(
    r"\b(?:current|former|returning|previous|existing|internal|20\d\d)"
    r"(?:\s*[/&,]\s*(?:current|former|returning|previous|existing|internal))?"
    r"(?:\s+[A-Za-z][\w.&'’-]*){0,3}\s+interns?\s+only\b",
    re.IGNORECASE,
)

# The benign shape "interns only" would plausibly take in a posting that does
# not restrict who may apply: a PERK offered to the intern class. "Housing is
# provided for our 2026 summer interns only", "these events are for current
# interns only". Zero rows in the corpus do this today, but 14 rows do the
# exact equivalent with "employees only" (Bilt's "Exclusive Employee only Bilt
# Points", UST's "Company-paid Employee Only benefits", Copart's E-Verify
# "For U.S. applicants and employees only" x11), which is what that shape looks
# like in the wild. A gate phrased with a real exclusivity frame ("this
# requisition is open/available/offered only to ...") is caught by
# _RESTRICTED_REQ_RE regardless, so skipping perk context costs no recall: all
# 5 true "interns only" rows still fire.
_PERK_CONTEXT_RE = re.compile(
    r"\b(?:housing|meals?|lunch|discounts?|perks?|benefits?|swag|parking|insurance|"
    r"401\s?k|stipend|events?|socials?|networking|provided|offered|available|free|"
    r"gym|shuttle)\b",
    re.IGNORECASE,
)

# "(Drexel University Co-ops Only)" — AVEVA, buried in the body as "Job Title:
# Software Developer Intern (Drexel University Co-ops Only)". The school
# override misses it because _SCHOOL_BARE_COOP_RE wants the school adjacent to
# the co-op token and _SCHOOL_COOP_PREFIX_RE wants a delimiter after it.
#
# [A-Z] and NOT re.IGNORECASE, exactly as in _SCHOOL_NAME_RE: the capital is
# the signal "a real proper noun is being named" rather than a generic phrase.
# The group noun list is deliberately ONLY Co-ops/Interns. "Employees?" was
# tried and removed — it matches the 14 benign perk rows above. "Students?" was
# tried and removed too: no corpus row needs it and it would match the utterly
# ordinary requirement "Full-Time Students Only".
#
# Group 1 is the qualifier and is checked twice in the function: against the
# candidate's own school, so a hypothetical "Georgia State University Co-ops
# Only" can never fire, and against _GENERIC_GROUP_WORDS, so "Summer Interns
# Only" and "Full Time Interns Only" — capitalised but not naming anybody — are
# left to _INTERNS_ONLY_RE, which requires a status/year token they lack.
_NAMED_GROUP_ONLY_RE = re.compile(
    r"\b((?:[A-Z][A-Za-z&.\-]*\s+){1,3})(?:Co[\s-]?[Oo]ps?|Interns?)\s+[Oo]nly\b"
)
_GENERIC_GROUP_WORDS = frozenset(
    """summer winter fall spring autumn full part time paid unpaid new all the our your and for
    january february march april may june july august september october november december
    student students""".split()
)

# THE RTX SENTENCE, and its whole grammatical family. Two halves that must both
# appear inside one sentence (the [^.] gaps stop at the first period):
#   half 1, the exclusivity frame — "This requisition is for", "This requisition
#           is open to", "This job posting is intended for", "this role is only
#           open to", "This opportunity is open only to", "This posting is for";
#   half 2, the insider group — "intern returning" (RTX), "2026 Truist Interns",
#           "Williams' summer 2026 interns", "former Regions contract workers",
#           or a pre-selected/attendee cohort ("Drexel University students who
#           have already been officially selected", PhoenixTeam; "candidates who
#           attended ICML 2026", Scale AI).
# Half 1 alone is worthless — it matches 1,337 corpus rows, because "This
# position is for a software engineering intern joining our platform team" is an
# ordinary sentence. Requiring half 2 within 80 characters is what makes the
# pair dispositive: across 58,995 rows the pair fires 11 times and all 11 are
# gates. Widening the two gaps from 80/60 to 200/150 still yields exactly those
# 11 rows, so the margin is large rather than tuned.
#
# "interns? (who is/are) returning" and not "intern returning": all four RTX
# rows use the singular verbatim, but RTX regenerates this boilerplate with the
# year embedded, and the plural or relative-clause rewrite ("This requisition is
# for RTX interns returning for an internship in 2027", "...for an RTX intern
# who is returning...") is the likeliest recurrence of the exact bug this
# override was built for. Branch 2 cannot cover it, because branch 2 needs the
# qualifier BEFORE the noun. Verified: the widened branch matches the same 11
# rows, 0 added, 0 lost.
_RESTRICTED_REQ_RE = re.compile(
    r"\b(?:this|the)\s+(?:requisition|job\s+posting|posting|position|role|opportunity|opening)\s+"
    r"(?:is\s+)?(?:only\s+)?"
    r"(?:for|open(?:\s+only)?\s+to|intended\s+(?:only\s+)?for|restricted\s+to|limited\s+to|"
    r"reserved\s+for|available\s+(?:only\s+)?to|offered\s+(?:only\s+)?to|designated\s+for)\b"
    r"[^.]{0,80}?"
    r"(?:\binterns?\s+(?:who\s+(?:is|are)\s+)?returning\b"
    r"|\b(?:returning|current|former|previous|existing|internal|20\d\d)\b[^.;]{0,60}?"
    r"\b(?:interns?|co[\s-]?ops?|employees?|contract\s+workers?)\b"
    r"|\bwho\s+(?:have\s+)?(?:already\s+)?(?:been\s+)?(?:officially\s+)?"
    r"(?:selected|pre-selected|invited|interviewed|attended|met\s+with)\b)",
    re.IGNORECASE,
)

# "internal candidates only" / "current applicants only" — named verbatim in
# Candidate_Profile_and_Filters.md as a gate. ZERO corpus rows match it today,
# in either direction: this is pure forward defence, and its false-positive
# surface across 58,995 rows is measured at zero. "employees?" is deliberately
# NOT in the noun list here — "current employees only" is how a benefits
# paragraph talks (see _PERK_CONTEXT_RE), while "current applicants only" is
# not a sentence a perk paragraph can produce.
_INTERNAL_ONLY_RE = re.compile(
    r"\b(?:internal|current)\s+(?:candidates?|applicants?)\s+only\b", re.IGNORECASE
)

# Travelers, five postings, verbatim: "The intent of this position is to provide
# our internal employees, 2026 Travelers Summer Interns and Summer Students the
# ability to apply... Applications outside of this audience will not be
# considered at this time." The generic half of that sentence ("will not be
# considered") is unusable on its own — "incomplete applications will not be
# considered" is common — so anchor on the distinctive noun phrase. 5 corpus
# rows, all Travelers, all gates, two of them still live at APPLY.
_OUTSIDE_AUDIENCE_RE = re.compile(r"\boutside\s+(?:of\s+)?th(?:is|e)\s+audience\b", re.IGNORECASE)

# Truist: "Applicants who were not 2026 Truist interns will not be considered."
# The exclusion is only trusted when the excluded trait is intern STATUS — a
# year or current/former token — and the consequence is stated in the same
# sentence. 2 corpus rows, both Truist.
#
# Both of those requirements are load-bearing against a sentence that is not a
# gate at all: "Applicants who are not available for the full internship period
# will not be considered" is an ordinary scheduling requirement the candidate
# can satisfy. It has no status token, and \binterns?\b deliberately does not
# match "internship", so it stays clean. Verified: adding the status token and
# dropping the -ship alternation keeps both Truist rows.
_NOT_AN_INTERN_RE = re.compile(
    r"\b(?:applicants?|candidates?|those|anyone|students?)\s+who\s+(?:were|was|are|is|did)\s+not\b"
    r"[^.]{0,60}?\b(?:current|former|returning|previous|20\d\d)\b[^.]{0,40}?\binterns?\b"
    r"[^.]{0,60}?\bwill\s+not\s+be\s+considered\b",
    re.IGNORECASE,
)

# A qualifications-list gate: "Must be a 2026 Truist Intern", "Must be a current
# Summer 2026 Baker Tilly Intern", "Must have been a 2026 Summer Kearney &
# Company Intern". 4 corpus rows, all gates. The current|former|...|20\d\d token
# again does the work, and the [^.;] gap also stops at a semicolon so the
# innocuous "Must be a current student enrolled full time in a degree program;
# prior intern experience is a plus" cannot bridge into the word "intern".
# Measured: the 4 true gates sit 8-26 characters from the stem, and no benign
# row in the corpus has a bare "intern" in the same sentence at all — \bintern\b
# correctly does not match "internship", which is what saves the nearest benign
# row (PSECU, whose "internship/student worker" sits 77 characters away).
#
# The (?-i:[A-Z]...) island is the third requirement and the one that survives
# a rephrasing: the noun must be a NAMED employer's intern — "2026 Truist
# Intern", "current Summer 2026 Baker Tilly Intern", "2026 Summer Kearney &
# Company Intern". Without it, "Must be a 2027 graduating senior with prior
# intern experience" reads as a gate. Verified: all 4 corpus gates still fire.
_MUST_BE_INTERN_RE = re.compile(
    r"\bmust\s+(?:be|have\s+been)\s+(?:a|an)\s+"
    r"(?:current|former|returning|previous|existing|20\d\d)\b[^.;]{0,60}?"
    r"(?-i:[A-Z][\w&.\-]*)\s+[Ii]nterns?\b",
    re.IGNORECASE,
)

# Blue Origin, under Minimum Qualifications: "Successfully completed an
# internship with Blue Origin in 2026." Case-SENSITIVE on the employer name and
# requiring an explicit year, so the ordinary preferred qualification "completed
# an internship in software engineering at a technology company" cannot match.
# 2 corpus rows, both Blue Origin.
#
# The captured name is then checked against job["company"] in the function. That
# check is the difference between this and a real false positive: without it,
# the preferred qualification "has completed an internship at NASA in 2025 or
# similar research experience" reads as a gate on a posting that is not NASA's.
# Dropping the year requirement entirely still returns only these same 2 rows,
# so the year is margin rather than load-bearing.
_PRIOR_INTERNSHIP_RE = re.compile(
    r"\b(?:successfully\s+)?completed\s+(?:an?\s+)?(?:prior\s+|previous\s+)?internship\s+"
    r"(?:with|at)\s+(us\b|our\s+(?:company|team|firm|organi[sz]ation)\b"
    r"|[A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})\s+in\s+20\d\d\b"
)

# Oracle's OVIP: "About the Oracle Veteran Internship Program (OVIP): Oracle is
# proud to sponsor an internship and integration program that exposes
# transitioning military veterans and active-duty Military Spouses...". The
# model read this restriction as a selling point ("military-focused
# sponsorship") and shipped APPLY on one of them.
#
# Note how narrow this has to be: 10,575 corpus rows contain "veteran" and 3,102
# contain "military", almost all of it EEO boilerplate ("without regard to
# protected veteran status"). This phrase alone is still too weak — "we also
# sponsor a Military Internship Program for transitioning service members; this
# role is open to all students" would match it and hide an open job. So the
# function requires a SECOND factor, _TITLE_VETERAN_PROGRAM_RE in the title,
# i.e. the posting must BE the veteran programme rather than mention one. All 5
# corpus rows carry both; combined, they fire on exactly those 5.
_VETERAN_PROGRAM_RE = re.compile(
    r"\b(?:veterans?|military)\s+(?:internship|intern)\s+program\b", re.IGNORECASE
)

# The same military-status gate written as an eligibility line rather than a
# programme name: "Currently on active duty and eligible for SkillBridge"
# (Rise8), "Has served at least 180 days on active duty. Is within 180 days of
# separation or retirement" (Two Six). 8 corpus rows, all genuine.
#
# The danger here is the OFCCP boilerplate that 207 rows carry — "protected
# veterans... active duty wartime or campaign badge veterans" — which is why
# neither branch keys on "active duty" alone: one requires a currently/must-be
# frame immediately in front of it, the other requires the statutory 180-day
# separation window. Widening the branches ~4x still returns the same rows.
_MILITARY_STATUS_RE = re.compile(
    r"\bwithin\s+180\s+days\s+of\s+(?:separation|retirement|transition)\b"
    r"|\b(?:currently|must\s+be|are)\s+(?:serving\s+)?on\s+active\s+duty\b"
    r"|\bactive[\s-]?duty\s+service\s?members?\s+only\b",
    re.IGNORECASE,
)

# SIG: "This program offers students currently enrolled at universities in Hong
# Kong or Singapore the opportunity to intern in the US...". A cohort gate keyed
# on where the school is, which the school override cannot see because it names
# no school. 1 corpus row.
#
# The region is CAPTURED and then tested for a US token in the function, rather
# than excluded by a lookahead at the front of the phrase. A lookahead is
# evaluated at one position only, so "enrolled at universities in Metro Atlanta"
# / "Greater Atlanta" / "North Georgia" would all slide past it and mark the
# candidate ineligible for a job in his own city — the single worst outcome this
# family can produce. Capturing and searching mirrors how _apply_non_us_override
# tests the whole location string, and how the school patterns above hand
# group(1) to _normalize_school.
_REGION_UNIVERSITY_RE = re.compile(
    r"\benrolled\s+at\s+(?:a\s+)?(?:universit(?:y|ies)|colleges?|schools?|institutions?)\s+in\s+"
    r"((?:the\s+)?[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)"
)
_REGION_EXEMPT_RE = re.compile(
    r"\b(?:U\.?S\.?A?|United\s+States|North\s+America|America|Georgia|Atlanta)\b", re.IGNORECASE
)


def _insider_group_gate(job: dict):
    """Return (label, quoted_match) for the first insider-group gate found in
    this posting, or None. Split out from the override itself so the tests can
    assert on WHICH gate fired, and so the override stays the same shape as its
    four siblings."""
    title = _flatten_markup(job.get("title") or "")
    # " . " between the two fields for the same reason block markup becomes a
    # period: a title ending in "...Interns" must not weld onto a description
    # opening with "Only candidates selected will be contacted."
    text = _flatten_markup((job.get("title") or "") + " . " + (job.get("description") or ""))

    for label, pattern in (
        ("company's own intern-conversion req", _TITLE_CONVERSION_RE),
        ("returning-intern-only req", _TITLE_RETURNING_RE),
        ("DoD SkillBridge (active-duty service members only)", _TITLE_SKILLBRIDGE_RE),
        ("military-status-only posting", _TITLE_MILITARY_ONLY_RE),
    ):
        match = pattern.search(title)
        if match:
            return label, match.group(0)

    match = _RESTRICTED_REQ_RE.search(text)
    if match:
        return "requisition restricted to a named insider group", match.group(0)

    for match in _INTERNS_ONLY_RE.finditer(text):
        if _PERK_CONTEXT_RE.search(text[max(0, match.start() - 80):match.start()]):
            continue  # a perk offered to interns, not a restriction on applying
        return "open to that company's own interns only", match.group(0)

    for match in _NAMED_GROUP_ONLY_RE.finditer(text):
        qualifier = match.group(1).strip()
        if _normalize_school(qualifier) == _CANDIDATE_SCHOOL_CORE:
            continue  # the candidate's OWN school named — not a restriction
        if all(w.lower() in _GENERIC_GROUP_WORDS for w in re.findall(r"[A-Za-z]+", qualifier)):
            continue  # "Summer Interns Only" names no group
        if _PERK_CONTEXT_RE.search(text[max(0, match.start() - 80):match.start()]):
            continue
        return "restricted to a named group the candidate is not in", match.group(0)

    for label, pattern in (
        ("internal candidates only", _INTERNAL_ONLY_RE),
        ("applications outside the stated audience are not considered", _OUTSIDE_AUDIENCE_RE),
        ("non-interns explicitly will not be considered", _NOT_AN_INTERN_RE),
        ("requires being that company's current/former intern", _MUST_BE_INTERN_RE),
    ):
        match = pattern.search(text)
        if match:
            return label, match.group(0)

    match = _PRIOR_INTERNSHIP_RE.search(text)
    if match:
        named = match.group(1)
        company_words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", job.get("company") or "")}
        named_words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", named)}
        # Only a gate when the named employer IS this posting's employer.
        # "completed an internship at NASA in 2025" in someone else's preferred
        # qualifications is a nice-to-have, not a restriction.
        if named.lower().startswith(("us", "our")) or (company_words and named_words & company_words):
            return "requires a prior internship at this same employer", match.group(0)

    if _TITLE_VETERAN_PROGRAM_RE.search(title):
        match = _VETERAN_PROGRAM_RE.search(text)
        if match:
            return "veteran/military-spouse internship programme", match.group(0)

    match = _MILITARY_STATUS_RE.search(text)
    if match:
        return "requires active-duty military status", match.group(0)

    for match in _REGION_UNIVERSITY_RE.finditer(text):
        if _REGION_EXEMPT_RE.search(match.group(1)):
            continue  # a US region, including "Metro Atlanta" — not a gate
        return "restricted to students enrolled outside the US", match.group(0)

    return None


def _apply_returning_intern_override(job: dict, result: dict) -> dict:
    """Force SKIP when the posting is open only to a group the candidate cannot
    join — the company's own returning interns, its internal employees, a
    pre-selected cohort, active-duty service members, or students enrolled
    abroad. See the module-level comment above _MARKUP_BLOCK_RE for why this is
    a deterministic catch rather than a rubric instruction: the rubric already
    carries the rule, and all four RTX postings carrying "This requisition is
    for an RTX intern returning for an internship in 2027" were classified
    actionable anyway.

    Runs FIRST in the override chain. _apply_full_time_override sets a tier
    WITHOUT hard_ineligible, so letting it fire first on one of these rows would
    leave this override's never-promote guard to return early, and
    _never_skip_github_sourced would then resurrect a gh: row it cannot
    actually apply to."""
    tier = result.get("tier")
    # PENDING is a queue state, not a verdict, and must never be touched here.
    if tier not in ("APPLY", "APPLY_CAVEAT", "INELIGIBLE"):
        return result

    gate = _insider_group_gate(job)
    if not gate:
        return result

    label, quoted = gate

    if tier == "INELIGIBLE":
        # The model already reached the right verdict unaided, so its reason is
        # kept and the tier is untouched — but the flag still has to be stamped.
        #
        # This branch is NOT cosmetic. Without it the guard above returned early
        # whenever the model got there first, hard_ineligible was never set, and
        # _never_skip_github_sourced read the verdict as an ordinary judgment
        # call and resurrected the row to APPLY_CAVEAT — which still pushes. The
        # perverse consequence: the better the rubric got at describing these
        # gates, the MORE often the model said INELIGIBLE itself, and the more
        # often its correct verdict was silently undone. Two consecutive
        # classify() calls on RTX gh:957897d315475c83 returned APPLY_CAVEAT and
        # INELIGIBLE from byte-identical input at temperature=0 for exactly this
        # reason. Only gh:-sourced rows can be resurrected, which is why the
        # four RTX rows failed while the IBM and Oracle LinkedIn rows passed.
        result["hard_ineligible"] = True
        return result

    log.info("  Insider-group override: job %s gated by %s", job.get("id"), label)
    result["tier"] = "INELIGIBLE"
    # Group membership he literally cannot enter, exactly like a school he does
    # not attend — so this is hard, and _never_skip_github_sourced must respect
    # it. Without this flag a gh: row (which every RTX leak is) comes straight
    # back as APPLY_CAVEAT and still pushes.
    result["hard_ineligible"] = True
    result["reason"] = (
        f"Overridden: this posting is restricted to a group the candidate is not in "
        f"({label}) — \"{quoted.strip()[:160]}\"."
    )
    return result


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


# A description this short is not a description. Matches the >200 bar that
# _fetch_generic and _fetch_icims already use to decide whether a fetch
# returned real content or an empty SPA shell.
_MIN_REAL_DESCRIPTION = 200


def _apply_title_only_override(job: dict, result: dict) -> dict:
    """A job classified without a description can never be a clean APPLY.

    Some sources structurally carry no description: SmartRecruiters' and
    Workday's listing endpoints don't include one (ats_sources.py), and the
    external fetch returns None for any unrecognized ATS. Those rows were
    labelled APPLY on the title alone and looked identical, in the queue, to a
    posting the classifier had read in full — so a guess and a verified match
    were indistinguishable at exactly the moment the user is deciding where to
    spend an evening.

    Downgrade to APPLY_CAVEAT, never lower. The profile's stated asymmetry is
    that hiding a job costs a real opportunity while a caveat costs ten
    seconds, so this keeps the job on the list and just tells the truth about
    what is known. INELIGIBLE is left alone: a hard block established from the
    title is still a hard block.

    Deterministic rather than prompted, like every other override here — 5e525a8
    recorded that prompting alone did not hold for the non-US rule.

    Runs LAST so it sees the tier the whole chain settled on.
    """
    if result.get("tier") != "APPLY":
        return result

    desc = (job.get("description") or "").strip()
    if len(desc) >= _MIN_REAL_DESCRIPTION:
        return result

    log.info("  Title-only override: job %s has %d chars of description", job.get("id"), len(desc))
    result["tier"] = "APPLY_CAVEAT"
    result["reason"] = "Classified on title only — no description available"
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
