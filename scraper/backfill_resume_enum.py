"""One-off backfill: coerce suggested_resume values that were never valid.

The classify_job tool schema has always declared this field as an enum of
Mobile | AI | Frontend | General, but Anthropic tool enums are guidance rather
than a hard constraint. A live check on 2026-08-23 found 39 rows holding "N/A"
and 1 holding "1Password" -- the model echoing the company name straight into
the field. `tier` was validated on the way out of the tool call; this was not.

classifier.py now coerces on write, so this only repairs history.

SAFETY: writes exactly one column. `status` is never in the patch -- it holds
the record of 531 applications.

    cd scraper && python backfill_resume_enum.py
    cd scraper && python backfill_resume_enum.py --apply
"""

import argparse
import json
import logging
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger(__name__)

VALID = ("Mobile", "AI", "Frontend", "General")


def run(write: bool) -> None:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/jobs"
    key = os.environ["SUPABASE_SERVICE_KEY"]
    H = {"apikey": key, "Authorization": "Bearer " + key, "Accept": "application/json"}

    def call(method, params, body=None):
        h = dict(H)
        if body is not None:
            h["Content-Type"] = "application/json"
            h["Prefer"] = "return=representation"
        req = urllib.request.Request(
            base + "?" + urllib.parse.urlencode(params), headers=h, method=method,
            data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else []

    # `not.in` already excludes NULLs: in SQL, NULL NOT IN (...) evaluates to
    # NULL, so those rows never match. That is exactly what we want here --
    # NULLs are absent values rather than wrong ones, and db.py defaults them
    # on write. No separate is-null filter needed (and adding one as a second
    # `suggested_resume` param is a 400).
    quoted = ",".join('"%s"' % v for v in VALID)
    bad = call("GET", {"select": "id,company,title,suggested_resume,status",
                       "suggested_resume": "not.in.(%s)" % quoted, "limit": "1000"})

    log.info("rows with an out-of-enum suggested_resume: %d", len(bad))
    for r in bad:
        log.info("  %-12r %s - %s", r["suggested_resume"], r["company"][:26], (r["title"] or "")[:42])

    if not bad:
        log.info("nothing to do")
        return
    if not write:
        log.info("DRY RUN - re-run with --apply to write %d rows to 'General'", len(bad))
        return

    for r in bad:
        patch = {"suggested_resume": "General"}
        assert "status" not in patch
        call("PATCH", {"id": "eq.%s" % r["id"]}, patch)
    log.info("updated %d rows to 'General'", len(bad))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    run(ap.parse_args().apply)
