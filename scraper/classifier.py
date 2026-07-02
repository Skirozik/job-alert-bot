"""Claude Haiku fit classifier.

Reads Candidate_Profile_and_Filters.md at startup and uses it as the rubric
for every classification call. Returns tier (APPLY/MAYBE/SKIP), a one-line
reason, and which resume variant to use.

The candidate profile + instructions are sent as a cached system prompt
(prompt caching), since they're identical on every call in a run — this cuts
input token cost substantially on the 2nd+ call. The response is forced
through a tool call with a JSON schema instead of asking for free-text JSON,
so there's no markdown-fence stripping or JSONDecodeError fallback path.
"""

import logging
import re
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

_client: Optional[anthropic.Anthropic] = None
_profile: Optional[str] = None

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 400

_VALID_TIERS = ("APPLY", "MAYBE", "SKIP")

_CLASSIFY_TOOL = {
    "name": "classify_job",
    "description": "Record the classification of an internship job posting against the candidate profile.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tier": {
                "type": "string",
                "enum": list(_VALID_TIERS),
                "description": "Fit tier for this posting.",
            },
            "reason": {
                "type": "string",
                "description": "One sentence explaining the match or mismatch.",
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

    try:
        resp = _get_client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=_system_prompt(),
            tools=[_CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_job"},
            messages=[{"role": "user", "content": user_prompt}],
        )

        tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
        if tool_use is None:
            log.error("Classifier returned no tool_use block for job %s", job.get("id"))
            return {"tier": "MAYBE", "reason": "Classifier error — review manually", "suggested_resume": "General"}

        result = dict(tool_use.input)

        if result.get("tier") not in _VALID_TIERS:
            log.warning("Unexpected tier '%s' for job %s — defaulting to MAYBE", result.get("tier"), job.get("id"))
            result["tier"] = "MAYBE"

        result = _apply_full_time_override(job, result)
        result = _apply_salary_fallback(job, result)
        result = _never_skip_github_sourced(job, result)

        return result

    except Exception as exc:
        log.error("Classifier failed for job %s: %s", job.get("id"), exc)
        return {"tier": "MAYBE", "reason": "Classifier error — review manually", "suggested_resume": "General"}


def _apply_full_time_override(job: dict, result: dict) -> dict:
    """Force SKIP if the description explicitly says "full-time position"
    (etc.) and never mentions internship/co-op anywhere — regardless of how
    the model scored stack fit. Never fires on postings that also mention
    internship/co-op (e.g. "may convert to full-time after graduation" is a
    normal, desirable internship perk, not a full-time posting)."""
    if result.get("tier") not in ("APPLY", "MAYBE"):
        return result

    desc = job.get("description") or ""
    if _FULL_TIME_PHRASE_RE.search(desc) and not _INTERNSHIP_WORD_RE.search(desc):
        log.info("  Full-time override: job %s described as full-time with no internship language",
                  job.get("id"))
        result["tier"] = "SKIP"
        result["reason"] = (
            "Overridden: description explicitly states this is a full-time/permanent "
            "position, with no internship/co-op language anywhere in the posting."
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


def _never_skip_github_sourced(job: dict, result: dict) -> dict:
    """GitHub tracker sources (SimplifyJobs/speedyapply) are curated,
    internship-only lists the user trusts completely — never auto-SKIP one,
    regardless of what the rubric or the full-time override above decided.
    Always leave it in APPLY or MAYBE for a human decision. Runs last so it
    overrides every other mechanism that could have produced SKIP."""
    if job.get("id", "").startswith("gh:") and result.get("tier") == "SKIP":
        result["tier"] = "MAYBE"
    return result
