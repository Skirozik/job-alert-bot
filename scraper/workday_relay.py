#!/usr/bin/env python3
"""The Workday boards, read from a home connection and handed to the ATS watcher.

Every Workday board in ats_config.py (44 of them: Nvidia, Salesforce, Intel, Capital
One, Cisco, Adobe, Boeing, Wells Fargo, Mastercard, ...) has answered HTTP 429 to
GitHub's runners on every request since 2026-11-16, while a home connection reads
them without trouble. Over the 45 days before that, those boards produced more
apply-worthy postings than any other board type, so this runs on the owner's Mac
(launchd, every five minutes) the way tesla_relay.py does. It reads the boards,
keeps the postings the database has not seen, stores the ones a title rules out
(ats_watch.title_gate: seniority, new grad, no internship marker) as INELIGIBLE
itself, reads a description for the rest, and leaves those in bot_state under
workday_relay. ats_watch.py picks them up on its next pass and classifies, stores
and pushes them like any other board, and while this relay is fresh the runner
leaves the Workday boards alone.

Gentle on purpose. Tesla's careers site blocked this same connection on 2026-11-19
after three days of five-minute reads, so in the steady state a pass costs each
board one request: the board is read newest first, one page (20 postings) at a
time, and stops at the first page the database already knows in full. The boards
that are not sorted newest first (ats_sources.WORKDAY_UNSORTED_COMPANIES plus
whatever the last full read found) are read in full every UNSORTED_EVERY_MIN
minutes instead, and every board is read in full once a UTC day, after
FULL_HOUR_UTC, as the backstop for anything the short reads can miss.

Idempotent like the Tesla relay: every run writes the full set of postings still
unknown to the database, and the watcher's dedup skips anything already stored.

Setup on a Mac: the venv tesla_relay.py uses, SUPABASE_URL and SUPABASE_SERVICE_KEY
in ~/.config/job-bot/relay.env (or the file JOB_BOT_RELAY_ENV names), and a user
LaunchAgent that runs this file with StartInterval 300.
    python workday_relay.py            # one pass, prints what it relayed
    python workday_relay.py --dry-run  # read and print, write nothing
    python workday_relay.py --full     # read every board in full this pass
"""

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

# The Mac keeps the two database variables in their own file, outside the repo.
load_dotenv(os.environ.get("JOB_BOT_RELAY_ENV") or os.environ.get("TESLA_RELAY_ENV")
            or Path.home() / ".config" / "job-bot" / "relay.env")

from ats_config import ATS_COMPANIES
from ats_sources import MAX_LEN, fetch_workday_listings, iter_workday_pages
from ats_watch import _always_full_tokens, _record_unsorted, title_gate
from db import StateUnavailable, find_unknown_candidates, insert_jobs, job_norm_key, read_state, set_state
from external_descriptions import _fetch_workday
from main import _freshest_first, _is_pure_coop_title
from target_key import definitive_target_key

log = logging.getLogger("workday_relay")

RELAY_KEY = "workday_relay"
FULL_DAY_KEY = "workday_relay_full_day"        # the UTC day of the last full read of every board
UNSORTED_AT_KEY = "workday_relay_unsorted_at"  # when the unsorted boards were last read in full
FULL_HOUR_UTC = 8          # same hour as the watcher's own daily full sweep: 3 to 4 AM Eastern
UNSORTED_EVERY_MIN = 30
SHORT_MAX_PAGES = 10       # a short read gives up after 200 postings; the daily full read has the rest
WORKERS = 4                # boards read at once; each board is a different host
MAX_DESCRIPTIONS = 25      # per run; more than this waits for the next run
DETAIL_PAUSE_S = 1.0


def boards() -> dict[str, str]:
    """company -> token for every Workday board in the config."""
    return {name: cfg["token"] for name, cfg in ATS_COMPANIES.items() if cfg["platform"] == "workday"}


def _keyed(jobs: list[dict]) -> list[dict]:
    for job in jobs:
        job["norm_key"] = job_norm_key(job)
    return jobs


def read_board(company: str, token: str, full: bool, sort_report: dict | None = None) -> tuple[list[dict], int, int]:
    """(postings the database has not seen, postings read, pages read) for one board.

    A full read takes every page and asks the dedup question once. A short read
    goes newest first, one page at a time, asking after each page, and stops at the
    first page with nothing new on it: on a newest-first board everything below is
    older still, and already stored.
    """
    if full:
        jobs = _keyed(fetch_workday_listings(company, token, full=True, sort_report=sort_report))
        unknown = find_unknown_candidates(jobs) if jobs else set()
        return [j for j in jobs if j["id"] in unknown], len(jobs), -(-len(jobs) // 20)

    out: list[dict] = []
    read = pages = 0
    for jobs, _ages in iter_workday_pages(company, token):
        pages += 1
        read += len(jobs)
        unknown = find_unknown_candidates(_keyed(jobs)) if jobs else set()
        out.extend(j for j in jobs if j["id"] in unknown)
        if not unknown or pages >= SHORT_MAX_PAGES:
            break
    return out, read, pages


def collect(full: bool, full_tokens: frozenset = frozenset()) -> dict:
    """Read every board, a few at a time. full reads them all in full; otherwise only
    the boards whose token is in full_tokens are. Returns the unknown postings with
    duplicates within the batch dropped (same id, same norm_key or same application
    target: the rule ats_watch._sweep applies), and the counts for the log."""
    sort_report: dict = {}
    found: list[dict] = []
    listed = answered = failed = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(read_board, company, token, full or token in full_tokens, sort_report): company
                   for company, token in boards().items()}
        for future in as_completed(futures):
            company = futures[future]
            try:
                rows, read, pages = future.result()
            except Exception as exc:
                log.error("  %s: read failed (%s)", company, exc)
                failed += 1
                continue
            if read == 0:
                failed += 1  # answered nothing: refused, down, or empty, none of which is a read
            else:
                answered += 1
            listed += read
            found.extend(rows)
            log.info("  %s: %d postings on %d page(s), %d not yet stored", company, read, pages, len(rows))

    seen_ids: set[str] = set()
    seen_norm_keys: set[str] = set()
    seen_targets: set[str] = set()
    jobs: list[dict] = []
    for job in found:
        target = definitive_target_key(job)
        if job["id"] in seen_ids or job["norm_key"] in seen_norm_keys or (target and target in seen_targets):
            continue
        seen_ids.add(job["id"])
        seen_norm_keys.add(job["norm_key"])
        if target:
            seen_targets.add(target)
        jobs.append(job)
    return {"jobs": jobs, "listed": listed, "answered": answered, "failed": failed, "sort_report": sort_report}


