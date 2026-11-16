#!/usr/bin/env python3
"""Tesla's careers site, read from a home connection and handed to the ATS watcher.

Tesla's careers site sits behind Akamai, which refuses GitHub's runners outright:
every client fingerprint, and a real headless Chromium, Firefox and WebKit, got
"Access Denied" from a runner on 2026-11-16. A home connection that presents
Safari's TLS fingerprint gets the whole list in one 1.6 MB request. So this runs
on the owner's Mac (launchd, every few minutes), keeps the U.S. internships the
database has not seen, reads each one's description, and leaves them in bot_state
under tesla_relay. ats_watch.py picks them up on its next pass and classifies,
stores and pushes them like any other board.

Idempotent on purpose: every run writes the full set of postings still unknown to
the database, and the watcher's dedup skips anything already stored, so a relay
that runs twice, or on two machines, cannot push one posting twice.

Setup on a Mac: a venv with requirements.txt plus curl_cffi, SUPABASE_URL and
SUPABASE_SERVICE_KEY in ~/.config/job-bot/relay.env (or the file TESLA_RELAY_ENV
names), and a user LaunchAgent that runs this file with StartInterval 240.
    python tesla_relay.py            # one read, prints what it relayed
    python tesla_relay.py --dry-run  # read and print, write nothing
"""

import html
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

# The Mac keeps the two database variables in their own file, outside the repo.
load_dotenv(os.environ.get("TESLA_RELAY_ENV") or Path.home() / ".config" / "job-bot" / "relay.env")

from ats_sources import MAX_LEN, _make_job  # same 'ats:' + sha1(url) id as every board
from db import find_unknown_candidates, job_norm_key, set_state
from main import (_is_new_grad_role, _is_non_internship_title, _is_pure_coop_title,
                  _is_senior_role)

log = logging.getLogger("tesla_relay")

STATE_URL = "https://www.tesla.com/cua-api/apps/careers/state"
DETAIL_URL = "https://www.tesla.com/cua-api/careers/job/{id}"
PUBLIC_URL = "https://www.tesla.com/careers/search/job/{id}"
RELAY_KEY = "tesla_relay"
# Measured from a home connection on 2026-11-16: Safari's fingerprint gets 200,
# Chrome's gets 403. safari_ios is a second try, not a known-good one.
IMPERSONATE = ("safari", "safari_ios")
HEADERS = {"Accept": "application/json", "Referer": "https://www.tesla.com/careers/search/"}
INTERN_TYPE = 3          # lookup.types: 1 fulltime, 2 parttime, 3 intern, 4 seasonal
MAX_DESCRIPTIONS = 15    # per run; more than this waits for the next run (keeps a backlog gentle on Tesla)
DETAIL_PAUSE_S = 2.0


def _get(url: str):
    """One JSON GET with a Safari fingerprint. Raises with the last status on failure."""
    from curl_cffi import requests as cffi  # imported here so CI's offline tests never need it

    last = "no attempt"
    for imp in IMPERSONATE:
        try:
            resp = cffi.get(url, impersonate=imp, headers=HEADERS, timeout=30)
        except Exception as exc:
            last = f"{imp}: {type(exc).__name__}: {exc}"
            continue
        if resp.status_code == 200:
            return resp.json()
        last = f"{imp}: HTTP {resp.status_code}"
    raise RuntimeError(f"Tesla refused the read ({last})")


def us_locations(state: dict) -> dict[str, str]:
    """location id -> "City, ST" for every U.S. location in the list's geo tree."""
    out: dict[str, str] = {}
    for region in state.get("geo") or []:
        for site in region.get("sites") or []:
            if site.get("id") != "US":
                continue
            for st in site.get("states") or []:
                for city, ids in (st.get("cities") or {}).items():
                    for loc_id in ids or []:
                        out[str(loc_id)] = f"{city}, {st.get('id')}"
    return out


