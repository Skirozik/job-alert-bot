"""Claude Haiku fit classifier for the Brice pipeline.

Fork of scraper/classifier.py (by way of scraper_hassan/classifier.py). The
mechanism is unchanged: the candidate's rubric is read once and sent as a
cached system prompt, and the verdict is forced through a classify_job tool
call at temperature 0.

Kept from the main pipeline:
  * the billing/auth breaker and `failed_kind`, so a failed classification is
    PARKED as PENDING by main.process_job rather than dropped or stored with a
    made-up verdict, and a credit outage costs one API call per run;
  * deterministic overrides, applied in this order after the tier is
    validated:
      family net      an I-5 ("outside his families") verdict on a title the
                      family filter places in a target family, with no
                      software/hardware role noun, becomes APPLY_CAVEAT: the
                      rubric makes every close call APPLY_CAVEAT (scraper_brice
                      only; Jump Trading's "Campus Systems Engineer" came back
                      I-5 on four runs of four);
      non-US          a posting located only outside the US, with no US or
                      US-remote option, is INELIGIBLE (the ATS boards and
                      jobright lists include foreign sites);
      salary fallback regex salary extraction when the model returns none;
      title-only cap  without a real description (jobright rows, a failed
                      Workday fetch) APPLY becomes APPLY_CAVEAT -- last, so it
                      sees the settled tier.
Dropped: the full-time, school, advanced-degree and insider-group overrides
and never-skip-GitHub. They encode the main candidate's situation and a
tracker source this pipeline does not have.

The tool text names no candidate facts: every hard block lives in the rubric's
numbered "INELIGIBLE — the complete list", which the tier description points
to. Actions logs are public and show each job's title, so neither reasons nor
per-job verdicts are logged at INFO -- the override lines are DEBUG, off in
Actions -- only ids and failures.
"""

import logging
import random
import re
import time
from typing import Optional

import anthropic
import families
import title_gate
from config import ANTHROPIC_API_KEY, CANDIDATE_PROFILE_PATH
from salary_extraction import extract_salary

log = logging.getLogger(__name__)

_client: Optional[anthropic.Anthropic] = None
_profile: Optional[str] = None

MODEL = "claude-haiku-4-5-20251001"
# Per request, in seconds. The SDK default is 600 s per attempt, and with the SDK's own two retries one hung call
# could outlast the 12 minutes between the 48-minute work budget and the 60-minute workflow timeout. A normal call
# takes a few seconds.
REQUEST_TIMEOUT_S = 60.0

# A job whose classification fails is PARKED as tier="PENDING" (main.process_job)
# and retried on later runs (main.retry_pending) -- never stored with a fallback
# verdict, which dedup would then hide forever (2026-08-04: 32 of 82 such rows on
# the main pipeline were real APPLYs), and never simply dropped, which loses a
# LinkedIn job once it ages out of the lookback window.
MAX_CLASSIFY_ATTEMPTS = 3

