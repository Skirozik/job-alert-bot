"""One-off backfill: re-classify APPLY_CAVEAT rows whose caveat was the GPA.

WHY: the candidate profile never stated a GPA, so the classifier had nothing to
compare a posting's floor against and demoted the row on that alone. Measured
2026-09-10: 64 such rows, 58 still open, and most of them name a floor the
candidate clears. The GPA now lives in the private profile that
CANDIDATE_PROFILE_PATH points at -- NOT in this repo, which is public, and no
figure from it belongs in this file -- so these rows carry a verdict the
classifier would no longer reach.

WHY NOT backfill_reclassify_apply.py: that one selects `.eq("tier", "APPLY")`,
so it cannot see an APPLY_CAVEAT row at all.

SCOPE: only rows whose stored reason mentions GPA. A blanket re-classification
of all 2,954 caveats would re-decide thousands of rows this change has nothing
to do with, and every write is a chance to lose a verdict that was correct.

SAFETY, inherited from backfill_reclassify_apply.py:
  * never writes `status` -- that column holds the application record
  * writes only the columns listed in WRITABLE
  * dry run by default; --apply to write
  * a parked classifier result never overwrites a real verdict

    cd scraper && python backfill_gpa_caveats.py
    cd scraper && python backfill_gpa_caveats.py --apply
"""

import argparse
import logging
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from classifier import classify
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger(__name__)

# `status` is deliberately absent, and `description` too: nothing here re-fetches
# a posting, so there is no reason for this script to touch its text.
WRITABLE = ("tier", "reason", "suggested_resume", "salary")

# Word-boundary, because "itar" inside "military" already burned one grep today.
GPA = re.compile(r"\bGPA\b", re.I)


def fetch_caveats(client):
    """PostgREST silently caps a response at 1000 rows, so page explicitly."""
    out, offset = [], 0
    while True:
        page = (
            client.table("jobs")
            .select("id, title, company, location, apply_url, url, status, tier, "
                    "reason, description")
            .eq("tier", "APPLY_CAVEAT")
            .range(offset, offset + 999)
            .execute()
            .data
        )
        out.extend(page)
        if len(page) < 1000:
            return out
        offset += 1000


def run(write: bool):
    client = get_client()
    caveats = fetch_caveats(client)

    # Rows already acted on are history, not a queue. Re-classifying one cannot
    # help and every write is a chance to damage the record.
    active = [j for j in caveats
              if (j.get("status") or "new") not in ("applied", "dismissed")]
    targets = [j for j in active if GPA.search(j.get("reason") or "")]

    log.info("APPLY_CAVEAT rows: %d total, %d still open, %d citing GPA",
             len(caveats), len(active), len(targets))
    log.info("Mode: %s", "APPLY (writing)" if write else "DRY RUN (no writes)")

    changed = unchanged = errors = 0
    transitions = {}

    for i, job in enumerate(targets, 1):
        label = f"{job['title'][:48]} @ {job['company'][:24]}"
        before = (job.get("reason") or "")[:70]

        try:
            result = classify(job)
        except Exception as exc:
            log.error("[%d/%d] classify failed: %s | %s", i, len(targets), exc, label)
            errors += 1
            continue

        if result.get("failed"):
            log.warning("[%d/%d] classifier parked this row, skipping | %s",
                        i, len(targets), label)
            errors += 1
            continue

        new_tier = result.get("tier", job.get("tier"))

        # NEVER demote to INELIGIBLE from here. This backfill exists to retire a
        # GPA caveat; a hard block is a different decision on a different axis,
        # and INELIGIBLE hides the job from the dashboard entirely.
        #
        # Both demotions the dry run produced were wrong, and the postings say
        # so. Citi: "graduating between December 2027 and May 2028" -- he
        # graduates December 2027, inside it -- yet the model returned "not
        # actually an internship, Time Type: Full time", reading the RETURN
        # OFFER salary line on a posting titled Summer Analyst Program. Ketjen
        # cited a window that appears nowhere in its text.
        #
        # A false INELIGIBLE is unrecoverable in practice: nobody reviews a
        # hidden row. Report it and leave the stored verdict alone.
        if new_tier == "INELIGIBLE" and job.get("tier") != "INELIGIBLE":
            log.warning("[%d/%d] REFUSED demotion to INELIGIBLE, left as %s | %s",
                        i, len(targets), job.get("tier"), label)
            log.warning("        model said: %s", (result.get("reason") or "")[:110])
            log.warning("        -> review by hand; this script will not hide a job")
            errors += 1
            continue

        patch = {
            "tier": new_tier,
            "reason": result.get("reason", ""),
            "suggested_resume": result.get("suggested_resume", "General"),
        }
        if result.get("salary"):
            patch["salary"] = result["salary"]
        assert "status" not in patch, "status must never be written by this script"
        assert set(patch) <= set(WRITABLE), f"unexpected column: {set(patch) - set(WRITABLE)}"

        if new_tier != job.get("tier"):
            key = f"{job.get('tier')} -> {new_tier}"
            transitions[key] = transitions.get(key, 0) + 1
            changed += 1
            log.info("[%d/%d] %s | %s", i, len(targets), key, label)
            log.info("        was: %s", before)
            log.info("        now: %s", (result.get("reason") or "")[:70])
        else:
            unchanged += 1
            log.info("[%d/%d] unchanged (%s) | %s", i, len(targets), new_tier, label)
            log.info("        now: %s", (result.get("reason") or "")[:70])

        if write:
            try:
                client.table("jobs").update(patch).eq("id", job["id"]).execute()
            except Exception as exc:
                log.error("  DB update failed for %s: %s", job["id"], exc)
                errors += 1

    log.info("=== %s ===", "Done" if write else "Dry run complete")
    log.info("  tier changed   : %d", changed)
    log.info("  tier unchanged : %d", unchanged)
    log.info("  errors/skipped : %d", errors)
    for k, v in sorted(transitions.items(), key=lambda kv: -kv[1]):
        log.info("    %-28s %d", k, v)
    if not write:
        log.info("  (nothing was written -- re-run with --apply)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default is a dry run)")
    run(ap.parse_args().apply)
