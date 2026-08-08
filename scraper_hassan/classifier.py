"""Claude Haiku fit classifier.

Fork of scraper/classifier.py for the Hassan persona. Keeps the outer
mechanism unchanged — read Hassan_Candidate_Profile_and_Filters.md at
startup, use it as a cached system prompt, force the response through a
classify_job tool call — but drops the deterministic overrides that encode
the ORIGINAL candidate's specific situation (_apply_full_time_override,
_apply_school_specific_override, _apply_advanced_degree_override,
_never_skip_github_sourced). Those are not transferable: they reference a
different school, a different degree level, and a GitHub-tracker source
this pipeline does not have.

No new deterministic overrides are added on day one — notably NOT a regex
backstop for the security-clearance rule, even though that is this
persona's highest-stakes filter. Overrides get added only after live
monitoring shows Haiku actually missing a specific phrasing, which is how
the original candidate's four were earned. The clearance rule lives in the
rubric for now; watch it, and add a backstop if it slips.

Also drops `suggested_resume` from the schema entirely — this persona has
one resume, not variants to choose between.
"""

import logging
import random
import time
from typing import Optional

import anthropic
from config import ANTHROPIC_API_KEY, CANDIDATE_PROFILE_PATH
from salary_extraction import extract_salary

log = logging.getLogger(__name__)

_client: Optional[anthropic.Anthropic] = None
_profile: Optional[str] = None

MODEL = "claude-haiku-4-5-20251001"

# On a transient API failure the job is NOT stored at all — see _failed() and
# main.process_job(). A missing row self-heals, because the next run
# rediscovers the listing and classifies it properly. A row stored with a
# fallback verdict does not: dedup means it is never looked at again.
#
# Confirmed the hard way on 2026-08-04, when an API outage stored jobs as
# "MAYBE / Classifier error — review manually" across every pipeline; on the
# main one, 32 of 82 such rows turned out to be APPLY.
MAX_CLASSIFY_ATTEMPTS = 3


def _backoff_seconds(attempt: int) -> float:
    return 2.0 * (2 ** attempt) + random.uniform(0, 1.5)


def _failed(detail: str) -> dict:
    """Signals 'do not store this job'. Callers must check result["failed"]
    BEFORE reading tier — the tier here is a placeholder, not a judgment."""
    return {
        "failed": True,
        "tier": "MAYBE",
        "reason": f"Classifier error — not stored, will retry next run ({detail[:120]})",
    }


MAX_TOKENS = 400

_VALID_TIERS = ("APPLY", "MAYBE", "SKIP")

_CLASSIFY_TOOL = {
    "name": "classify_job",
    "description": "Record the classification of a job posting against the candidate profile.",
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
            "salary": {
                "type": "string",
                "description": "Salary if mentioned in the description, e.g. '$20-30/hr' or "
                               "'$85,000-$110,000/yr'. Empty string if not mentioned.",
            },
        },
        "required": ["tier", "reason"],
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
    text = f"""You evaluate job postings for a specific candidate.
Use the classify_job tool to record your evaluation.

CANDIDATE PROFILE AND FILTERS:
{_get_profile()}"""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def classify(job: dict) -> dict:
    """Classify a job posting against the candidate profile.

    Returns dict with keys: tier, reason, salary.
    Falls back to MAYBE + manual review note on any error.
    """
    user_prompt = f"""JOB POSTING:
Title: {job.get("title", "")}
Company: {job.get("company", "")}
Location: {job.get("location", "")}
Description: {job.get("description") or "(not available — classify on title/company/location only)"}"""

    last_exc = None
    for attempt in range(MAX_CLASSIFY_ATTEMPTS):
      try:
        resp = _get_client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            # Pinned. Classification must be reproducible: at the API default,
            # identical input produced different verdicts ~20-30% of the time
            # on borderline jobs.
            temperature=0,
            system=_system_prompt(),
            tools=[_CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_job"},
            messages=[{"role": "user", "content": user_prompt}],
        )

        tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
        if tool_use is None:
            log.error("Classifier returned no tool_use block for job %s", job.get("id"))
            return _failed("no tool_use block in response")

        result = dict(tool_use.input)

        if result.get("tier") not in _VALID_TIERS:
            log.warning("Unexpected tier '%s' for job %s — defaulting to MAYBE", result.get("tier"), job.get("id"))
            result["tier"] = "MAYBE"

        result = _apply_salary_fallback(job, result)

        return result

      except Exception as exc:
        last_exc = exc
        if attempt < MAX_CLASSIFY_ATTEMPTS - 1:
            backoff = _backoff_seconds(attempt)
            log.warning("Classifier attempt %d/%d failed for job %s (%s) — retrying in %.1fs",
                        attempt + 1, MAX_CLASSIFY_ATTEMPTS, job.get("id"), exc, backoff)
            time.sleep(backoff)
        else:
            log.error("Classifier failed for job %s after %d attempts: %s",
                      job.get("id"), MAX_CLASSIFY_ATTEMPTS, exc)

    return _failed(str(last_exc))


def _apply_salary_fallback(job: dict, result: dict) -> dict:
    """The model doesn't reliably notice every stated salary, especially
    when it's phrased unusually (e.g. "$ 25.00 to $40.00 per Hour") or the
    description is long — fall back to the same regex extractor used by the
    original persona's classifier when the model's own extraction is empty."""
    if result.get("salary"):
        return result
    salary = extract_salary(job.get("description") or "")
    if salary:
        result["salary"] = salary
    return result
