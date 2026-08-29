"""One-off: delete redundant duplicate rows that were never engaged with.

Three sources give one posting three primary keys (LinkedIn a bare numeric id,
ats:sha1(url), gh:sha1(apply_url)), so the table accumulated several rows per
real job. The dashboard has collapsed them for display since dupes.ts landed,
and the notification ledger now stops them pinging more than once -- but the
redundant rows themselves are still there, cluttering every view.

WHAT THIS WILL NOT TOUCH, and why:

  - Groups where ANY member has a status other than 'new'. Those carry the
    per-source history the outcomes work exists to analyse ("does direct-to-ATS
    beat LinkedIn Easy Apply?"), and that question is unanswerable once the
    losing source's row is gone. A group is either entirely untouched noise, or
    it is evidence; there is no middle case worth the risk.
  - Rows whose target_key is NULL. That means the application target could not
    be PROVEN (see target_key.py) -- dupes.ts would fall back to fuzzy matching
    there, and fuzzy matching is not a basis for an irreversible delete.
  - Anything at all, unless --execute is passed.

WHY DELETING IS SAFE HERE: the surviving row keeps notified_at, so if a deleted
row is rediscovered and re-inserted later, the ledger's sibling check suppresses
its push rather than pinging again. The ledger is what makes this reversible in
the only sense that matters -- the data comes back, the noise does not.

Run from the scraper directory:
    cd scraper && python dedupe_existing.py              # dry run, review this
    cd scraper && python dedupe_existing.py --execute    # then delete
"""

import logging
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from db import get_client
from target_key import definitive_target_key

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

PAGE = 1000


def load_rows(client) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        result = (
            client.table("jobs")
            .select("id,company,title,status,tier,url,apply_url,is_easy_apply,"
                    "target_key,description,salary,found_at,notified_at")
            .order("id")
            .range(offset, offset + PAGE - 1)
            .execute()
        )
        page = result.data or []
        rows.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
    return rows


def _survivor_rank(row: dict) -> tuple:
    """Best row first. Richer rows win, then direct-ATS for its better apply
    link, then the oldest so found_at keeps meaning "when we first saw it"."""
    return (
        0 if row.get("description") else 1,
        0 if row.get("salary") else 1,
        0 if str(row.get("id", "")).startswith("ats:") else 1,
        str(row.get("found_at") or ""),
    )


def main() -> int:
    execute = "--execute" in sys.argv
    client = get_client()

    log.info("Loading jobs...")
    rows = load_rows(client)
    log.info("Loaded %d rows", len(rows))

    groups: dict = defaultdict(list)
    for row in rows:
        key = row.get("target_key") or definitive_target_key(row)
        if key:
            groups[key].append(row)

    dupe_groups = {k: v for k, v in groups.items() if len(v) > 1}
    log.info("%d groups have more than one row", len(dupe_groups))

    to_delete: list[dict] = []
    skipped_touched = 0
    skipped_rows = 0

    for key, members in sorted(dupe_groups.items()):
        touched = [m for m in members if (m.get("status") or "new") != "new"]
        if touched:
            skipped_touched += 1
            skipped_rows += len(members)
            log.info("SKIP  %s — %d rows, %d already actioned (%s)",
                     key, len(members), len(touched),
                     ", ".join(sorted({str(m.get("status")) for m in touched})))
            continue

        ordered = sorted(members, key=_survivor_rank)
        keep, drop = ordered[0], ordered[1:]
        log.info("GROUP %s", key)
        log.info("  KEEP   %-22s %s @ %s", keep["id"], (keep.get("title") or "")[:48],
                 keep.get("company"))
        for d in drop:
            log.info("  DELETE %-22s %s @ %s", d["id"], (d.get("title") or "")[:48],
                     d.get("company"))
        to_delete.extend(drop)

    log.info("")
    log.info("SUMMARY")
    log.info("  duplicate groups found ....... %d", len(dupe_groups))
    log.info("  skipped (already actioned) ... %d groups / %d rows", skipped_touched, skipped_rows)
    log.info("  rows that would be deleted ... %d", len(to_delete))

    if not to_delete:
        log.info("Nothing to do.")
        return 0

    if not execute:
        log.info("")
        log.info("DRY RUN — nothing deleted. Review the GROUP blocks above, then re-run "
                 "with --execute.")
        return 0

    deleted = 0
    for row in to_delete:
        try:
            client.table("jobs").delete().eq("id", row["id"]).execute()
            deleted += 1
        except Exception as exc:
            log.error("  failed to delete %s: %s", row["id"], exc)
    log.info("Done: %d rows deleted, %d groups preserved untouched", deleted, skipped_touched)
    return 0


if __name__ == "__main__":
    sys.exit(main())
