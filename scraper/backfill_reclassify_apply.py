"""One-off backfill: re-fetch stale descriptions and re-classify every APPLY row.

WHY: an audit of the 124 live APPLY rows on 2026-08-20 found six whose stored
description was mostly page chrome (cookie banners, department menus) and five
with no description at all. b20a2c2 fixed the capture path and added a
title-only override, but existing rows still carry whatever was stored when they
were first seen. This re-runs them through the fixed pipeline.

SAFETY: never writes `status`. That column holds the record of 531 applications
and a re-classification that clobbered it would be unrecoverable. Same discipline
as backfill_norm_keys.py — this touches only the columns it owns.

Dry run by default; pass --apply to write.

    cd scraper && python backfill_reclassify_apply.py
    cd scraper && python backfill_reclassify_apply.py --apply
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
from external_descriptions import fetch_external_description
from db import get_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# Columns this script is allowed to write. `status` is deliberately absent.
WRITABLE = ("description", "tier", "reason", "suggested_resume", "salary")

# A stored description that opens with these is site furniture, not a posting.
# Only used to decide whether re-fetching is worth a request — never to reject.
CHROME_HEAD = re.compile(
    r"cookie|skip to main content|privacy (?:policy|notice)|accept all"
    r"|navigate this website|select how often|search by keyword|we use cookies",
    re.I,
)
MIN_REAL = 200


def needs_refetch(desc: str) -> str:
    """Return a short reason to re-fetch, or '' to keep what is stored."""
    text = (desc or "").strip()
    if len(text) < MIN_REAL:
        return f"only {len(text)} chars"
    if CHROME_HEAD.search(text[:400]):
        return "opens with site chrome"
    return ""


def fetch_all_apply(client):
    """PostgREST silently caps a response at 1000 rows, so page explicitly."""
    out, offset = [], 0
    while True:
        page = (
            client.table("jobs")
            .select("id, title, company, location, apply_url, url, status, tier, reason, description")
            .eq("tier", "APPLY")
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
    jobs = fetch_all_apply(client)

    # Rows already acted on are history, not a queue. Re-classifying one cannot
    # help — it is not going to be re-decided — and every write is a chance to
    # damage the record.
    active = [j for j in jobs if (j.get("status") or "new") not in ("applied", "dismissed")]
    log.info("APPLY rows: %d total, %d still active", len(jobs), len(active))
    log.info("Mode: %s", "APPLY (writing)" if write else "DRY RUN (no writes)")

    refetched = refetch_failed = changed = unchanged = errors = 0
    transitions = {}

    for i, job in enumerate(active, 1):
        label = f"{job['title'][:52]} @ {job['company'][:26]}"

        why = needs_refetch(job.get("description"))
        if why:
            target = job.get("apply_url") or job.get("url") or ""
            fresh = fetch_external_description(target)
            old_n = len((job.get("description") or "").strip())
            if fresh and len(fresh.strip()) >= MIN_REAL:
                log.info("[%d/%d] refetch (%s): %d -> %d chars | %s",
                         i, len(active), why, old_n, len(fresh), label)
                job["description"] = fresh
                refetched += 1
            else:
                log.info("[%d/%d] refetch (%s) FAILED, keeping stored | %s",
                         i, len(active), why, label)
                refetch_failed += 1

        try:
            result = classify(job)
        except Exception as exc:
            log.error("[%d/%d] classify failed: %s | %s", i, len(active), exc, label)
            errors += 1
            continue

        # A parked classifier error must never overwrite a real verdict.
        if result.get("failed"):
            log.warning("[%d/%d] classifier parked this row, skipping | %s", i, len(active), label)
            errors += 1
            continue

        new_tier = result.get("tier", job.get("tier"))
        patch = {
            "description": job.get("description"),
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
            log.info("[%d/%d] %s | %s | %s", i, len(active), key, label, result.get("reason", "")[:90])
            changed += 1
        else:
            unchanged += 1

        if write:
            try:
                client.table("jobs").update(patch).eq("id", job["id"]).execute()
            except Exception as exc:
                log.error("  DB update failed for %s: %s", job["id"], exc)
                errors += 1

    log.info("=== %s ===", "Done" if write else "Dry run complete")
    log.info("  descriptions re-fetched : %d  (%d failed, kept stored)", refetched, refetch_failed)
    log.info("  tier changed            : %d", changed)
    log.info("  tier unchanged          : %d", unchanged)
    log.info("  errors/skipped          : %d", errors)
    for k, v in sorted(transitions.items(), key=lambda kv: -kv[1]):
        log.info("    %-28s %d", k, v)
    if not write:
        log.info("  (nothing was written — re-run with --apply)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default is a dry run)")
    run(ap.parse_args().apply)
