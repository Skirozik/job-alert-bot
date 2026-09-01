"""Repair rows the PENDING drain promoted without a description.

WHAT WENT WRONG. requeue_rogue_rows flipped the zombie Modal writer's rows to
PENDING so retry_pending would reclassify them with the current classifier.
That worked for rows that carried a stored description. But the zombie's
LinkedIn-sourced SKIP rows (and its gh: rows) were stored WITHOUT one -- its
title gate skipped them before any fetch -- and retry_pending deliberately
never re-fetches. Classifying from a bare title cannot see "full-time, not an
internship" or "PhD required" or "TS/SCI day 1", and the rubric sends judgment
calls to APPLY_CAVEAT, never INELIGIBLE. So "Nordstrom - Engineer 1" and
".NET Developer" were promoted to APPLY_CAVEAT and pushed, and Netflix
PhD-required roles landed APPLY_CAVEAT instead of INELIGIBLE.

WHAT THIS DOES. For every row that is tier APPLY/APPLY_CAVEAT, status='new',
description IS NULL, and found_at >= 2026-09-01 (the rogue window -- rows the
real pipeline discovers store their description at insert, so a null one
today is a drain promotion):

  1. fetch the real description (external_descriptions for gh:/ats: ids,
     the LinkedIn guest endpoint for numeric ids)
  2. re-run classify() on the full posting
  3. update tier/reason/suggested_resume/salary/description in place

NO PUSHES, EVER -- this file never imports notifier. These rows already
notified (wrongly, in some cases); this corrects the record, it does not
re-announce it. A row whose description cannot be fetched is left unchanged
and listed at the end for a manual look.

Dry run by default: lists the rows it would repair and writes nothing.
    cd scraper && python repair_descriptionless_promotions.py             # dry
    cd scraper && python repair_descriptionless_promotions.py --execute   # fix
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from classifier import classify
from external_descriptions import fetch_external_description
from linkedin import fetch_description as fetch_linkedin_description
from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger(__name__)

ROGUE_EPOCH = "2026-09-01T00:00:00+00:00"


def main() -> int:
    execute = "--execute" in sys.argv[1:]
    client = get_client()

    jobs = (client.table("jobs")
            .select("id,title,company,location,apply_url,status,tier,found_at")
            .in_("tier", ["APPLY", "APPLY_CAVEAT"])
            .eq("status", "new")
            .is_("description", "null")
            .gte("found_at", ROGUE_EPOCH)
            .order("found_at").execute().data) or []

    print()
    print(f"{'WOULD REPAIR' if not execute else 'REPAIRING'} {len(jobs)} row(s): "
          "active tier, no stored description, found in the rogue window")
    print("=" * 92)
    for j in jobs:
        print(f"  [{j['tier']:<12}] {(j.get('company') or '?')[:24]:<24} "
              f"{(j.get('title') or '')[:48]}")
    if not execute:
        print()
        print("Dry run — nothing fetched, nothing classified, nothing written.")
        return 0

    fixed = unchanged = unfetchable = 0
    orphans = []
    for i, job in enumerate(jobs, 1):
        log.info("[%d/%d] %s @ %s [%s]", i, len(jobs), job.get("title"),
                 job.get("company"), job["id"])
        if job["id"].startswith(("gh:", "ats:")):
            desc = fetch_external_description(job.get("apply_url", ""))
        else:
            desc, _, apply_url, _, _ = fetch_linkedin_description(job["id"])
            if apply_url and not job.get("apply_url"):
                job["apply_url"] = apply_url
        if not desc:
            unfetchable += 1
            orphans.append(job)
            log.info("  -> no description obtainable; left as-is")
            continue

        job["description"] = desc
        result = classify(job)
        if result.get("failed"):
            unfetchable += 1
            orphans.append(job)
            log.warning("  -> classify failed (%s); left as-is", result.get("failed_kind"))
            continue

        patch = {
            "description": desc,
            "tier": result.get("tier", job["tier"]),
            "reason": result.get("reason", ""),
            "suggested_resume": result.get("suggested_resume", "General"),
        }
        if result.get("salary"):
            patch["salary"] = result["salary"]
        if job.get("apply_url"):
            patch["apply_url"] = job["apply_url"]
        client.table("jobs").update(patch).eq("id", job["id"]).eq("status", "new").execute()
        if patch["tier"] != job["tier"]:
            fixed += 1
            log.info("  -> %s -> %s | %s", job["tier"], patch["tier"], patch["reason"][:70])
        else:
            unchanged += 1
            log.info("  -> tier stands (%s) now with the full posting behind it", patch["tier"])

    print()
    print(f"Repaired with a real description: {fixed + unchanged} "
          f"({fixed} tier corrected, {unchanged} confirmed) — {unfetchable} unfetchable:")
    for j in orphans:
        print(f"  [{j['tier']:<12}] {(j.get('company') or '?')[:24]:<24} "
              f"{(j.get('title') or '')[:48]}  id={j['id']}")
    if orphans:
        print("  These kept a title-only verdict. Judge them by eye on the dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