# Tripped by a billing or auth failure, which no amount of retrying can fix.
# Once set, classify() returns immediately without touching the API -- so an
# outage costs one failed call per run instead of 3 attempts x exponential
# backoff per job. Per-process by design: every scheduled run is a fresh
# process, so it cannot stick past a run and needs no reset logic.
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
    BEFORE reading tier -- the tier here is a placeholder, not a judgment.

    The caller parks the job as PENDING rather than dropping it. `failed_kind`
    tells it which: billing/auth mean the outage is account-wide and worth an
    owner alert, transient/malformed mean this one job hiccuped and will
    quietly retry.
    """
    return {
        "failed": True,
        "failed_kind": kind,
        "tier": "APPLY_CAVEAT",
        "reason": f"Classifier error — parked for retry ({detail[:120]})",
    }


MAX_TOKENS = 400

_VALID_TIERS = ("APPLY", "APPLY_CAVEAT", "INELIGIBLE")

# Tool definitions render ahead of the system prompt, so this text is read on every call and is part of the
# cached prefix. It names no candidate facts: the hard blocks live in the rubric's numbered list.
_CLASSIFY_TOOL = {
    "name": "classify_job",
    "description": "Record the classification of a job posting against the candidate profile.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tier": {
                "type": "string",
                "enum": list(_VALID_TIERS),
                "description": (
                    "Fit tier. APPLY = clean fit, no caveat worth mentioning. APPLY_CAVEAT = worth applying, but "
                    "with exactly one specific reservation the candidate should know before spending time on it; "
                    "the reason field must state that caveat in under 12 words. INELIGIBLE = a HARD block only: "
                    "the posting meets one of the numbered conditions (I-1, I-2, ...) in the profile's list headed "
                    "'INELIGIBLE — the complete list'. That list is exhaustive: a posting that meets none of its "
                    "conditions is never INELIGIBLE. Full-time, entry-level, new-grad, early-career and "
                    "associate-level roles are what this search is for, never a block in themselves. Anything that "
                    "is a judgment call rather than a numbered hard block is APPLY_CAVEAT, never INELIGIBLE."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "For APPLY: one short sentence on the match. GROUNDING RULE — name only technologies, tools, "
                    "or responsibilities that appear VERBATIM in this posting's text. Do not infer a stack from the "
                    "company, the job title, or what a role like this usually involves, and never restate the "
                    "candidate's own skills as though the posting asked for them. If the posting names no specific "
                    "technology, say 'title-level match only' rather than inventing one. A reason that names a "
                    "technology absent from the posting is a failure even when the tier is right. For "
                    "APPLY_CAVEAT: the caveat itself, under 12 words, naming the specific reservation. For "
                    "INELIGIBLE: start with the label of the condition that applies (for example 'I-1:'), then one "
                    "short sentence naming the evidence in the posting."
                ),
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
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=REQUEST_TIMEOUT_S)
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

    Returns a dict with tier, reason and salary -- or, when no verdict could be
    obtained, _failed()'s dict with failed=True and failed_kind, which the
    caller must check first.
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
                # Pinned. Classification must be reproducible: at the API
                # default, identical input produced different verdicts ~20-30%
                # of the time on borderline jobs.
                temperature=0,
                system=_system_prompt(),
                tools=[_CLASSIFY_TOOL],
                tool_choice={"type": "tool", "name": "classify_job"},
                messages=[{"role": "user", "content": user_prompt}],
            )

            tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
            if tool_use is None:
                # A malformed response is not retryable in any useful way, but
                # it IS a failure -- don't invent a verdict for it.
                log.error("Classifier returned no tool_use block for job %s", job.get("id"))
                return _failed("no tool_use block in response", "malformed")

            result = dict(tool_use.input)

            if result.get("tier") not in _VALID_TIERS:
                # A short token ("MAYBE", "N/A") is logged as is; anything else
                # only by length -- prose in the tier field could be a reason,
                # and the Actions log is public.
                tier_val = result.get("tier")
                shown = (repr(tier_val) if isinstance(tier_val, str) and re.fullmatch(r"[A-Z_/]{1,20}", tier_val)
                         else f"<{len(str(tier_val))} chars>")
                log.warning("Unexpected tier %s for job %s — defaulting to APPLY_CAVEAT", shown, job.get("id"))
                result["tier"] = "APPLY_CAVEAT"

            result = _apply_family_net(job, result)
            result = _apply_non_us_override(job, result)
            result = _apply_salary_fallback(job, result)
            result = _apply_title_only_override(job, result)

            return result

        except Exception as exc:
            last_exc = exc
            kind = _error_kind(exc)

            # Billing and auth are account-wide and permanent for this run.
            # Retrying them is pure wasted wall-clock.
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


# Non-US locations are a FACTUAL test, not a judgment call, and the model does
# not reliably apply them: on the main pipeline, after the rubric named non-US
# roles as a hard block, 60 of 70 stored foreign-location jobs still came back
# actionable on a re-run (2026-08-16). This pipeline's ATS boards and jobright
# lists include foreign sites, so the check is deterministic here too.
#
# The test is deliberately asymmetric -- ANY US signal wins. A US state code, or
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
# The three regexes above are byte-identical to scraper/classifier.py's (test_classifier_tool.py checks). This
# fork's boards and lists add places main never sees; they reached the 2026-09-30 candidates.
_FOREIGN_EXTRA_RE = re.compile(
    r"\b(?:tunisia|kuwait|bahrain|morocco|casablanca|ecuador|quito|united arab emirates|jalisco|nuevo le[oó]n"
    r"|monterrey|guadalajara|tijuana)\b",
    re.I,
)


def _apply_non_us_override(job: dict, result: dict) -> dict:
    """Force INELIGIBLE when the posting's location is outside the US and no US
    option is offered. ANY US signal exempts the posting -- in this fork also a
    spelled-out state or a stand-alone U.S. city (families.names_us_location):
    "Vienna, Virginia", "Paris, Texas", "Chicago, New York, London"."""
    if result.get("tier") not in ("APPLY", "APPLY_CAVEAT"):
        return result
    loc = job.get("location") or ""
    if not loc:
        return result
    if _US_STATE_RE.search(loc) or _US_WORD_RE.search(loc):
        return result
    m = _FOREIGN_RE.search(loc) or _FOREIGN_EXTRA_RE.search(loc)
    if not m:
        return result
    if families.names_us_location(loc):
        return result
    log.debug("  Non-US override: job %s located in %r", job.get("id"), loc[:40])
    result["tier"] = "INELIGIBLE"
    result["hard_ineligible"] = True
    result["reason"] = f"Overridden: based in {m.group(0).title()} with no US or US-remote option stated."
    return result


_I5_RE = re.compile(r"^\W*I-5\b")
FAMILY_NET_REASON = "Verify duties: flagged as outside the target families"


def _apply_family_net(job: dict, result: dict) -> dict:
    """An I-5 verdict on an in-family title becomes APPLY_CAVEAT.

    I-5 is "outside all his families". When the title itself sits in one of
    them (families.classify_family, the same test the ATS and jobright passes
    use) and names no software, hardware or science role, calling it out of
    family is a judgment about the duties -- and the rubric's asymmetry rule
    makes every close call APPLY_CAVEAT, never INELIGIBLE. Hiding the job costs
    a real opportunity; the caveat costs a look. Jump Trading's "Campus Systems
    Engineer", an infrastructure role that lists Python and shell scripting,
    came back "I-5: software development" on four runs of four.

    Never touches another numbered block (I-1 years, I-2 seniority, ...), a
    help-desk title (the owner's floor), or a title with a software/hardware
    role noun. Runs FIRST, so the non-US override and the title-only cap still
    see the tier it settles on.
    """
    if result.get("tier") != "INELIGIBLE" or not _I5_RE.match(str(result.get("reason") or "")):
        return result
    title = " ".join(str(job.get("title") or "").split())
    fam, sub = families.classify_family(title, job.get("company") or "")
    if (fam not in title_gate.ALLOWED_FAMILIES or (fam, sub) in title_gate.EXCLUDED_SUBFAMILIES
            or families.SWE_HW_SCI.search(title)):
        return result
    log.debug("  Family net: job %s", job.get("id"))
    result["tier"] = "APPLY_CAVEAT"
    result["reason"] = FAMILY_NET_REASON
    return result


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


# A description this short is not a description. Matches the >200 bar the main
# pipeline's external fetchers use to tell real content from an empty SPA shell.
_MIN_REAL_DESCRIPTION = 200


def _apply_title_only_override(job: dict, result: dict) -> dict:
    """A job classified without a description can never be a clean APPLY.

    jobright rows carry no description by design (the bot never opens
    jobright.ai), and a Workday row whose description fetch failed has none
    either. Labelled APPLY on the title alone, such a row would look identical,
    in the queue and on the phone, to a posting the classifier read in full.

    Downgrade to APPLY_CAVEAT, never lower: hiding a job costs a real
    opportunity while a caveat costs ten seconds, so this keeps the job and
    tells the truth about what is known -- and APPLY_CAVEAT pings are silent.
    INELIGIBLE is left alone: a hard block established from the title is still
    a hard block.

    Runs LAST so it sees the tier the whole chain settled on.
    """
    if result.get("tier") != "APPLY":
        return result

    desc = (job.get("description") or "").strip()
    if len(desc) >= _MIN_REAL_DESCRIPTION:
        return result

    log.debug("  Title-only override: job %s has %d chars of description", job.get("id"), len(desc))
    result["tier"] = "APPLY_CAVEAT"
    result["reason"] = "Title-only: no description available — check the posting"
    return result
