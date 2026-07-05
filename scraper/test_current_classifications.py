"""Dry-run regression test: re-run a sample of jobs already on the dashboard
through the CURRENT pre-filter + classifier pipeline and report what would
happen, without writing anything to the DB.

Use this after a rubric/pre-filter change to sanity-check the fix actually
works against real data before trusting it — includes the specific jobs
found problematic during tonight's session (as a direct regression check)
plus a random sample of whatever's currently active, so it also surfaces
anything NOT yet caught.

Run from the scraper directory:
    cd scraper && python test_current_classifications.py
"""

import logging
import random
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from main import _is_senior_role, _is_new_grad_role, _is_non_internship_title
from classifier import classify
from db import get_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# Jobs specifically reported as misclassified tonight, before their
# respective fixes — a direct regression check that each stays fixed.
KNOWN_BAD_IDS = [
    "4372440447",   # XIFIN "Associate Machine Learning Engineer" — no internship marker
    "4436163280",   # Amigo "Applied AI Engineer" — full-time, spans new-grad to senior
    "4436148998",   # AssetWatch "Backend Engineer" — explicit "full-time position"
    "4435223212",   # GTECH "Mobile Application Developer" — 5+ yrs required, no marker
    "gh:a9f07e130f51032c",  # American Heart Association — requires MS/PhD
    # Comcast's Drexel Co-op Program / SIG's "Co-op with Drexel University" —
    # restricted to students currently enrolled at Drexel, never mentioned in
    # the model's own reasoning. The two SIG/Xfinity ones are gh:-sourced and
    # already applied/dismissed, so expect MAYBE (never-skip-github still
    # applies) with the corrected reason, not SKIP.
    "4435406993",            # Comcast "Comcast Platform Software Engineer Co-op"
    "4435478554",            # Comcast "Comcast Software Engineer Co-op" (status=applied)
    "gh:5e38bc3e0a1b3e2c",   # Xfinity "Comcast AI Strategy & Transformation Co-op" (status=dismissed)
    "gh:a004f82aaa9c4617",   # SIG "Financial Reporting AI Co-op" (status=applied)
    "gh:6c37d2ab4c1ede73",   # SIG "AI Co-op" (status=applied)
]

SAMPLE_SIZE = 10


def _reclassify(job: dict) -> tuple[str, str]:
    """Run a job through the live pre-filter + classifier chain fresh.
    Returns (new_tier, reason_or_note). Does not write to the DB."""
    if _is_senior_role(job["title"]):
        return "SKIP", "Pre-filtered: seniority keyword in title"
    if _is_new_grad_role(job["title"]):
        return "SKIP", "Pre-filtered: new grad / full-time role, not an internship"
    if not job["id"].startswith("gh:") and _is_non_internship_title(job["title"]):
        return "SKIP", "Pre-filtered: no internship marker in title"

    result = classify(job)
    return result.get("tier", "MAYBE"), result.get("reason", "")


def run():
    client = get_client()

    known_bad = []
    for jid in KNOWN_BAD_IDS:
        rows = client.table("jobs").select("*").eq("id", jid).execute().data
        if rows:
            known_bad.append(rows[0])
        else:
            log.warning("Known-bad job %s not found in DB", jid)

    active = (
        client.table("jobs")
        .select("*")
        .in_("tier", ["APPLY", "MAYBE"])
        .eq("status", "new")
        .execute()
        .data
    )
    sample = random.sample(active, min(SAMPLE_SIZE, len(active)))

    log.info("=== Regression check: %d known-previously-bad jobs ===", len(known_bad))
    regressions = 0
    for job in known_bad:
        new_tier, reason = _reclassify(job)
        stored_tier = job["tier"]
        flag = "STILL WRONG" if new_tier in ("APPLY", "MAYBE") else "correctly caught"
        if new_tier in ("APPLY", "MAYBE"):
            regressions += 1
        log.info("[%s] stored=%s fresh=%s | %s @ %s | %s",
                  flag, stored_tier, new_tier, job["title"], job["company"], reason[:100])

    log.info("=== Sample check: %d random jobs currently on the dashboard ===", len(sample))
    changed = 0
    for job in sample:
        new_tier, reason = _reclassify(job)
        stored_tier = job["tier"]
        note = "" if new_tier == stored_tier else "  <-- WOULD CHANGE"
        if new_tier != stored_tier:
            changed += 1
        log.info("stored=%s fresh=%s | %s @ %s%s", stored_tier, new_tier, job["title"], job["company"], note)

    log.info("=== Done: %d/%d known-bad jobs still wrong, %d/%d sampled jobs would change ===",
              regressions, len(known_bad), changed, len(sample))
    log.info("(Dry run — nothing was written to the database.)")


if __name__ == "__main__":
    run()
