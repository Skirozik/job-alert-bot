"""LinkedIn internship job alert scraper — main entry point.

Run:  cd LinkedIn_Job_Bot/scraper && python main.py
Env:  set variables in ../.env (local) or GitHub repo secrets (CI).

Flow per run:
  0. Acquire a run-lock in Supabase (guards against two schedulers overlapping)
  1. Search LinkedIn (5 terms × 2 locations), paginating up to MAX_PAGES per search,
     plus a supplementary fetch from tracked GitHub internship-list repos.
     Dedup runs against an in-memory index loaded once at the start of the run
     (LinkedIn returns newest-first, so once a full page is all true DB
     duplicates, nothing deeper can be new either).
  2. Canary check — 0 raw LinkedIn results across all searches = something is broken
  3. Fetch description for each new job (separate detail request)
  4. Classify with Claude Haiku against Candidate_Profile_and_Filters.md
  5. Store all results in Supabase (including SKIP — so they're never re-classified)
  6. ntfy.sh push for APPLY and MAYBE, only once storage succeeded
"""

import logging
import re
import sys
import time
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Load .env from repo root for local development
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# Job titles/reasons can contain emoji or other non-ASCII characters. On
# Windows, stdout defaults to the system code page (e.g. cp1252) rather than
# UTF-8, which makes logging raise (and silently swallow) an encoding error
# on every such line. GitHub Actions/Linux runners already default to UTF-8,
# so this is a no-op there.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config import SEARCH_TERMS, LOCATIONS, LOOKBACK_SECONDS
from linkedin import fetch_listings, fetch_description
from github_sources import fetch_github_listings
from external_descriptions import fetch_external_description
from classifier import classify
from notifier import push_job, push_canary
from db import (
    load_dedup_index, make_norm_key, insert_job, start_run, finish_run,
    fetch_pending_jobs, count_pending_jobs, update_job_classification,
    get_state, set_state, clear_state,
)

# How many jobs were parked this run, by failure kind. Module-level rather than
# threaded through return values because process_job's bool return is load
# bearing — ats_watch.py and github_watch.py truth-test it for "notified" — and
# widening that contract to carry a diagnostic would be the wrong trade.
_PARKED_THIS_RUN: dict = {}

# Parked jobs retried per run. The scrape itself uses ~50-70s of a 20-minute
# Actions budget; 40 classifications at ~2-3s each adds at most 2-3 minutes, and
# a multi-day backlog still drains at roughly 120 jobs/hour across runs.
RETRY_PENDING_MAX = 40

# bot_state key for the outage canary. Throttled so a multi-day outage sends
# one alert every 6h rather than one every 20 minutes.
_DOWN_ALERT_KEY = "classifier_down_alert_at"
_DOWN_ALERT_THROTTLE_HOURS = 6

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# Title-level pre-filter: skip before hitting Claude if the title is clearly
# a senior/non-intern role. Saves description fetches and API calls.
# Matched as whole words/phrases (via \b) so e.g. "architect" doesn't match
# "Solutions Architecture Intern" and "fellow" doesn't match "Fellowship".
_SENIOR_SIGNALS = [
    "senior", "sr", "staff", "lead", "principal",
    "director", "manager", "head of", "vp", "vice president",
    "architect", "fellow", "distinguished", "executive",
]

_NEW_GRAD_SIGNALS = [
    "new grad",
    "new graduate",
    "new college grad",
    "college grad",
    "ncg",
    "university grad",
    "university graduate",
    "recent grad",
    "recent graduate",
]

_SENIOR_RE = re.compile(r"\b(?:" + "|".join(re.escape(s) for s in _SENIOR_SIGNALS) + r")\b")
_NEW_GRAD_RE = re.compile(r"\b(?:" + "|".join(re.escape(s) for s in _NEW_GRAD_SIGNALS) + r")\b")

def _is_senior_role(title: str) -> bool:
    return bool(_SENIOR_RE.search(title.lower()))

def _is_new_grad_role(title: str) -> bool:
    return bool(_NEW_GRAD_RE.search(title.lower()))