def split(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    """(rows a title alone rules out, carrying their INELIGIBLE verdict; rows for the model)."""
    ineligible: list[dict] = []
    to_classify: list[dict] = []
    for job in jobs:
        reason = title_gate(job["title"])
        if reason is None:
            to_classify.append(job)
            continue
        job["tier"] = "INELIGIBLE"
        job["reason"] = reason
        job["suggested_resume"] = "General"
        ineligible.append(job)
    return ineligible, to_classify


def describe(jobs: list[dict], fetch=_fetch_workday) -> list[dict]:
    """The rows to relay this run, newest first, with a description read for each one
    the model will see. Past MAX_DESCRIPTIONS a posting waits for the next run rather
    than going over title only. A pure co-op title is relayed without a read: the
    watcher stores it without a model call."""
    out: list[dict] = []
    for job in _freshest_first(jobs):
        if len(out) >= MAX_DESCRIPTIONS:
            break
        if not _is_pure_coop_title(job["title"]):
            try:
                text = fetch(job["url"])
                job["description"] = (text or "")[:MAX_LEN] or None
            except Exception as exc:  # classified on its title alone, as other boards' are
                log.warning("No description for %s (%s): %s", job["id"], job["title"], exc)
            time.sleep(DETAIL_PAUSE_S)
        out.append(job)
    return out


def full_due(now: datetime) -> bool:
    """Once a UTC day, on the first run at or after FULL_HOUR_UTC."""
    if now.hour < FULL_HOUR_UTC:
        return False
    try:
        return read_state(FULL_DAY_KEY) != now.date().isoformat()
    except StateUnavailable as exc:
        log.warning("Full-read state unreadable (%s); short read this run", exc)
        return False


def unsorted_due(now: datetime) -> bool:
    """Every UNSORTED_EVERY_MIN minutes, and on the first run."""
    try:
        raw = read_state(UNSORTED_AT_KEY)
    except StateUnavailable as exc:
        log.warning("Unsorted-read state unreadable (%s); short read this run", exc)
        return False
    if not raw:
        return True
    try:
        return (now - datetime.fromisoformat(raw)).total_seconds() >= UNSORTED_EVERY_MIN * 60
    except ValueError:
        return True


def run(dry_run: bool = False, force_full: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    full = force_full or full_due(now)
    full_tokens = _always_full_tokens() if (not full and unsorted_due(now)) else frozenset()
    log.info("Workday relay: %s read of %d boards%s", "FULL" if full else "short", len(boards()),
             f", {len(full_tokens)} unsorted board(s) in full" if full_tokens else "")

    got = collect(full, full_tokens)
    if got["answered"] == 0:
        # Every board answered nothing: this connection is being refused, or is
        # offline. Keep the old payload rather than report an empty read.
        raise RuntimeError(f"no Workday board answered ({got['failed']} tried)")

    ineligible, to_classify = split(got["jobs"])
    relayed = describe(to_classify) if to_classify else []
    for job in relayed + ineligible:
        job.pop("norm_key", None)  # the watcher and insert_jobs compute their own
    payload = {"at": now.isoformat(timespec="seconds"), "listed": got["listed"], "boards": got["answered"],
               "failed": got["failed"], "unseen": len(got["jobs"]), "jobs": relayed}
    if not dry_run:
        if ineligible:
            insert_jobs(ineligible)
        set_state(RELAY_KEY, json.dumps(payload))
        if full:
            set_state(FULL_DAY_KEY, now.date().isoformat())
        if full or full_tokens:
            set_state(UNSORTED_AT_KEY, now.isoformat(timespec="seconds"))
        if got["sort_report"]:
            _record_unsorted(got["sort_report"])

    log.info("Workday: %d boards answered (%d did not), %d postings read, %d not yet in the database: "
             "%d stored as ineligible on their titles, %d relayed this run, %d waiting%s",
             got["answered"], got["failed"], got["listed"], len(got["jobs"]), len(ineligible), len(relayed),
             len(to_classify) - len(relayed), " (dry run, nothing written)" if dry_run else "")
    for job in relayed:
        log.info("  %s | %s | %s | %s", job["company"], job["title"], job["location"],
                 "with description" if job.get("description") else "title only")
    return payload


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        run(dry_run="--dry-run" in sys.argv, force_full="--full" in sys.argv)
    except Exception as exc:
        log.error("%s", exc)
        sys.exit(1)
