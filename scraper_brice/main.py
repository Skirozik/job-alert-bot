"""Brice pipeline -- entry point.  Run:  cd scraper_brice && python main.py [--dry-run ...]
Env: ../.env.brice locally, repository secrets in Actions (.github/workflows/scrape_brice.yml).

A full-time, entry-level search across the United States from three sources,
collected first and then processed under per-source caps and a time budget:

  start_run (75-min lock; fails CLOSED on a database error -> exit 1 + owner alert)
  COLLECT -- listing fields only; nothing fetched per job, nothing sent to Claude
    A. ATS       ats_pass.collect_ats_candidates(): 26 company boards through
                 title_gate.source_gate, then run-level dedup and a DB lookup.
    B. LinkedIn  22 terms x "United States", <=10 pages, per-page DB lookup; a
                 search stops after ALL_DUP_PAGES_TO_STOP consecutive
                 all-duplicate pages (not while LI_LEFTOVER_KEY is fresh)
                 or a partial page; each new title goes through
                 title_gate.gate(). Its own 25-minute budget.
    C. jobright  five README lists (never jobright.ai): conditional GET with
                 the stored ETag, parse, source_gate, dedup.
  PROCESS -- stop starting work at RUN_TIME_BUDGET_S
    1. LinkedIn titles the gate dropped are stored INELIGIBLE "Pre-filtered: <rule>"
    2-4. ATS, LinkedIn, jobright queues, each capped by MAX_CLASSIFY_PER_RUN:
         already-stored guard -> description -> classify -> store -> ping
    5. retry_pending: PENDING rows parked by earlier runs
    6. jobright ETags saved only for lists that left nothing behind
    7. LI_LEFTOVER_KEY stamped if LinkedIn jobs were left unstored, cleared
       after a complete scan that left none
  finish_run(stats), then owner alerts (throttled through bot_state)

ATS first, so the in-run dedup keeps the richest copy of a posting (direct
apply link, full description); jobright rows are title-only and lose to both.

Pings: APPLY sounds, APPLY_CAVEAT is silent (notifier.py). The first run pings
everything it classifies -- there is no silent seed. Infrastructure alerts go
to the owner's topic only. No per-job line logs a tier, a reason or a ping:
the log shows each title, so a verdict beside it would publish the rubric.

--dry-run touches no database, no Claude and no ntfy, needs no secrets, keeps
every LinkedIn request >= 5 s from the previous one and prints what a run
would do.
"""

import argparse
import logging
import random
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env.brice")