# Positive gate: LinkedIn's search does fuzzy/semantic matching, so generic
# professional titles with no internship signal in either direction (e.g.
# "Backend Engineer", "Associate Machine Learning Engineer") still show up
# despite every search term ending in "intern". Require an explicit marker
# instead of relying on the classifier to catch these via judgment alone —
# validated against production data: 0 of 33 jobs actually applied to lack
# this marker, while 35% of a given active queue can. LinkedIn-only (see
# call site) — GitHub tracker sources are internship-only by construction
# and occasionally omit "intern" from the title on a genuine internship.
# The trailing boundary is (?![a-z0-9]) rather than \b because an underscore is
# a WORD character in regex, so \b never fires between "p" and "_". That made
# "Software Engineering Co-op_Summer 2027" (GE Appliances, seen twice) read as a
# non-internship title and get pre-filtered without ever reaching the
# classifier. Underscore-as-separator is a real convention in ATS-generated
# titles — "Intern_Summer 2027", "Data Co-Op_Fall" — and every one of them was
# being dropped silently.
# "summer analyst", "summer associate" and "trainee" are internship programs
# that never say "intern". Measured over 30 days: ~10-15 tech-titled postings a
# month were pre-filtered on this vocabulary gap alone, including "Summer
# Analyst - Core Platforms" (Rockefeller Capital), "Summer Associate,
# Enterprise AI & Insights (8-10 Week Program)" (Legends Global) and "Student
# Trainee (AI Data Engineer)".
#
# "campus" and "early career" were considered and deliberately NOT added: in
# the same 30 days they matched 71 postings, overwhelmingly full-time new-grad
# roles ("Software Engineer, Early Career"), which the rubric excludes anyway —
# so they would add classifier calls without adding opportunities.
_INTERN_TITLE_RE = re.compile(
    r"\bintern(?:ship)?s?(?![a-z0-9])|\bco[\s-]?ops?(?![a-z0-9])|\bapprentice(?:ship)?s?(?![a-z0-9])"
    r"|\bsummer\s+analyst\b|\bsummer\s+associate\b|\btrainee\b"
)

# Paid student-worker roles are functionally internships but rarely contain the
# word. Universities, research institutes, and several large employers label
# them "Student Assistant" / "Part-Time Student - Software Engineer" instead.
# Measured over 90 days: 29 software-titled student roles were pre-filtered
# without ever reaching the classifier, including a Georgia Tech Research
# Institute "Software Engineer Student Assistant" — paid, Python and Java, in
# his own city — and John Deere's long-running part-time student program.
#
# Deliberately requires a job-noun after "student" so this does NOT match
# "Software Engineer II - Student Affairs", where "Student" names the
# university department doing the hiring rather than who is being hired. The
# optional middle word admits "Student IT Associate" without opening it up to
# arbitrary phrases.
# Requiring a fixed job-noun after "student" proved too brittle — it missed
# "Student Web/Application Developer" on the slash. Instead: a student token
# AND a software token, minus an explicit list of phrases where "student" names
# the department being hired *for* rather than the person being hired. That
# handles arbitrary word order and punctuation, which is what real ATS titles
# actually look like.
_STUDENT_TOKEN_RE = re.compile(r"\bstudent\b|\bwork[\s-]?study\b")
_SOFTWARE_TOKEN_RE = re.compile(
    r"software|developer|programmer|engineer|web|application|comput|data|\bIT\b|"
    r"front.?end|back.?end|full.?stack|\bqa\b|test",
    re.I,
)
# "Software Engineer II - Student Affairs" is an ordinary full-time job in a
# university's Student Affairs office, not a student hire. Same for the loan,
# services, life, success and housing departments.
_STUDENT_DEPARTMENT_RE = re.compile(
    r"student\s+(?:affairs|loan|services|success|life|housing|health|union|center|records|accounts|conduct)"
)


def _is_student_worker_title(title: str) -> bool:
    t = title.lower()
    if _STUDENT_DEPARTMENT_RE.search(t):
        return False
    return bool(_STUDENT_TOKEN_RE.search(t) and _SOFTWARE_TOKEN_RE.search(t))


def _is_non_internship_title(title: str) -> bool:
    return not (_INTERN_TITLE_RE.search(title.lower()) or _is_student_worker_title(title))


