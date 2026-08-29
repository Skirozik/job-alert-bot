"""READ-ONLY audit: does anything in "To apply" duplicate something already applied to?

WHY: a Notion internship appeared in To apply that looked already-applied. It
turned out not to be a duplicate -- the applied rows were New York and the
To-apply rows were San Francisco, genuinely separate postings. But that raised
the real question: across the WHOLE list, is anything in To apply a duplicate of
something already actioned?

THIS SCRIPT ONLY REPORTS. There is no --apply, no --execute, no write path of
any kind, and no branch that could acquire one. It answers a question; what to
do about the answer is a separate decision.

WHAT IT IS LOOKING FOR, and why the obvious cases are already handled:

  web/lib/dupes.ts collapses duplicates for display, and a collapsed group takes
  its highest-ranked member's status (effectiveStatus), so an applied twin
  already drops the whole group out of To apply. That machinery works. Two
  things can stop a pair reaching it:

    - dupes.ts:421 short-circuits on `!key.startsWith('url:')`, so a DEFINITIVE
      target_key match (ashby:/workday:/greenhouse:/...) unions unconditionally,
      location never consulted. Those pairs are already collapsed -- which is
      why Tier C below should come back EMPTY.
    - everything else needs fuzzy Phase 2, which requires compatibleLocations,
      which returns false outright when EITHER side has no parseable location
      (dupes.ts:234 `if (!la.size || !lb.size) return false`). ats_sources.py
      writes `location or ""`, so an ATS row with a blank location can never
      fuzzy-match anything. That is the most likely real leak.

TIERS:
  A  same norm_key, locations compatible or either side blank
     -> the real finding: should be collapsed, is not
  B  same norm_key, locations clearly differ
     -> the Notion/SF-vs-NY class. Reported so its size is visible; the user
        has explicitly said these are NOT duplicates and nothing should act on
        them.
  C  same target_key
     -> expected EMPTY. A non-empty Tier C means the reading of dupes.ts above
        is wrong, and that must be resolved before trusting any other number
        in this report.

Run from the scraper directory:
    cd scraper && python audit_apply_dupes.py
"""

import logging
import re
import sys
import unicodedata
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
COLS = ("id,company,title,location,status,tier,url,apply_url,is_easy_apply,"
        "target_key,norm_key,found_at")

# ── Mirrors of web/lib/dupes.ts, ported ONLY for this audit ────────────────
# The real matcher stays in TypeScript. These exist so the report can say
# "dupes.ts would/would not have paired these", and they are deliberately the
# small deterministic half -- plain() and normalizedLocations() -- not the
# fuzzy title scoring. If they drift, the audit's location column gets less
# accurate; it cannot cause a write, because nothing here writes.

_CITY_STATE = re.compile(r"([A-Za-z][A-Za-z .'\-]*?),\s*([A-Z]{2})\b")
_ALIASES = {"new york city ny": "new york ny", "nyc": "new york ny", "la": "los angeles ca"}


