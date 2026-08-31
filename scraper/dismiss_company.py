"""Bulk-dismiss every review-queue job from named companies.

WHY: some employers post internships at a volume that swamps the review queue --
TikTok and ByteDance together accounted for 148 of 391 gold stars and well over a
hundred rows in To apply. Dismissing them is a triage decision, not a data fix.

SCOPE, deliberately narrow: only rows that are still in the REVIEW queue --
status='new' AND tier in (APPLY, APPLY_CAVEAT). It never touches anything
already actioned (applied/saved/heard_back/...), because those carry outcome
history, and it never touches INELIGIBLE rows, which are already hidden.

REVERSIBLE: this only sets status='dismissed'. The rows are intact and the
dashboard's "Reset to new" puts any of them back. Nothing is deleted.

Company matching uses the same normalisation as scraper/db.py norm_company, plus
a token-prefix rule so "TikTok", "🔥TikTok" and "TikTok USDS Joint Venture" all
match the target "TikTok" -- the emoji and the suffix are noise, not a different
employer.

    cd scraper && python dismiss_company.py TikTok ByteDance             # dry run
    cd scraper && python dismiss_company.py TikTok ByteDance --execute   # write
"""

import logging
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from db import get_client, norm_company

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger(__name__)

PAGE = 1000


def _matches(company: str, targets: list) -> bool:
    """Normalised equality, or a normalised token-prefix.

    Prefix rather than substring: "tiktok usds joint venture" is TikTok, but a
    bare `in` test would also catch an unrelated employer whose name merely
    contains the target somewhere in the middle.
    """
    n = norm_company(company or "")
    return any(n == t or n.startswith(t + " ") for t in targets)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    execute = "--execute" in sys.argv
    if not args:
        print(__doc__)
        return 1

    targets = [norm_company(a) for a in args]
    log.info("Targets (normalised): %s", ", ".join(targets))

    client = get_client()
    rows, offset = [], 0
    while True:
        page = (client.table("jobs")
                .select("id,company,title,tier,status")
                .eq("status", "new").in_("tier", ["APPLY", "APPLY_CAVEAT"])
                .order("id").range(offset, offset + PAGE - 1).execute().data) or []
        rows.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE

    hits = [r for r in rows if _matches(r.get("company", ""), targets)]
    by_company = Counter(r.get("company") or "?" for r in hits)

    log.info("Scanned %d review-queue rows (status=new, tier APPLY/APPLY_CAVEAT)", len(rows))
    log.info("Matched %d", len(hits))
    for company, n in by_company.most_common():
        log.info("    %4d  %s", n, company)

    if not hits:
        log.info("Nothing to do.")
        return 0

    if not execute:
        log.info("")
        log.info("DRY RUN — nothing written. Re-run with --execute to dismiss these.")
        for r in hits[:10]:
            log.info("  would dismiss %s  %s @ %s", r["id"], (r.get("title") or "")[:44], r.get("company"))
        return 0

    done = failed = 0

    def _dismiss(row):
        client.table("jobs").update({"status": "dismissed"}).eq("id", row["id"]).execute()

    # Threaded for the same reason backfill_target_keys is: one request per row,
    # and hundreds of sequential round trips overruns the job timeout.
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_dismiss, r): r for r in hits}
        for fut in as_completed(futures):
            try:
                fut.result()
                done += 1
            except Exception as exc:
                failed += 1
                if failed <= 10:
                    log.error("  failed for %s: %s", futures[fut]["id"], exc)

    log.info("Done: %d dismissed, %d failed", done, failed)
    log.info("Reversible: each row still exists; 'Reset to new' in the dashboard restores it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