MAX_PAGES_PER_SEARCH = 10  # 100 results max per search term/location pair
# Was 5 (50 results). Confirmed live that LinkedIn's guest search endpoint
# does NOT reliably return newest-first, despite the assumption embedded in
# the "stop once a page is all duplicates" logic below (sortBy=DD produces a
# different, still-non-chronological order) — a real job took 3 full scan
# cycles to climb into the top 50 results for its own matching search term
# before this ever showed up. Doubling the search depth is the lowest-risk
# lever to catch a brand-new posting sooner, without touching scan
# frequency (which independently controls worst-case latency once a job IS
# visible). Runs currently finish in ~50-70s against a 20-minute budget, so
# there's ample headroom before this risks the timeout.


def process_job(job: dict) -> bool:
    """Fetch description, classify, store, and notify for a single job that
    has already passed the title pre-filter (or is gh:-sourced, which skips
    that filter entirely — see the pre-filter block in run()). Returns True
    if a push notification was sent.

    Factored out so github_watch.py's fast-path trigger (polls GitHub
    tracker commit feeds far more often than the main 20-min scan, to
    notify sooner than waiting for the next full cycle) reuses the exact
    same classify/store/notify logic as the main run loop, instead of a
    second copy that could silently drift out of sync with it.
    """
    log.info("Processing: '%s' @ %s [%s]", job["title"], job["company"], job["id"])

    # Fetch description + logo + apply info. LinkedIn jobs get a full
    # detail-page fetch; GitHub-sourced jobs already carry their own
    # apply_url/location and get a description from the ATS API; ATS-watch
    # jobs (ats_watch.py) already carry a full description from the listing
    # call itself, so there's nothing left to fetch for them.
    if job["id"].startswith("ats:"):
        if job.get("description"):
            log.info("  ATS source — description already provided: %d chars", len(job["description"]))
        else:
            log.info("  ATS source — no description in listing (platform doesn't include one)")
    elif not job["id"].startswith("gh:"):
        desc, logo_url, apply_url, is_easy_apply, salary_li = fetch_description(job["id"])
        if desc:
            job["description"] = desc
            log.info("  Description: %d chars", len(desc))
        else:
            log.info("  No description — classifying on title/company/location")
        if logo_url:
            job["logo_url"] = logo_url
            log.info("  Logo: %s", logo_url)
        # Card-level detection (linkedin.py) can catch cases the detail
        # page misses (and vice versa on rate limit) — keep True from either.
        job["is_easy_apply"] = job.get("is_easy_apply", False) or is_easy_apply
        if apply_url:
            job["apply_url"] = apply_url
            log.info("  Apply URL: %s", apply_url)
        if salary_li:
            job["salary"] = salary_li
            log.info("  Salary (LinkedIn): %s", salary_li)
    else:
        desc = fetch_external_description(job.get("apply_url", ""))
        if desc:
            job["description"] = desc
            log.info("  GitHub source — fetched description: %d chars", len(desc))
        else:
            log.info("  GitHub source — no description available (unrecognized/unfetchable ATS)")

    # Classify
    result = classify(job)

    # A transient API failure must NOT be persisted. Storing a fallback verdict
    # puts the job in the dedup index, so it is never reconsidered — that is
    # exactly how the 2026-08-04 outage buried real APPLY jobs as junk MAYBEs.
    # Returning early leaves the row absent, and the next run rediscovers it.
    if result.get("failed"):
        # PARK, don't drop. The description was fetched a few lines above and
        # would be lost with the row — and a LinkedIn listing is only
        # rediscoverable while it is inside LOOKBACK_SECONDS and still ranked
        # in the first ten pages, so "the next run will retry it" quietly
        # stopped being true for any outage longer than a few hours.
        #
        # Storing it puts the job in the dedup index, which here is the point:
        # retry_pending() reads it back from the DB, so nothing depends on the
        # posting still being live.
        #
        # PENDING is a queue state, not a verdict. Never notify on one.
        kind = result.get("failed_kind", "transient")
        job["tier"] = "PENDING"
        job["reason"] = "Awaiting classification — Claude API unavailable when this job was found"
        job["suggested_resume"] = "General"   # placeholder, overwritten at promotion
        if insert_job(job):
            _PARKED_THIS_RUN[kind] = _PARKED_THIS_RUN.get(kind, 0) + 1
            log.warning("  Classification FAILED (%s) — parked as PENDING for automatic retry", kind)
        else:
            log.error("  Classification FAILED (%s) and the park write failed too — job %s is lost",
                      kind, job.get("id"))
        return False
    job["tier"] = result.get("tier", "APPLY_CAVEAT")
    job["reason"] = result.get("reason", "")
    job["suggested_resume"] = result.get("suggested_resume", "General")
    if not job.get("salary") and result.get("salary"):
        job["salary"] = result["salary"]
        log.info("  Salary (Claude): %s", job["salary"])
    # NOTE: the reason is deliberately NOT logged. This repo is public, which
    # makes Actions logs world-readable, and ~1% of reasons quote the
    # candidate's name, school, graduation timing or citizenship. The job id is
    # logged instead — look the reason up in the database when debugging.
    log.info("  → %s | Resume: %s | id=%s",
             job["tier"], job["suggested_resume"], job["id"])

    # Store in Supabase first (including SKIP — prevents re-classification)
    stored = insert_job(job)

    # Push notification for APPLY and MAYBE — only if it was actually
    # persisted, so a DB hiccup doesn't cause the same job to be
    # re-classified and re-notified every run until the write succeeds.
    if job["tier"] in ("APPLY", "APPLY_CAVEAT") and stored:
        push_job(job)
        return True
    return False


