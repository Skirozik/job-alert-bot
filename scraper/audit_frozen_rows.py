"""READ-ONLY: what exactly is frozen in the old tier vocabulary?

The Aug 15 rename (9bb39e6) replaced APPLY/MAYBE/SKIP with
APPLY/APPLY_CAVEAT/INELIGIBLE in code only -- stored rows were never remapped.
audit_recent_pushes found 23 MAYBE and 6,672 SKIP survivors. A status='new'
MAYBE row is permanently frozen: never re-classified (only PENDING falls
through main.py's already-stored short-circuit), invisible on the dashboard
(page.tsx fetches tier=in.(APPLY,APPLY_CAVEAT)), excluded from the digest.
That is the "silent delete" the rename was written to kill -- and per the note
at classifier.py:169, a chunk of historical MAYBEs were actually APPLY-grade.

This lists every MAYBE row in full (23 rows is nothing against the egress
budget) with a best-effort liveness probe of its posting URL, and a
status census of the SKIP rows via count headers (6,672 rows are NOT
downloaded). It decides nothing; what to do with the answer is a separate
decision.

READ-ONLY against the database: no update/delete/insert/upsert, no write RPC,
and it never reads sys.argv. The only network writes are GET requests to the
job posting URLs themselves, to see whether they still resolve.

Run from the scraper directory:
    cd scraper && python audit_frozen_rows.py
"""

import logging
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger(__name__)

# A plain requests default UA gets an instant 403 from most ATS frontends;
# a browser UA turns the probe into ordinary traffic.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def _probe(url: str) -> str:
    """Best-effort: does this posting URL still resolve? Evidence, not verdict.

    LinkedIn and some ATS hosts serve logged-out interstitials with a 200, so
    '200' here means "did not obviously die", never "still open". 404/410 and
    a redirect to a careers search page are the reliable signals, and they
    only appear on the negative side -- which is the side that matters for
    writing a row off.
    """
    if not url:
        return "no url"
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=10,
                            allow_redirects=True, stream=True)
        resp.close()
        note = f"HTTP {resp.status_code}"
        if resp.history and resp.url.rstrip("/") != url.rstrip("/"):
            from urllib.parse import urlparse
            note += f" -> {urlparse(resp.url).netloc}{urlparse(resp.url).path[:40]}"
        return note
    except requests.RequestException as exc:
        return f"unreachable ({type(exc).__name__})"


def _count(q) -> int:
    res = q.limit(1).execute()
    return res.count or 0


def main() -> int:
    client = get_client()

    rows = (client.table("jobs")
            .select("id,company,title,location,status,found_at,url,apply_url,reason")
            .eq("tier", "MAYBE").order("found_at").execute().data) or []
    print()
    print(f"ALL {len(rows)} MAYBE ROWS -- the frozen ones. status='new' means nobody")
    print("has ever seen it: not the dashboard, not the digest, not a push.")
    print("=" * 96)
    for r in rows:
        live = _probe(r.get("apply_url") or r.get("url"))
        print(f"  {r.get('found_at','')[:10]}  [{r.get('status','?'):<9}] "
              f"{(r.get('company') or '?')[:24]:<24} {(r.get('title') or '')[:44]:<44}")
        print(f"             posting: {live:<40} id={r['id']}")
        if r.get("reason"):
            print(f"             old verdict: {r['reason'][:78]}")
    frozen = sum(1 for r in rows if r.get("status") == "new")
    print(f"\n  {frozen} of {len(rows)} are status='new' -- frozen and invisible.")

    print()
    print("SKIP ROWS -- census only, none downloaded")
    total = _count(client.table("jobs").select("id", count="exact").eq("tier", "SKIP"))
    new = _count(client.table("jobs").select("id", count="exact")
                 .eq("tier", "SKIP").eq("status", "new"))
    print(f"  total {total}, of which status='new' {new}, user-acted {total - new}")
    print("  (SKIP rows were never meant to alert; the question for them is only")
    print("   whether to remap the label to INELIGIBLE so one vocabulary remains.)")

    print()
    print("Read-only against the database. Nothing was written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