def internships(state: dict) -> list[dict]:
    """U.S. internship postings as watcher-shaped job dicts (no descriptions yet)."""
    where = us_locations(state)
    jobs = []
    for item in state.get("listings") or []:
        title = (item.get("t") or "").strip()
        if item.get("y") != INTERN_TYPE and "intern" not in title.lower():
            continue
        loc = where.get(str(item.get("l")))
        if loc is None:  # not in the U.S.
            continue
        job = _make_job("Tesla", title, loc, PUBLIC_URL.format(id=item.get("id")), None, None)
        if job:
            job["search_term"] = "tesla-relay"
            jobs.append(job)
    return jobs


_BREAK = re.compile(r"(?i)<\s*(?:br|/p|/li|/div|/h\d|/ul)\s*/?>")
_TAG = re.compile(r"<[^>]+>")


def _text(*parts) -> str:
    s = "\n".join(p for p in parts if isinstance(p, str) and p)
    s = html.unescape(_TAG.sub(" ", _BREAK.sub("\n", s)))
    s = re.sub(r"[ \t  ]+", " ", s)
    return re.sub(r"\s*\n\s*", "\n", s).strip()


def _would_be_classified(title: str) -> bool:
    """Mirrors the watcher's title gates: only these are worth a description read."""
    return not (_is_senior_role(title) or _is_new_grad_role(title)
                or _is_non_internship_title(title) or _is_pure_coop_title(title))


def _tesla_id(job: dict) -> str:
    return job["url"].rstrip("/").rsplit("/", 1)[-1]


def collect(state: dict) -> tuple[list[dict], int, int]:
    """(jobs to relay this run, U.S. internships listed, how many of those are unseen).
    Reads descriptions for the new ones, MAX_DESCRIPTIONS per run."""
    jobs = internships(state)
    for job in jobs:
        job["norm_key"] = job_norm_key(job)
    unknown = find_unknown_candidates(jobs)
    out, read = [], 0
    for job in jobs:
        if job["id"] not in unknown:
            continue
        if _would_be_classified(job["title"]):
            if read >= MAX_DESCRIPTIONS:
                continue  # relayed on a later run, with its description
            read += 1
            try:
                d = _get(DETAIL_URL.format(id=_tesla_id(job)))
                desc = _text(d.get("jobDescription"), d.get("jobResponsibilities"),
                             d.get("jobRequirements"), d.get("jobCompensationAndBenefits"))
                job["description"] = desc[:MAX_LEN] or None
                if d.get("location") and d.get("state"):
                    # The detail is the posting's own city; the list's location id can
                    # be a hub (a Bellevue role filed under Palo Alto). Some come in capitals.
                    city = d["location"].split(",")[0].strip()
                    job["location"] = f"{city.title() if city.isupper() else city}, {d['state']}"
            except Exception as exc:  # classified on its title alone, as other boards' are
                log.warning("No description for %s (%s): %s", job["id"], job["title"], exc)
            time.sleep(DETAIL_PAUSE_S)
        job.pop("norm_key", None)
        out.append(job)
    return out, len(jobs), len(unknown & {j["id"] for j in jobs})


def run(dry_run: bool = False) -> dict:
    state = _get(STATE_URL)
    jobs, listed, unseen = collect(state)
    payload = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "listed": listed, "unseen": unseen, "jobs": jobs}
    if listed == 0:
        # The list came back but held no U.S. internships at all: a format change,
        # not a quiet day (there were 324 on 2026-11-16). Keep the old payload.
        raise RuntimeError(f"Tesla's list had no U.S. internships ({len(state.get('listings') or [])} listings)")
    if not dry_run:
        set_state(RELAY_KEY, json.dumps(payload))
    log.info("Tesla: %d U.S. internships listed, %d not yet in the database, %d relayed this run%s",
             listed, unseen, len(jobs), " (dry run, nothing written)" if dry_run else "")
    for job in jobs:
        log.info("  %s | %s | %s", job["title"], job["location"],
                 "with description" if job.get("description") else "title only")
    return payload


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        run(dry_run="--dry-run" in sys.argv)
    except Exception as exc:
        log.error("%s", exc)
        sys.exit(1)