def retry_pending() -> int:
    """Classify jobs parked by an earlier run. Returns how many were promoted.

    Runs before the LinkedIn search so the backlog drains even if the search
    half later fails. Nothing here re-fetches a description: the parked row
    already carries whatever was fetched at discovery, and classify() handles a
    NULL description through its "(not available…)" prompt branch. That keeps
    this pass API-only and fast.
    """
    pending = fetch_pending_jobs(RETRY_PENDING_MAX)
    if not pending:
        return 0

    log.info("Retrying %d parked job(s)", len(pending))
    attempted = promoted = notified = 0
    consecutive_failures = 0

    for row in pending:
        # The gh: id prefix has to survive — _never_skip_github_sourced keys off it.
        job = {
            "id": row["id"],
            "title": row.get("title", ""),
            "company": row.get("company", ""),
            "location": row.get("location", ""),
            "description": row.get("description"),
        }
        attempted += 1
        result = classify(job)

        if result.get("failed"):
            kind = result.get("failed_kind", "transient")
            if kind in ("billing", "auth"):
                # The breaker just tripped; every remaining row would fail the
                # same way. Leave them PENDING for the next run — while credits
                # are out this costs exactly one failed call per run.
                log.warning("Classifier still down (%s) — stopping the retry pass; "
                            "%d job(s) remain parked", kind, len(pending) - attempted + 1)
                _PARKED_THIS_RUN[kind] = _PARKED_THIS_RUN.get(kind, 0) + 1
                break

            # A poison row — e.g. one that never yields a tool_use block — sits
            # at the head of an oldest-first queue forever. Skip past it rather
            # than letting it block everything behind it.
            consecutive_failures += 1
            log.warning("Pending job %s failed again (%s) — leaving parked", row["id"], kind)
            if consecutive_failures >= 2:
                log.warning("Two consecutive failures — stopping the retry pass for this run")
                break
            continue

        consecutive_failures = 0
        ok = update_job_classification(
            row["id"],
            result["tier"],
            result.get("reason", ""),
            result.get("suggested_resume", "General"),
            # Only if the classifier found one AND the row does not already have it.
            salary=result.get("salary") if not row.get("salary") else None,
        )
        if not ok:
            continue
        promoted += 1

        # Store first, then notify — the same ordering process_job uses, so a
        # DB hiccup can never produce a push for a row that was not written.
        if result["tier"] in ("APPLY", "APPLY_CAVEAT"):
            merged = {**row, **result}
            push_job(merged)
            notified += 1

    still = count_pending_jobs()
    log.info("Pending retry: %d attempted, %d promoted (%d notified), %d still pending",
             attempted, promoted, notified, still)

    # Recovery note, once per outage. Only fires if an outage was actually
    # announced, so a routine one-off transient park never triggers it.
    if promoted and get_state(_DOWN_ALERT_KEY):
        push_canary(f"Classifier recovered — {promoted} parked job(s) classified, "
                    f"{notified} pushed. {still} still queued.")
        clear_state(_DOWN_ALERT_KEY)

    return notified


