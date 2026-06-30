"""Claude Haiku fit classifier.

Reads Candidate_Profile_and_Filters.md at startup and uses it as the rubric
for every classification call. Returns tier (APPLY/MAYBE/SKIP), a one-line
reason, and which resume variant to use.
"""

import json
import re
import logging
from typing import Optional

import anthropic
from config import ANTHROPIC_API_KEY, CANDIDATE_PROFILE_PATH

log = logging.getLogger(__name__)

_client: Optional[anthropic.Anthropic] = None
_profile: Optional[str] = None

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 300


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


def classify(job: dict) -> dict:
    """Classify a job posting against the candidate profile.

    Returns dict with keys: tier, reason, suggested_resume.
    Falls back to MAYBE + manual review note on any error.
    """
    prompt = f"""You evaluate internship job postings for a specific candidate. Return valid JSON only — no markdown, no extra text.

CANDIDATE PROFILE AND FILTERS:
{_get_profile()}

JOB POSTING:
Title: {job.get("title", "")}
Company: {job.get("company", "")}
Location: {job.get("location", "")}
Description: {job.get("description") or "(not available — classify on title/company/location only)"}

Return exactly this JSON:
{{"tier": "APPLY" or "MAYBE" or "SKIP", "reason": "<one sentence explaining the match or mismatch>", "suggested_resume": "Mobile" or "AI" or "Frontend" or "1Password" or "General"}}"""

    raw = ""
    try:
        resp = _get_client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()

        # Strip markdown code fences if the model wraps its response
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = raw.rstrip("`").strip()

        result = json.loads(raw)

        if result.get("tier") not in ("APPLY", "MAYBE", "SKIP"):
            log.warning("Unexpected tier '%s' for job %s — defaulting to MAYBE", result.get("tier"), job.get("id"))
            result["tier"] = "MAYBE"

        return result

    except json.JSONDecodeError:
        log.error("Classifier returned non-JSON for job %s: %r", job.get("id"), raw)
        return {"tier": "MAYBE", "reason": "Classifier error — review manually", "suggested_resume": "General"}
    except Exception as exc:
        log.error("Classifier failed for job %s: %s", job.get("id"), exc)
        return {"tier": "MAYBE", "reason": "Classifier error — review manually", "suggested_resume": "General"}