# Titles can contain emoji and other non-ASCII characters; on Windows stdout
# defaults to a legacy code page and logging would drop those lines.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import ats_pass  # noqa: E402
import classifier  # noqa: E402
import config  # noqa: E402  (read after load_dotenv: config reads the environment at import)
import db  # noqa: E402
import jobright  # noqa: E402
import linkedin  # noqa: E402
import notifier  # noqa: E402
import title_gate  # noqa: E402
from db import make_norm_key  # noqa: E402  (a pure function: safe in a dry run)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
# These print full request URLs at INFO, and Actions logs are public.
for _noisy in ("httpx", "httpcore", "hpack", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# Every clock read goes through here so tests can drive the budgets.
_monotonic = time.monotonic

SOURCES = ("ats", "linkedin", "jobright")          # collection and processing order
ACTIONABLE = ("APPLY", "APPLY_CAVEAT")

# The keys finish_run writes -- exactly the stat columns of scrape_runs in
# scraper_brice/schema.sql. An unknown key fails the whole UPDATE and leaves the
# run-lock held for db.RUN_LOCK_MINUTES.
FINISH_RUN_KEYS = ("total_raw", "new_jobs", "notified", "rate_limited", "ats_candidates", "jobright_candidates",
                   "classified", "failed", "leftover")

PARKED_REASON = "Awaiting classification — Claude API unavailable when this job was found"
DOWN_ALERT_KEY = "classifier_down_alert_at"          # also read by retry_pending's recovery notice
ETAG_KEY_PREFIX = "jobright_etag:"
# When a run last left LinkedIn jobs unstored (cap, time, a failed write). While it is younger than the 24 h
# lookback, every search pages past stored results instead of stopping after ALL_DUP_PAGES_TO_STOP of them:
# a later run sees a search's first pages fully stored and would otherwise never reach the leftovers behind them.
LI_LEFTOVER_KEY = "linkedin_leftover_at"

_LINKEDIN_PACE = (2.0, 3.5)          # between search pages and searches (scraper/main.py)
_LINKEDIN_RETRY_PACE = (1.5, 2.5)    # before re-asking for an empty page
_DRY_LINKEDIN_PACE = (5.0, 6.0)      # dry run: every LinkedIn request >= 5 s after the previous one
_WORKDAY_PACE = (0.5, 1.0)           # between Workday description GETs
_JOBRIGHT_PACE = (1.0, 2.0)          # between README GETs

DRY_ONLY_DEFAULTS = {"no_linkedin": False, "no_ats": False, "no_jobright": False,
                     "linkedin_pages": None, "linkedin_terms": None, "sample": None}


class RunState:
    """Everything one run counts, and the in-run dedup sets."""

    def __init__(self, dry_run: bool = False, linkedin_pages: Optional[int] = None,
                 linkedin_terms: Optional[int] = None):
        self.dry_run = dry_run
        self.linkedin_pages = linkedin_pages or config.MAX_PAGES_PER_SEARCH
        self.linkedin_terms = linkedin_terms
        self.t0 = _monotonic()
        self._order = 0
        # a posting seen from ANY source this run (first copy wins) / on LinkedIn only
        self.run_seen_ids: set[str] = set()
        self.run_seen_nks: set[str] = set()
        self.li_seen_ids: set[str] = set()
        self.li_seen_nks: set[str] = set()
        self.li = {"searches": 0, "rate_limited": 0, "errors": 0, "total_raw": 0, "new": 0, "to_classify": 0,
                   "terms": 0, "prefiltered": Counter(), "skipped_terms": []}
        self.li_marker: Optional[str] = None   # LI_LEFTOVER_KEY as this run found it
        self.li_deep = False                   # page past stored results (a fresh marker)
        self.li_unstored = 0                   # LinkedIn jobs this run found and did not store
        self.ats = {"boards": 0, "boards_with_listings": 0, "listings": 0, "kept": 0, "candidates": 0,
                    "dropped_by": Counter(), "dropped_samples": {}, "by_board": {}, "seconds": 0.0,
                    "cut_short": False}
        self.jobright: dict[str, dict] = {}
        self.used = Counter()          # classify() calls per source (the cap counts these)
        self.leftover = Counter()      # per source: candidates not processed (cap or time), not stored
        self.leftover_why: dict[str, str] = {}
        self.leftover_lists = Counter()  # jobright leftovers per list (decides the ETag)
        self.attempted = 0             # classify() calls, fresh + PENDING retries
        self.succeeded = 0
        self.failures = Counter()      # classify() failures by kind
        self.parked = Counter()        # rows actually written as PENDING, by kind
        self.hard_down = Counter()     # billing/auth failures seen this run
        self.pushed = 0
        self.push_attempts = 0         # pings tried; pushed counts the ones ntfy accepted
        self.quota_hit = False

    def next_order(self) -> int:
        self._order += 1
        return self._order

    def elapsed(self) -> float:
        return _monotonic() - self.t0

    @property
    def jobright_candidates(self) -> int:
        return sum(info["new"] for info in self.jobright.values())


# ── collection ───────────────────────────────────────────────────────────────

def _seen_in_run(state: RunState, job: dict) -> bool:
    """True if any source already produced this posting this run; otherwise mark it seen."""
    if job["id"] in state.run_seen_ids or job["norm_key"] in state.run_seen_nks:
        return True
    state.run_seen_ids.add(job["id"])
    state.run_seen_nks.add(job["norm_key"])
    return False


def _new_candidates(state: RunState, jobs: list[dict], source: str) -> list[dict]:
    """Run-level dedup, then the stored-row lookup (skipped in a dry run). Order is kept."""
    fresh = []
    for job in jobs:
        job["source"] = source
        job["norm_key"] = job.get("norm_key") or make_norm_key(job.get("company", ""), job.get("title", ""))
        if not _seen_in_run(state, job):
            fresh.append(job)
    known_ids, known_nks = (set(), set()) if state.dry_run or not fresh else db.find_known_candidates(fresh)
    out = []
    for job in fresh:
        if job["id"] in known_ids or job["norm_key"] in known_nks:
            continue
        job["_order"] = state.next_order()
        out.append(job)
    return out


def collect_ats(state: RunState) -> list[dict]:
    """ATS pass (SPEC section 8). Integration point: ats_pass.collect_ats_candidates()
    returns (candidates, stats) -- rows that already passed title_gate.source_gate,
    with id "ats:...", family and search_term set; stats carries boards,
    boards_with_listings, listings, kept and dropped_by (optionally dropped_samples
    and by_board, for the dry-run report).
    """
    started = _monotonic()
    candidates, stats = ats_pass.collect_ats_candidates()
    state.ats["seconds"] = _monotonic() - started
    for key in ("boards", "boards_with_listings", "listings", "kept"):
        state.ats[key] = int(stats.get(key) or 0)
    state.ats["dropped_by"] = Counter(stats.get("dropped_by") or {})
    state.ats["dropped_samples"] = dict(stats.get("dropped_samples") or {})
    state.ats["by_board"] = dict(stats.get("by_board") or {})
    state.ats["cut_short"] = bool(stats.get("cut_short"))
    queue = _new_candidates(state, list(candidates), "ats")
    state.ats["candidates"] = len(queue)
    log.info("ATS: %d listings from %d/%d boards | kept by the gate %d | new %d",
             state.ats["listings"], state.ats["boards_with_listings"], state.ats["boards"],
             len(candidates), len(queue))
    return queue


def _fetch_page(term: str, location: str, start: int):
    return linkedin.fetch_listings(term, location, config.LOOKBACK_SECONDS, start=start)


def scan_linkedin(state: RunState) -> list[dict]:
    """LinkedIn pass (SPEC section 10.2): new postings with job["gate"] = the rule
    that drops the title, or None when it goes to Claude.

    The search budget runs from this pass's own start, so a slow ATS sweep
    cannot spend it; RUN_TIME_BUDGET_S still stops new searches. With
    state.li_deep (a fresh LI_LEFTOVER_KEY) a search is not stopped by
    all-stored pages, so jobs an earlier run left behind are reached again."""
    pace = _DRY_LINKEDIN_PACE if state.dry_run else _LINKEDIN_PACE
    retry_pace = _DRY_LINKEDIN_PACE if state.dry_run else _LINKEDIN_RETRY_PACE
    terms = list(config.SEARCH_TERMS)
    if state.linkedin_terms:
        terms = terms[:state.linkedin_terms]
    stats = state.li
    stats["terms"] = len(terms)
    new_jobs: list[dict] = []
    first_request = True
    search_t0 = _monotonic()

    for index, term in enumerate(terms):
        if _monotonic() - search_t0 >= config.SEARCH_TIME_BUDGET_S or not _time_left(state):
            stats["skipped_terms"] = terms[index:]
            log.warning("Search time budget spent — skipping %d term(s): %s",
                        len(stats["skipped_terms"]), ", ".join(stats["skipped_terms"]))
            break
        for location in config.LOCATIONS:
            if not first_request:
                time.sleep(random.uniform(*pace))
            stats["searches"] += 1
            log.info("Searching: '%s' in %s", term, location)
            consecutive_dup = 0

            for page in range(state.linkedin_pages):
                start = page * 10
                if page:
                    time.sleep(random.uniform(*pace))
                first_request = False
                jobs, err = _fetch_page(term, location, start)
                if err == "rate_limited":
                    stats["rate_limited"] += 1
                    log.warning("  p%d: rate limited — stopping pagination", page)
                    break
                if err:
                    stats["errors"] += 1
                    log.error("  p%d: error — %s", page, err)
                    break
                if not jobs:
                    # The guest endpoint intermittently returns an empty page for
                    # a request that succeeds seconds later.
                    time.sleep(random.uniform(*retry_pace))
                    jobs, err = _fetch_page(term, location, start)
                    if err == "rate_limited":
                        stats["rate_limited"] += 1
                    elif err:
                        stats["errors"] += 1
                    if err or not jobs:
                        log.info("  p%d: 0 listings on retry too — done", page)
                        break
                    log.info("  p%d: 0 listings on first try, %d on retry — continuing", page, len(jobs))

                stats["total_raw"] += len(jobs)
                for job in jobs:
                    job["norm_key"] = make_norm_key(job.get("company", ""), job.get("title", ""))
                db_ids, db_nks = (set(), set()) if state.dry_run else db.find_known_candidates(jobs)

                all_dup = True
                page_new = 0
                for job in jobs:
                    nk = job["norm_key"]
                    stored = job["id"] in db_ids or nk in db_nks
                    li_seen = job["id"] in state.li_seen_ids or nk in state.li_seen_nks
                    if not (stored or li_seen):
                        all_dup = False
                    state.li_seen_ids.add(job["id"])
                    state.li_seen_nks.add(nk)
                    if _seen_in_run(state, job) or stored:
                        continue
                    job.update(search_term=term, source="linkedin", family=None,
                               gate=title_gate.gate(job.get("title", "")), _order=state.next_order())
                    new_jobs.append(job)
                    page_new += 1

                log.info("  p%d (start=%d): %d listings, %d new", page, start, len(jobs), page_new)
                consecutive_dup = consecutive_dup + 1 if all_dup else 0
                if consecutive_dup >= config.ALL_DUP_PAGES_TO_STOP and not state.li_deep:
                    log.info("  All duplicates in DB — stopping pagination")
                    break
                if len(jobs) < 10:
                    break        # a partial page is the last page

    stats["new"] = len(new_jobs)
    stats["prefiltered"] = Counter(j["gate"] for j in new_jobs if j["gate"])
    stats["to_classify"] = sum(1 for j in new_jobs if not j["gate"])
    log.info("Total raw: %d | New: %d | Rate limited: %d/%d searches",
             stats["total_raw"], stats["new"], stats["rate_limited"], stats["searches"])
    return new_jobs


def collect_jobright(state: RunState) -> list[dict]:
    """jobright pass (SPEC section 9), per config.JOBRIGHT_LISTS entry (jobright.py):
      jobright.fetch_readme(url, etag) -> (status, text, etag)   raw.githubusercontent.com only
      jobright.parse_readme(text, name, today) -> (rows, unparsed_link_rows)
      jobright.rows_to_jobs(rows, name, support_only=..., today=..., samples=...) -> (jobs, dropped_by)
      jobright.canary_problems(status, parsed_rows, unparsed_link_rows) -> [problem, ...]
    The ETag comes from bot_state (never in a dry run); a 304 yields no rows. Rows are README fields
    only: nothing is fetched per job, and never from jobright.ai.
    """
    today = datetime.now(timezone.utc).date()
    queue: list[dict] = []
    for index, (name, url, support_only) in enumerate(config.JOBRIGHT_LISTS):
        if index:
            time.sleep(random.uniform(*_JOBRIGHT_PACE))
        etag = None if state.dry_run else db.get_state(ETAG_KEY_PREFIX + name)
        status, text, new_etag = jobright.fetch_readme(url, etag)
        info = {"status": status, "etag": new_etag, "rows": 0, "unparsed": 0, "window": 0, "kept": 0, "new": 0,
                "dropped_by": Counter(), "dropped_samples": {}, "problems": []}
        state.jobright[name] = info
        if status == 304:
            log.info("jobright %s: not modified since the last complete read", name)
            continue
        rows, unparsed = jobright.parse_readme(text, name, today) if status == 200 else ([], 0)
        info["rows"], info["unparsed"] = len(rows), unparsed
        info["problems"] = list(jobright.canary_problems(status, len(rows), unparsed))
        jobs, dropped_by = jobright.rows_to_jobs(rows, name, support_only=support_only, today=today,
                                                 samples=info["dropped_samples"])
        info["kept"], info["dropped_by"] = len(jobs), Counter(dropped_by)
        info["window"] = len(rows) - info["dropped_by"].get(jobright.STALE_RULE, 0)
        new = _new_candidates(state, list(jobs), "jobright")
        for job in new:
            job["_list"] = name
        info["new"] = len(new)
        queue += new
        log.info("jobright %s: HTTP %s | %d rows (%d unparsed, %d in window) | kept %d | new %d%s", name, status,
                 len(rows), unparsed, info["window"], len(jobs), len(new),
                 f" | CANARY: {'; '.join(info['problems'])}" if info["problems"] else "")
    return queue


# ── processing ───────────────────────────────────────────────────────────────

def _queue_key(job: dict):
    """Entry-marked titles and the primary families first, then discovery order."""
    return (0 if title_gate.is_entry_marked(job.get("title", "")) else 1,
            title_gate.FAMILY_RANK.get(job.get("family"), 9), job.get("_order", 0))


def _time_left(state: RunState) -> bool:
    return state.elapsed() < config.RUN_TIME_BUDGET_S


def _unstored(state: RunState, job: dict) -> None:
    """A job whose write failed: found again by the next run (a LinkedIn one only while LI_LEFTOVER_KEY is fresh)."""
    if job.get("source") == "linkedin":
        state.li_unstored += 1


def _leave(state: RunState, source: str, jobs: list[dict], why: str) -> None:
    if not jobs:
        return
    state.leftover[source] += len(jobs)
    state.leftover_why[source] = why
    if source == "linkedin":
        state.li_unstored += len(jobs)
    for job in jobs:
        if job.get("_list"):
            state.leftover_lists[job["_list"]] += 1
    log.info("Leftover %s: %d (%s) — next run picks them up", source, len(jobs), why)


def store_prefiltered(state: RunState, jobs: list[dict]) -> None:
    """LinkedIn titles the gate dropped: stored INELIGIBLE so no later run re-reads them. Not capped."""
    for index, job in enumerate(jobs):
        if not _time_left(state):
            _leave(state, "linkedin", jobs[index:], "time")
            return
        log.info("Processing: '%s' @ %s [%s]", job.get("title"), job.get("company"), job["id"])
        log.info("  Pre-filter SKIP (%s)", job["gate"])
        job["tier"] = "INELIGIBLE"
        job["reason"] = f"Pre-filtered: {job['gate']}"
        if not db.insert_job(job):
            _unstored(state, job)


def _is_workday(url: Optional[str]) -> bool:
    try:
        return (urlparse(url or "").hostname or "").endswith(".myworkdayjobs.com")
    except ValueError:
        return False


def _describe(job: dict) -> None:
    """Fill in what the listing lacks, per source. Runs only after the already-stored guard."""
    source = job.get("source")
    if source == "linkedin":
        desc, logo_url, apply_url, is_easy_apply, salary = linkedin.fetch_description(job["id"])
        if desc:
            job["description"] = desc
            log.info("  Description: %d chars", len(desc))
        else:
            log.info("  No description — classifying on title/company/location")
        if logo_url:
            job["logo_url"] = logo_url
        job["is_easy_apply"] = job.get("is_easy_apply", False) or is_easy_apply
        if apply_url:
            job["apply_url"] = apply_url
        if salary:
            job["salary"] = salary
    elif source == "ats":
        if job.get("description"):
            log.info("  Description: %d chars", len(job["description"]))
        elif _is_workday(job.get("url")):
            # Workday listings carry no description: one GET to its CXS endpoint.
            time.sleep(random.uniform(*_WORKDAY_PACE))
            desc = ats_pass.fetch_workday_description(job["url"])
            if desc:
                job["description"] = desc
                log.info("  Description: %d chars", len(desc))
            else:
                log.info("  No description — classifying on title/company/location")
        else:
            log.info("  No description — classifying on title/company/location")
    else:
        # jobright: README fields only, by design (the bot never opens jobright.ai)
        log.info("  Title-only source — classifying on title/company/location")


def process_job(job: dict, state: RunState) -> str:
    """One queued job: guard, describe, classify, store, ping.
    Returns 'skipped', 'classified', 'pushed' or 'parked'."""
    log.info("Processing: '%s' @ %s [%s]", job.get("title"), job.get("company"), job["id"])

    # Already stored? Stop before a description fetch, a Claude call and -- the
    # part that reaches the phone -- a second ping. find_known_candidates fails
    # open, and insert_job's ON CONFLICT DO NOTHING reports success for a row
    # that already existed. A PENDING row falls through to be classified.
    existing = db.get_job_row(job["id"])
    pending_row = existing is not None and existing.get("tier") == "PENDING"
    if existing is not None and not pending_row:
        log.info("  Already stored — no re-fetch, no re-classify, no push")
        return "skipped"

    _describe(job)
    state.attempted += 1
    state.used[job["source"]] += 1
    result = classifier.classify(job)

    if result.get("failed"):
        kind = result.get("failed_kind", "transient")
        state.failures[kind] += 1
        if kind in ("billing", "auth"):
            state.hard_down[kind] += 1
        if pending_row:
            log.warning("  Classification FAILED (%s) — the row stays parked as PENDING", kind)
            return "parked"
        # PARK, don't drop: a dropped LinkedIn job is lost once it leaves the
        # lookback window. PENDING is a queue state, never a verdict: no ping.
        job["tier"] = "PENDING"
        job["reason"] = PARKED_REASON
        if db.insert_job(job):
            state.parked[kind] += 1
            log.warning("  Classification FAILED (%s) — parked as PENDING for automatic retry", kind)
        else:
            _unstored(state, job)
            log.error("  Classification FAILED (%s) and the park write failed too — job %s is not stored",
                      kind, job["id"])
        return "parked"

    state.succeeded += 1
    job["tier"] = result.get("tier", "APPLY_CAVEAT")
    job["reason"] = result.get("reason", "")
    new_salary = result.get("salary") if not job.get("salary") else None
    if new_salary:
        job["salary"] = new_salary
    # Neither the tier nor the reason is logged, and neither is a ping: Actions logs are public, and a title
    # printed next to its verdict lets a reader infer private rubric rules (a graduation window, a clearance
    # rule) from which titles pass. Look them up by id; the run summary has the totals.
    log.info("  -> classified | id=%s", job["id"])

    if pending_row:
        # insert_job would change nothing on an existing row; promote it instead.
        stored = db.update_job_classification(job["id"], job["tier"], job["reason"], salary=new_salary)
        notify = stored and (existing.get("status") or "new") == "new"
    else:
        stored = db.insert_job(job)
        notify = stored
        if not stored:
            _unstored(state, job)
    if job["tier"] in ACTIONABLE and notify:
        state.push_attempts += 1
        if notifier.push_job(job):
            state.pushed += 1
            return "pushed"
    return "classified"


def process_queue(state: RunState, source: str, queue: list[dict]) -> None:
    """Classify one source's queue in priority order, within its cap and the run's time budget."""
    cap = config.MAX_CLASSIFY_PER_RUN[source]
    ordered = sorted(queue, key=_queue_key)
    for index, job in enumerate(ordered):
        if state.used[source] >= cap:
            _leave(state, source, ordered[index:], "cap")
            return
        if not _time_left(state):
            _leave(state, source, ordered[index:], "time")
            return
        process_job(job, state)


def retry_pending(state: RunState) -> None:
    """Classify jobs parked by earlier runs (scraper/main.py retry_pending, adapted).

    Promotion goes through update_job_classification (insert_job would change
    nothing). A promoted row is pinged only if it is actionable AND its status
    is still 'new' -- a parked row can be actioned before it is promoted.
    Rows parked without a description are re-classified title-only, and the
    classifier's title-only cap keeps them off a loud APPLY.
    """
    rows = db.fetch_pending_jobs(config.RETRY_PENDING_MAX)
    if not rows:
        return
    log.info("Retrying %d parked job(s)", len(rows))
    attempted = promoted = notified = 0
    consecutive_failures = 0
    for index, row in enumerate(rows):
        if not _time_left(state):
            log.info("Time budget spent — %d parked job(s) wait for the next run", len(rows) - index)
            break
        job = {"id": row["id"], "title": row.get("title", ""), "company": row.get("company", ""),
               "location": row.get("location", ""), "description": row.get("description")}
        attempted += 1
        state.attempted += 1
        result = classifier.classify(job)
        if result.get("failed"):
            kind = result.get("failed_kind", "transient")
            state.failures[kind] += 1
            if kind in ("billing", "auth"):
                # The breaker just tripped: every remaining row would fail the same way.
                state.hard_down[kind] += 1
                log.warning("Classifier still down (%s) — stopping the retry pass; %d job(s) remain parked",
                            kind, len(rows) - index)
                break
            consecutive_failures += 1
            log.warning("Pending job %s failed again (%s) — leaving parked", row["id"], kind)
            if consecutive_failures >= 2:
                log.warning("Two consecutive failures — stopping the retry pass for this run")
                break
            continue

        consecutive_failures = 0
        state.succeeded += 1
        ok = db.update_job_classification(row["id"], result["tier"], result.get("reason", ""),
                                          salary=result.get("salary") if not row.get("salary") else None)
        if not ok:
            continue
        promoted += 1
        if result["tier"] in ACTIONABLE and (row.get("status") or "new") == "new":
            state.push_attempts += 1
            if notifier.push_job({**row, **result}):
                notified += 1
                state.pushed += 1

    still = db.count_pending_jobs()
    log.info("Pending retry: %d attempted, %d promoted (%d notified), %d still pending",
             attempted, promoted, notified, still)
    if promoted and db.get_state(DOWN_ALERT_KEY):
        notifier.push_owner_alert(
            f"Classifier recovered — {promoted} parked job(s) classified, {notified} pushed, {still} still queued.",
            title="Brice: classifier recovered", priority="default")
        db.clear_state(DOWN_ALERT_KEY)


def persist_jobright_etags(state: RunState) -> None:
    """Keep a list's ETag only if this run left none of its rows behind; otherwise
    clear it, so the next run re-reads the whole README instead of getting a 304
    that hides the leftovers."""
    for name, info in state.jobright.items():
        if info["status"] != 200:
            continue            # 304: the stored tag is still right; a failed fetch keeps the old one
        key = ETAG_KEY_PREFIX + name
        if state.leftover_lists[name] == 0 and info.get("etag"):
            db.set_state(key, info["etag"])
        else:
            db.clear_state(key)


def _younger_than(value: Optional[str], seconds: float) -> bool:
    """True if an ISO timestamp from bot_state is less than `seconds` old. Unreadable -> False."""
    if not value:
        return False
    try:
        when = datetime.fromisoformat(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - when < timedelta(seconds=seconds)
    except (TypeError, ValueError):
        return False


def read_linkedin_marker(state: RunState) -> None:
    state.li_marker = db.get_state(LI_LEFTOVER_KEY)
    state.li_deep = _younger_than(state.li_marker, config.LOOKBACK_SECONDS)
    if state.li_deep:
        log.info("LinkedIn jobs were left unstored in the last 24 h — every search pages past stored results")


def persist_linkedin_marker(state: RunState) -> None:
    """Stamp LI_LEFTOVER_KEY when this run left LinkedIn jobs unstored. Clear it once a full deep scan (every
    term, no rate limit or error) left nothing behind, or once it is too old to matter."""
    if state.li_unstored:
        db.set_state(LI_LEFTOVER_KEY, datetime.now(timezone.utc).isoformat())
        return
    li = state.li
    complete = not li["skipped_terms"] and not li["rate_limited"] and not li["errors"]
    if state.li_marker and (complete or not state.li_deep):
        db.clear_state(LI_LEFTOVER_KEY)


def run_passes(state: RunState) -> None:
    ats_queue = collect_ats(state)
    read_linkedin_marker(state)
    li_new = scan_linkedin(state)
    jobright_queue = collect_jobright(state)

    store_prefiltered(state, [j for j in li_new if j["gate"]])
    process_queue(state, "ats", ats_queue)
    process_queue(state, "linkedin", [j for j in li_new if not j["gate"]])
    process_queue(state, "jobright", jobright_queue)
    retry_pending(state)
    persist_jobright_etags(state)
    persist_linkedin_marker(state)


# ── owner alerts ─────────────────────────────────────────────────────────────

def _throttled(key: str, hours: float) -> bool:
    # an unreadable marker alerts rather than stays silent (_younger_than -> False)
    return _younger_than(db.get_state(key), hours * 3600)


def _alert(key: str, hours: float, message: str, title: str, priority: str = "urgent") -> bool:
    if _throttled(key, hours):
        log.info("Owner alert throttled (%s)", key)
        return False
    if notifier.push_owner_alert(message, title=title, priority=priority):
        db.set_state(key, datetime.now(timezone.utc).isoformat())
        return True
    return False


def send_alerts(state: RunState) -> None:
    """Owner alerts after finish_run (the lock is already released)."""
    if state.quota_hit:
        # bot_state is unusable, so no throttle: one alert per affected run.
        notifier.push_owner_alert("Supabase quota spent — run stopped before more work. Every request is refused "
                                  "until the quota refills or the plan is upgraded.",
                                  title="Brice: Supabase quota spent")
        return

    hard = sum(state.hard_down.values())
    if hard:
        kind = "billing" if state.hard_down.get("billing") else "auth"
        parked = state.parked.get("billing", 0) + state.parked.get("auth", 0)
        total = db.count_pending_jobs()
        _alert(DOWN_ALERT_KEY, config.ALERT_THROTTLE_HOURS,
               f"Claude classifier is DOWN ({kind}) — parked {parked} job(s) this run, {total} waiting. They "
               f"classify automatically once the API is back. Top up: console.anthropic.com",
               title="Brice: classifier down")
    elif state.attempted >= 3 and state.succeeded == 0:
        kinds = ", ".join(f"{n} {k}" for k, n in sorted(state.failures.items())) or "none recorded"
        _alert("alert_at:all_failed", config.ALERT_THROTTLE_HOURS,
               f"Every classification failed this run ({state.attempted} attempted: {kinds}). Failed jobs are "
               f"parked as PENDING and retried automatically.",
               title="Brice: all classifications failed")

    li = state.li
    if li["searches"] > 0 and li["total_raw"] == 0:
        _alert("alert_at:linkedin_zero", config.ALERT_THROTTLE_HOURS,
               f"LinkedIn returned 0 results across {li['searches']} searches (rate limited: "
               f"{li['rate_limited']}). ATS and jobright still ran. Check whether LinkedIn blocked the runner IP "
               f"or changed its API.",
               title="Brice: LinkedIn returned nothing")
    elif li["skipped_terms"] or (li["searches"] and 2 * li["rate_limited"] > li["searches"]):
        _alert("alert_at:linkedin_incomplete", config.ALERT_THROTTLE_HOURS,
               f"LinkedIn search incomplete: {len(li['skipped_terms'])} of {li['terms']} terms skipped by the time "
               f"budget, {li['rate_limited']} of {li['searches']} searches rate limited. The ATS sweep took "
               f"{state.ats['seconds'] / 60:.0f} min. Skipped terms are searched again next run (24 h window).",
               title="Brice: LinkedIn search incomplete")

    if state.ats["boards"] > 0 and state.ats["boards_with_listings"] == 0:
        _alert("alert_at:ats_empty", config.CANARY_THROTTLE_HOURS,
               f"None of the {state.ats['boards']} company boards returned a listing this run.",
               title="Brice: ATS boards returned nothing")
    elif state.ats["cut_short"]:
        _alert("alert_at:ats_slow", config.CANARY_THROTTLE_HOURS,
               f"The ATS sweep hit its {config.ATS_SWEEP_BUDGET_S // 60}-minute budget, so the boards still paging "
               f"were cut short (their later pages wait for the next run). A board is slow or stuck; the log names "
               f"the boards with no listings.",
               title="Brice: ATS sweep cut short")

    for name, info in state.jobright.items():
        if info["problems"]:
            _alert(f"alert_at:jobright:{name}", config.CANARY_THROTTLE_HOURS,
                   f"jobright {name} list: {'; '.join(info['problems'])}.",
                   title=f"Brice: jobright {name} list")

    # A topic ntfy rejects (a reserved topic answering 403, a malformed secret) drops every ping while each run
    # still looks healthy. The jobs are stored, so they are never pinged later.
    if state.push_attempts >= 3 and state.pushed == 0:
        _alert("alert_at:pings_failed", config.ALERT_THROTTLE_HOURS,
               f"Every ping to Brice failed this run ({state.push_attempts} attempted). The jobs are stored and "
               f"on his dashboard but will not be pinged again. Check the NTFY_TOPIC_BRICE secret and ntfy.sh.",
               title="Brice: pings failing")


def _wrap_up(state: RunState, crashed: Optional[BaseException]) -> None:
    """Summary line and owner alerts. Never raises: it runs in run()'s finally, after
    finish_run, where an exception would replace the one already propagating."""
    try:
        log_summary(state)
    except Exception as exc:  # noqa: BLE001
        log.error("Run summary failed (%s)", type(exc).__name__)
    try:
        if crashed is not None:
            notifier.push_owner_alert(f"run crashed: {type(crashed).__name__}", title="Brice: run crashed")
        send_alerts(state)
    except Exception as exc:  # noqa: BLE001
        log.error("Owner alerts failed (%s)", type(exc).__name__)


# ── entry point ──────────────────────────────────────────────────────────────

def finish_stats(state: RunState) -> dict:
    stats = {
        "total_raw": state.li["total_raw"],
        "new_jobs": state.li["new"] + state.ats["candidates"] + state.jobright_candidates,
        "notified": state.pushed,
        "rate_limited": state.li["rate_limited"],
        "ats_candidates": state.ats["candidates"],
        "jobright_candidates": state.jobright_candidates,
        "classified": state.succeeded,
        "failed": sum(state.parked.values()),
        "leftover": sum(state.leftover.values()),
    }
    return {k: stats[k] for k in FINISH_RUN_KEYS}


def _counts(counter: Counter) -> str:
    return ", ".join(f"{k} {n}" for k, n in sorted(counter.items())) or "0"


def log_summary(state: RunState) -> None:
    leftover = ", ".join(f"{s} {n} ({state.leftover_why.get(s)})" for s, n in state.leftover.items() if n) or "0"
    log.info("Run summary: ats cand %d | linkedin new %d (claude %d, pre-filtered %d) | jobright cand %d | "
             "classified %d | parked %s | pushed %d | leftover %s",
             state.ats["candidates"], state.li["new"], state.li["to_classify"], sum(state.li["prefiltered"].values()),
             state.jobright_candidates, state.succeeded, _counts(state.parked), state.pushed, leftover)


def preflight() -> list[str]:
    """Names of missing settings (never their values)."""
    missing = [name for name in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "ANTHROPIC_API_KEY", "NTFY_TOPIC")
               if not getattr(config, name, "")]
    path = config.CANDIDATE_PROFILE_PATH
    try:
        ok = path.is_file() and path.stat().st_size > 0
    except OSError:
        ok = False
    if not ok:
        missing.append(f"CANDIDATE_PROFILE_PATH ({path.name})")
    return missing


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Brice pipeline: LinkedIn + company ATS boards + jobright lists.")
    parser.add_argument("--dry-run", action="store_true",
                        help="collect and report only: no database, no Claude, no ntfy, no secrets needed")
    dry = parser.add_argument_group("dry-run options (only with --dry-run)")
    dry.add_argument("--no-linkedin", action="store_true", help="skip the LinkedIn pass")
    dry.add_argument("--no-ats", action="store_true", help="skip the company-board pass")
    dry.add_argument("--no-jobright", action="store_true", help="skip the jobright pass")
    dry.add_argument("--linkedin-pages", type=int, default=None, metavar="N",
                     help="pages per search, 1-10 (default 1)")
    dry.add_argument("--linkedin-terms", type=int, default=None, metavar="N",
                     help="only the first N search terms (default all)")
    dry.add_argument("--sample", type=int, default=None, metavar="N", help="sample titles to print (default 8)")
    args = parser.parse_args(argv)
    given = [f"--{k.replace('_', '-')}" for k, default in DRY_ONLY_DEFAULTS.items() if getattr(args, k) != default]
    if given and not args.dry_run:
        parser.error(f"{', '.join(given)} only valid with --dry-run")
    if args.linkedin_pages is not None and not 1 <= args.linkedin_pages <= config.MAX_PAGES_PER_SEARCH:
        parser.error(f"--linkedin-pages must be 1-{config.MAX_PAGES_PER_SEARCH}")
    if args.linkedin_terms is not None and not 1 <= args.linkedin_terms <= len(config.SEARCH_TERMS):
        parser.error(f"--linkedin-terms must be 1-{len(config.SEARCH_TERMS)}")
    if args.sample is not None and args.sample < 0:
        parser.error("--sample must be >= 0")
    return args


def run(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    if args.dry_run:
        return dry_run(args)

    missing = preflight()
    if missing:
        log.error("Preflight failed — missing or empty: %s. Nothing fetched or classified.", ", ".join(missing))
        if config.OWNER_NTFY_TOPIC:
            notifier.push_owner_alert(f"Run did not start: missing or empty {', '.join(missing)}.",
                                      title="Brice: run did not start")
        return 1
    if not config.OWNER_NTFY_TOPIC:
        log.warning("OWNER_NTFY_TOPIC not set — infrastructure alerts will only be logged")

    log.info("=== Brice pipeline starting — %d terms x %d locations, %d jobright lists ===",
             len(config.SEARCH_TERMS), len(config.LOCATIONS), len(config.JOBRIGHT_LISTS))
    try:
        run_id = db.start_run()
    except db.QuotaExceeded:
        log.error("Supabase quota spent — run stopped before any work")
        notifier.push_owner_alert("Supabase quota spent — run stopped before more work.",
                                  title="Brice: Supabase quota spent")
        return 1
    except db.DbUnavailable as exc:
        log.error("Supabase unavailable at run start (%s) — nothing fetched or classified", type(exc).__name__)
        notifier.push_owner_alert(f"Supabase unavailable at run start ({type(exc).__name__}) — nothing fetched "
                                  f"or classified.", title="Brice: Supabase unavailable")
        return 1
    if run_id is None:
        log.warning("Another run appears to be in progress (<%d min, unfinished) — skipping", db.RUN_LOCK_MINUTES)
        return 0

    state = RunState()
    crashed: Optional[BaseException] = None
    try:
        run_passes(state)
    except db.QuotaExceeded:
        state.quota_hit = True
        log.error("Supabase quota spent mid-run — stopping before more work")
    except Exception as exc:
        crashed = exc
        raise
    finally:
        db.finish_run(run_id, **finish_stats(state))     # never raises; releases the lock first
        _wrap_up(state, crashed)
    return 1 if state.quota_hit else 0


# ── dry run ──────────────────────────────────────────────────────────────────

def dry_run(args) -> int:
    """Collect from the selected sources and print what a run would do. No db, classifier or notifier call."""
    print("=== DRY RUN — no database, no Claude, no ntfy ===")
    state = RunState(dry_run=True, linkedin_pages=args.linkedin_pages or 1, linkedin_terms=args.linkedin_terms)
    sample = 8 if args.sample is None else args.sample
    queues: dict[str, list[dict]] = {s: [] for s in SOURCES}
    prefiltered: list[dict] = []
    ran = []
    if not args.no_ats:
        queues["ats"] = collect_ats(state)
        ran.append("ats")
    if not args.no_linkedin:
        li_new = scan_linkedin(state)
        queues["linkedin"] = [j for j in li_new if not j["gate"]]
        prefiltered = [j for j in li_new if j["gate"]]
        ran.append("linkedin")
    if not args.no_jobright:
        queues["jobright"] = collect_jobright(state)
        ran.append("jobright")
    print_dry_report(state, queues, prefiltered, ran, sample)
    return 0


def print_dry_report(state: RunState, queues: dict, prefiltered: list[dict], ran: list[str], sample: int) -> None:
    out = print
    out("")
    if "ats" in ran:
        a = state.ats
        out(f"ats:      {a['boards']} boards ({a['boards_with_listings']} with listings) | raw {a['listings']} | "
            f"kept {a['kept']} | new in run {len(queues['ats'])} | dropped: {_counts(a['dropped_by'])}")
        if a["by_board"]:
            out("  per board, listings / kept by the gate:")
            for company, b in a["by_board"].items():
                out(f"    {company} ({b.get('platform', '?')}): {b.get('listings', 0)} / {b.get('kept', 0)}")
            empty = [company for company, b in a["by_board"].items() if not b.get("listings")]
            if empty:
                out(f"  boards with no listings (an error or an empty board): {', '.join(empty)}")
    if "linkedin" in ran:
        li = state.li
        out(f"linkedin: {li['searches']} searches ({li['rate_limited']} rate limited) | raw {li['total_raw']} | "
            f"new in run {li['new']} | to Claude {li['to_classify']} | pre-filtered "
            f"{sum(li['prefiltered'].values())}: {_counts(li['prefiltered'])}")
        if li["skipped_terms"]:
            out(f"          skipped (search budget): {', '.join(li['skipped_terms'])}")
    if "jobright" in ran:
        rows = sum(i["rows"] for i in state.jobright.values())
        kept = sum(i["kept"] for i in state.jobright.values())
        dropped = Counter()
        for info in state.jobright.values():
            dropped.update(info["dropped_by"])
        out(f"jobright: {len(state.jobright)} lists | rows {rows} | kept {kept} | new in run {len(queues['jobright'])}"
            f" | dropped: {_counts(dropped)}")
        for name, info in state.jobright.items():
            out(f"  jobright {name}: HTTP {info['status']} | rows {info['rows']} ({info['unparsed']} unparsed) | "
                f"in window {info.get('window', 0)} | kept {info['kept']} | new {info['new']} | "
                f"canary: {'; '.join(info['problems']) or 'none'}")
            if info["dropped_by"]:
                out(f"    dropped: {_counts(info['dropped_by'])}")

    would, left = [], []
    for source in SOURCES:
        n = len(queues[source])
        cap = config.MAX_CLASSIFY_PER_RUN[source]
        would.append(f"{source} {min(n, cap)}")
        if n > cap:
            left.append(f"{source} {n - cap}")
    out(f"would classify: {' / '.join(would)}; leftover {', '.join(left) or '0'}"
        f" (caps {config.MAX_CLASSIFY_PER_RUN}; the time budget may stop a run sooner)")

    for source in SOURCES:
        if source not in ran or not sample:
            continue
        rows = sorted(queues[source], key=_queue_key)[:sample]
        out(f"\nkept sample ({source}, {len(rows)} of {len(queues[source])}): company | title | location | family")
        for job in rows:
            out(f"  {job.get('company')} | {job.get('title')} | {job.get('location')} | {job.get('family') or '-'}")
    if sample:
        by_rule: dict[str, list[str]] = {}
        totals: dict[str, int] = {}
        for job in prefiltered:
            by_rule.setdefault(f"linkedin, {job['gate']}", []).append(f"{job.get('company')} | {job.get('title')}")
        for rule, titles in state.ats["dropped_samples"].items():
            by_rule.setdefault(f"ats, {rule}", []).extend(titles)
            # the ATS pass keeps only the first few titles per rule; the count is the rule's total
            totals[f"ats, {rule}"] = state.ats["dropped_by"].get(rule, 0)
        for info in state.jobright.values():
            for rule, titles in info.get("dropped_samples", {}).items():
                by_rule.setdefault(f"jobright, {rule}", []).extend(titles)
                totals[f"jobright, {rule}"] = totals.get(f"jobright, {rule}", 0) + info["dropped_by"].get(rule, 0)
        for label, titles in sorted(by_rule.items()):
            total = max(len(titles), totals.get(label, 0))
            out(f"\ndropped sample ({label}, {min(len(titles), sample)} of {total}):")
            for t in titles[:sample]:
                out(f"  {t}")


if __name__ == "__main__":
    sys.exit(run())