def _maybe_alert_classifier_down() -> None:
    """One urgent push per ~6h while the classifier is down.

    Only for billing and auth. A transient park is a network blip that
    self-heals within 20 minutes and is not worth a 3am notification.
    """
    hard = _PARKED_THIS_RUN.get("billing", 0) + _PARKED_THIS_RUN.get("auth", 0)
    if not hard:
        return

    last = get_state(_DOWN_ALERT_KEY)
    if last:
        try:
            when = datetime.fromisoformat(last)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - when < timedelta(hours=_DOWN_ALERT_THROTTLE_HOURS):
                return          # already alerted recently
        except Exception:
            pass                # unparseable marker — alert rather than stay silent

    kind = "billing" if _PARKED_THIS_RUN.get("billing") else "auth"
    total = count_pending_jobs()
    push_canary(
        f"Claude classifier is DOWN ({kind}) — parked {hard} job(s) this run, "
        f"{total} waiting. They classify automatically once the API is back. "
        f"Top up: console.anthropic.com"
    )
    set_state(_DOWN_ALERT_KEY, datetime.now(timezone.utc).isoformat())


def run():
    log.info("=== Job scraper starting — %d terms × %d locations ===",
             len(SEARCH_TERMS), len(LOCATIONS))

    # ── 0. Run-lock: skip if another scheduler's run looks still in progress ──
    run_id = start_run()
    if run_id is None:
        log.warning("Another run appears to be in progress (started <20 min ago, "
                     "unfinished) — skipping to avoid double-processing.")
        return

    new_jobs: list[dict] = []
    seen_in_run: set[str] = set()
    total_raw = 0
    rate_limited_count = 0
    notified = 0

    try:
        # ── 0.5 Drain the parked backlog first ──────────────────────────────
        # Before the search, so a backlog still drains on a run where the
        # LinkedIn half later fails. Inside the try, so finish_run() in the
        # finally still releases the run-lock.
        notified += retry_pending()

        # ── 1. Fetch + dedup, paginating until an all-duplicate page ────────
        # Dedup index is loaded once (one bulk query) instead of 2 Supabase
        # calls per listing. LinkedIn returns newest-first, so once a full
        # page is all *true DB duplicates*, everything deeper is older and
        # already stored — stop paginating. (Distinct from "already queued
        # this run under another search term", which used to be conflated
        # with a DB duplicate and could cut pagination short too early.)
        known_ids, known_norm_keys = load_dedup_index()

        for term in SEARCH_TERMS:
            for location in LOCATIONS:
                log.info("Searching: '%s' in %s", term, location)

                for page in range(MAX_PAGES_PER_SEARCH):
                    start = page * 10
                    jobs, err = fetch_listings(term, location, LOOKBACK_SECONDS, start=start)

                    if err == "rate_limited":
                        rate_limited_count += 1
                        log.warning("  p%d: rate limited — stopping pagination", page)
                        break
                    if err:
                        log.error("  p%d: error — %s", page, err)
                        break
                    if not jobs:
                        # Confirmed live: this endpoint is flaky, not just
                        # exhausted — the identical request (same term/
                        # location/start) returned 10 results, then 10, then
                        # 0 across back-to-back attempts seconds apart. A
                        # single empty page isn't reliable evidence pagination
                        # is done, so retry once before treating it that way —
                        # otherwise a transient glitch silently truncates the
                        # search and can delay catching a job an entire extra
                        # scan cycle for no real reason.
                        time.sleep(random.uniform(1.5, 2.5))
                        jobs, err = fetch_listings(term, location, LOOKBACK_SECONDS, start=start)
                        if err or not jobs:
                            log.info("  p%d: 0 listings on retry too — done", page)
                            break
                        log.info("  p%d: 0 listings on first try, %d on retry — continuing", page, len(jobs))

                    total_raw += len(jobs)
                    new_on_page = 0
                    all_db_duplicate = True

                    for j in jobs:
                        nk = make_norm_key(j["company"], j["title"])
                        is_db_dup = j["id"] in known_ids or nk in known_norm_keys
                        if not is_db_dup:
                            all_db_duplicate = False

                        if j["id"] in seen_in_run:
                            continue
                        seen_in_run.add(j["id"])

                        if not is_db_dup:
                            j["search_term"] = term
                            j["norm_key"] = nk
                            new_jobs.append(j)
                            new_on_page += 1
                            known_ids.add(j["id"])
                            known_norm_keys.add(nk)

                    log.info("  p%d (start=%d): %d listings, %d new", page, start, len(jobs), new_on_page)

                    if all_db_duplicate:
                        log.info("  All duplicates in DB — stopping pagination")
                        break

                    if len(jobs) < 10:
                        break  # partial page = last page

                    time.sleep(random.uniform(2.0, 3.5))

                time.sleep(random.uniform(2.0, 3.5))

        log.info("Total raw: %d | New: %d | Rate limited: %d/%d searches",
                 total_raw, len(new_jobs), rate_limited_count, len(SEARCH_TERMS) * len(LOCATIONS))

        # ── 2. Canary: 0 raw LinkedIn results across ALL searches = likely blocked ──
        if total_raw == 0:
            msg = (
                "Scraper returned 0 results across all searches.\n"
                f"Rate limited: {rate_limited_count}/{len(SEARCH_TERMS) * len(LOCATIONS)} searches.\n"
                "Check if LinkedIn has blocked the runner IP or changed its API."
            )
            log.warning("CANARY: %s", msg)
            push_canary(msg)
            return

        # ── 1b. Supplementary GitHub-tracker sources (no rate limiting) ──────
        # Kept out of the canary/total_raw check above — it exists to detect
        # LinkedIn-specific blocking, and mixing in another source would mask it.
        for j in fetch_github_listings():
            if j["id"] in seen_in_run:
                continue
            seen_in_run.add(j["id"])
            nk = make_norm_key(j["company"], j["title"])
            if j["id"] in known_ids or nk in known_norm_keys:
                continue
            known_ids.add(j["id"])
            known_norm_keys.add(nk)
            j["norm_key"] = nk
            new_jobs.append(j)

        if not new_jobs:
            log.info("No new jobs this run — done.")
            return

        # ── 3–6. Per-job: describe → classify → notify → store ──────────────
        for job in new_jobs:
            log.info("Processing: '%s' @ %s [%s]",
                     job["title"], job["company"], job["id"])

            # 3a. Pre-filter: skip senior/non-intern titles without hitting Claude.
            # GitHub-tracker sources are curated, internship-only lists the
            # user trusts completely — every one should reach APPLY/MAYBE for
            # a human decision, never get auto-SKIPped by a title heuristic.
            # (The "never SKIP" policy for gh: jobs is also enforced at the
            # classifier level — see _never_skip_github_sourced in
            # classifier.py — so it holds even if a description reveals a
            # mismatch Claude would otherwise SKIP for.)
            if not job["id"].startswith("gh:"):
                if _is_senior_role(job["title"]):
                    log.info("  Pre-filter SKIP (senior title)")
                    job["tier"] = "INELIGIBLE"
                    job["reason"] = "Pre-filtered: seniority keyword in title"
                    job["suggested_resume"] = "General"
                    insert_job(job)
                    continue

                if _is_new_grad_role(job["title"]):
                    log.info("  Pre-filter SKIP (new grad / full-time role)")
                    job["tier"] = "INELIGIBLE"
                    job["reason"] = "Pre-filtered: new grad / full-time role, not an internship"
                    job["suggested_resume"] = "General"
                    insert_job(job)
                    continue

                if _is_non_internship_title(job["title"]):
                    log.info("  Pre-filter SKIP (no internship marker in title)")
                    job["tier"] = "INELIGIBLE"
                    job["reason"] = "Pre-filtered: no internship marker in title"
                    job["suggested_resume"] = "General"
                    insert_job(job)
                    continue

            if process_job(job):
                notified += 1

        if _PARKED_THIS_RUN:
            log.warning("Parked this run: %s",
                        ", ".join(f"{n} {k}" for k, n in sorted(_PARKED_THIS_RUN.items())))
        _maybe_alert_classifier_down()

        log.info("=== Run complete: %d new jobs, %d notified ===",
                 len(new_jobs), notified)

    finally:
        finish_run(
            run_id,
            total_raw=total_raw,
            new_jobs=len(new_jobs),
            notified=notified,
            rate_limited=rate_limited_count,
        )


if __name__ == "__main__":
    run()