def _plain(raw) -> str:
    text = unicodedata.normalize("NFKD", raw or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def _normalized_locations(raw) -> set:
    text = raw or ""
    out = set()
    for m in _CITY_STATE.finditer(text):
        n = _plain(f"{m.group(1)}, {m.group(2)}")
        out.add(_ALIASES.get(n, n))
    for part in re.split(r"\s*[·;]\s*", text):
        n = _plain(re.sub(r"\+\d+\s*$", "", part))
        if n:
            out.add(_ALIASES.get(n, n))
    return out


def _locations_compatible(a: dict, b: dict) -> bool:
    """True when dupes.ts WOULD consider these locations compatible, plus the
    blank case reported separately.

    dupes.ts returns False when either set is empty. Here that is folded into
    Tier A on purpose: a blank location is exactly the condition that stops the
    real matcher from ever pairing two rows, so it is the finding, not a reason
    to dismiss the pair.
    """
    la, lb = _normalized_locations(a.get("location")), _normalized_locations(b.get("location"))
    if not la or not lb:
        return True   # blank -> surfaced as Tier A, see docstring
    return bool(la & lb)


def _key_or_none(value):
    """norm_key '' and '|' are not identities. make_norm_key("","") is the
    literal '|', so without this every blank-company row would 'match' every
    other one -- the same trap claim_job_notification guards with
    nullif(nullif(...,''),'|')."""
    v = (value or "").strip()
    return None if v in ("", "|") else v


def _fetch(client, predicate: str) -> list:
    rows, offset = [], 0
    while True:
        q = client.table("jobs").select(COLS).order("id").range(offset, offset + PAGE - 1)
        for clause in predicate.split("&"):
            col, _, rest = clause.partition("=")
            op, _, val = rest.partition(".")
            if op == "eq":
                q = q.eq(col, val)
            elif op == "neq":
                q = q.neq(col, val)
            elif op == "in":
                q = q.in_(col, val.strip("()").split(","))
        page = q.execute().data or []
        rows.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
    return rows


def _fmt(row: dict, width: int = 46) -> str:
    return (f"{row['id']:<22} {(row.get('title') or '')[:width]:<{width}} "
            f"@ {(row.get('company') or '')[:22]:<22} [{(row.get('location') or '—')[:28]}]")


def classify(to_apply: list, actioned: list) -> dict:
    """Pure: rows in, {tier: [(row, matches)]} out. No IO, so the thing the
    whole report means is directly testable rather than inferred from stdout."""
    by_norm, by_target = defaultdict(list), defaultdict(list)
    for row in actioned:
        nk = _key_or_none(row.get("norm_key"))
        if nk:
            by_norm[nk].append(row)
        tk = row.get("target_key") or definitive_target_key(row)
        if tk:
            by_target[tk].append(row)

    tiers = {"A": [], "B": [], "C": []}

    for row in to_apply:
        tk = row.get("target_key") or definitive_target_key(row)
        if tk and by_target.get(tk):
            tiers["C"].append((row, by_target[tk]))
            continue

        nk = _key_or_none(row.get("norm_key"))
        if not nk:
            continue
        matches = by_norm.get(nk)
        if not matches:
            continue

        compatible = [m for m in matches if _locations_compatible(row, m)]
        if compatible:
            tiers["A"].append((row, compatible))
        else:
            tiers["B"].append((row, matches))

    return tiers


def main() -> int:
    client = get_client()

    log.info("Loading To apply (status=new, tier APPLY/APPLY_CAVEAT)...")
    # Same predicates the dashboard uses -- web/app/page.tsx:221-223.
    to_apply = _fetch(client, "status=eq.new&tier=in.(APPLY,APPLY_CAVEAT)")
    log.info("  %d rows", len(to_apply))

    log.info("Loading everything already actioned (status != new)...")
    actioned = _fetch(client, "status=neq.new")
    log.info("  %d rows", len(actioned))

    tiers = classify(to_apply, actioned)

    for name, header in (
        ("C", "TIER C — same requisition id. EXPECTED EMPTY: dupes.ts unions these unconditionally"),
        ("A", "TIER A — same company+role, location compatible or blank. THE REAL FINDING"),
        ("B", "TIER B — same company+role, different city. The Notion class; user says NOT duplicates"),
    ):
        pairs = tiers[name]
        print()
        print("=" * 100)
        print(f"{header}  —  {len(pairs)} row(s)")
        print("=" * 100)
        for row, matches in pairs:
            print(f"  TO-APPLY  {_fmt(row)}")
            for m in matches[:4]:
                print(f"     ALREADY {m.get('status','?'):<10} {_fmt(m)}")
            if len(matches) > 4:
                print(f"     ... and {len(matches) - 4} more")
            print()

    print()
    print("SUMMARY")
    print(f"  To apply rows scanned .................. {len(to_apply)}")
    print(f"  Already-actioned rows compared against . {len(actioned)}")
    print(f"  Tier C (same requisition) .............. {len(tiers['C'])}   <- expected 0")
    print(f"  Tier A (real duplicates) ............... {len(tiers['A'])}")
    print(f"  Tier B (different city, not duplicates)  {len(tiers['B'])}")
    print()
    print("Read-only. Nothing was written, moved, hidden or deleted.")
    if tiers["C"]:
        print("WARNING: Tier C is non-empty. Re-read web/lib/dupes.ts:416-427 before "
              "drawing any conclusion from Tier A/B -- the assumption behind this "
              "audit's classification is wrong.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
