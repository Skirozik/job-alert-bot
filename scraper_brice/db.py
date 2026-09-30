"""Supabase client, dedup keys, run-lock and job storage for the Brice pipeline.

Fork of scraper_hassan/db.py, with the main pipeline's guards ported from
scraper/db.py:

  * QuotaExceeded: a spent Supabase quota stops the run instead of failing
    open (every fail-open branch that could lead to another fetch or Claude
    call re-raises it);
  * find_known_candidates: two indexed IN lookups per <=100 candidates replace
    the fork's full-table download (load_dedup_index is gone), so egress per
    run follows the candidates, not the table size;
  * get_job_row: the already-stored guard in front of every fetch, Claude call
    and push;
  * the PENDING queue helpers and bot_state (alert throttles, jobright ETags).

start_run fails CLOSED here: after one retry, any non-quota error raises
DbUnavailable instead of "proceeding without run-lock" -- a fresh project
whose schema was never run, or an outage, must not run unlocked with every
listing looking new.

The key code (norm_company .. make_norm_key and its constants) is copied
byte-for-byte from scraper/db.py; scraper/test_norm_key.py and
test_db_guards.py fail if it drifts.

IMPORT CONSTRAINT: scraper/test_norm_key.py loads this file while sys.path[0]
is scraper/, so `from config import ...` resolves to the MAIN pipeline's
config. Module-level imports are therefore limited to the stdlib, supabase,
postgrest and SUPABASE_URL / SUPABASE_SERVICE_KEY from config.

Nothing here logs the Supabase URL or key: every exception is logged through
_redact().
"""

import re
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from urllib.parse import urlparse

from postgrest.types import ReturnMethod
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_SERVICE_KEY

log = logging.getLogger(__name__)


class QuotaExceeded(RuntimeError):
    """Supabase is dropping every request because the org's plan quota is spent.

    The fail-open branches in this module exist for TRANSIENT errors: a blip
    that costs one wasted pass and clears itself. A spent quota is the other
    kind -- every request fails the same way until the billing cycle turns --
    and under fail-open that means "every candidate is new" on every run:
    re-fetch every description, re-classify every listing with Claude, fail to
    store any of it, repeat at the next cron. Measured 2026-09-12, the day the
    egress cap hit: one Job Scraper run re-processed 724 jobs it already had,
    took 12 minutes instead of one, and the watchers were being cancelled by
    their own next run all afternoon. Nothing was stored and nobody was told.

    So this raises. The run dies before the first
    description fetch or Claude call, the workflow goes red, and the fix is
    the Supabase billing page rather than a bill that climbs silently.
    """


# What a quota refusal looks like from the Python client. The gateway answers
# HTTP 402 with {"message": "Service for this project is restricted due to the
# following violations: exceed_egress_quota. ..."}; postgrest-py wraps that in
# an APIError whose str() carries the message. Matched on prose because that is
# all the client surfaces (the same reason start_run matches text), and on the
# status code where a lower-level httpx error exposes one.
_QUOTA_MARKERS = (
    "exceed_egress_quota",
    "restricted due to the following violations",
    "payment required",
)


def _raise_if_quota(exc: BaseException) -> None:
    """Turn a spent-quota refusal into QuotaExceeded; do nothing for anything else.

    Called first in every fail-open except block below whose swallowed error
    could let another description fetch or Claude call happen afterwards.

    DELIBERATELY NOT called in finish_run, get_state, set_state or clear_state:
    those are end-of-run bookkeeping with nothing costly after them, and
    finish_run runs inside run()'s finally -- raising there would replace
    whatever exception was already propagating (possibly a real bug's
    traceback) with a QuotaExceeded raised while cleaning up.
    """
    text = f"{exc} {getattr(exc, 'message', '') or ''}".lower()
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 402 or any(marker in text for marker in _QUOTA_MARKERS):
        raise QuotaExceeded(
            "Supabase is refusing requests for this project -- plan quota spent "
            f"({str(exc)[:160]}). Stopping this run before any classification; "
            "it will keep failing until the quota refills or the plan is upgraded."
        ) from exc


class DbUnavailable(RuntimeError):
    """Supabase unavailable at run start; nothing fetched or classified.

    Raised by start_run for any failure that is not a spent quota and survives
    one retry. The forks used to log "proceeding without run-lock/stats" and
    carry on, which on this pipeline would mean a run with no lock whose every
    listing looks new (the dedup lookups fail open too).
    """


def _redact(text) -> str:
    """str(text) with the Supabase key, URL and host replaced by ***.

    postgrest/httpx errors can carry the request URL, and Actions logs are
    public. Every log line in this module that interpolates an exception goes
    through here.
    """
    s = str(text)
    host = urlparse(SUPABASE_URL).netloc if SUPABASE_URL else ""
    for secret in (SUPABASE_SERVICE_KEY, SUPABASE_URL, host):
        if secret:
            s = s.replace(secret, "***")
    return s


def _exc_text(exc: BaseException) -> str:
    """Exception type and redacted message, capped for the log."""
    return f"{type(exc).__name__}: {_redact(exc)[:200]}"


_client: Optional[Client] = None

# Noise words stripped from company names during normalization
_COMPANY_NOISE = {
    "inc", "llc", "corp", "co", "company", "international", "electronics",
    "financial", "technologies", "technology", "labs", "group", "holdings",
    "solutions", "software", "ltd", "plc", "industries", "services", "systems",
    "digital", "global", "ventures",
}


def get_client() -> Client:
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set. "
                "Copy .env.brice.example to .env.brice and fill in your values."
            )
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


def norm_company(c: str) -> str:
    c = (c or "").lower().strip()
    c = re.sub(r"\(yc.*?\)", "", c)          # strip YC batch tags
    c = re.sub(r"'s\b", "", c)
    c = re.sub(r"[^a-z0-9 ]", " ", c)
    toks = [t for t in c.split() if t]
    if toks and toks[0] == "the":
        toks = toks[1:]
    stripped = [t for t in toks if t not in _COMPANY_NOISE]
    # If every token was noise (e.g. "The Digital Solutions Group"), fall back
    # to the pre-strip tokens so unrelated companies don't collide on "".
    final = stripped if stripped else toks
    return " ".join(final).strip()


# The words norm_role deletes, so that the "Intern", "Internship", "Co-op" and
# "Coop" spellings of ONE internship share a key, in any word order. Also the
# signal make_norm_key uses to keep an internship from ever sharing a key with
# a full-time job — see make_norm_key.
_INTERN_WORDS = re.compile(r"\b(internship|intern|co\s*op|coop)\b")

# Appended to the key of a title that carries no internship word.
FULL_TIME_KEY_SUFFIX = "|ft"


def _norm_role_parts(r: str) -> tuple[str, bool]:
    """The normalised role, and whether an internship word was stripped from it.

    One pass, so the key and the flag can never disagree about what was
    stripped. norm_role's comment below explains the normalisation itself.
    """
    r = (r or "").lower().strip()
    r = re.sub(r"[^a-z0-9 ]", " ", r)
    had_intern_word = bool(_INTERN_WORDS.search(r))
    r = _INTERN_WORDS.sub("", r)
    r = re.sub(r"\s+", " ", r)
    return r.strip(), had_intern_word


def norm_role(r: str) -> str:
    # Strip only a trailing "- Season YYYY" tag, not everything after the
    # first dash — otherwise "Intern - iOS - Summer 2026" and
    # "Intern - Data - Summer 2026" both collapse to the same key.
    # Strip a "Season YYYY" tag wherever it appears, with or without
    # surrounding brackets or dashes.
    #
    # Previously this only matched a TRAILING "- Season YYYY" or a
    # parenthesised "(Season YYYY)", so a season sitting mid-title survived and
    # the same job produced two different keys — i.e. two notifications.
    # Confirmed live on the main pipeline:
    #   "Software Engineer Intern (Fall 2026) - Austin, TX"
    #       -> "software engineer austin tx"
    #   "Software Engineer Intern - Fall 2026 - Austin - TX"
    #       -> "software engineer fall 2026 austin tx"
    # Same Cloudflare posting, two keys, notified twice. Google's
    # "Intern, BS, Summer 2027" vs "Intern - BS - Summer 2027" failed the same way.
    # Season tags are deliberately NOT stripped. They are normalised for free
    # by the punctuation pass below: "(Fall 2026)" and "- Fall 2026 -" both
    # reduce to the same " fall 2026 " token, so the same posting written two
    # ways produces one key.
    #
    # The old code stripped a PARENTHESISED "(Fall 2026)" but not a mid-title
    # "- Fall 2026 -", which is precisely why the same Cloudflare job produced
    # two keys and notified twice ("software engineer austin tx" vs
    # "software engineer fall 2026 austin tx"). Google's "Intern, BS, Summer
    # 2027" vs "Intern - BS - Summer 2027" failed the same way.
    #
    # Stripping seasons everywhere would fix that but cause something worse:
    # a Spring 2027 and a Summer 2027 posting for the same role would collapse
    # into one key and one of them would never be surfaced. Heliux posts
    # exactly that pair. A duplicate notification is a nuisance; a hidden job
    # is a missed opportunity, so keep the season and let it distinguish them.
    return _norm_role_parts(r)[0]


def make_norm_key(company: str, title: str) -> str:
    """The cross-source dedup key: a listing whose key is already stored is
    dropped as known BEFORE any fetch, pre-filter or classification.

    AN INTERNSHIP MUST NEVER SHARE A KEY WITH A FULL-TIME JOB. norm_role
    deletes "intern" so that one internship's spellings collapse together —
    but that also made "Production Engineering Intern" and the full-time
    "Production Engineering" the same key. Meta's full-time role had been
    stored (INELIGIBLE) since July, so on 2026-09-25 the bot saw Meta's
    Production Engineering internship 65 times and dropped it as a duplicate
    every time: never fetched, never classified, never pushed. The logs showed
    Ramp, Keysight, Emerson, SAIC and Hyra internships lost the same way, and
    29 starred companies (Google, Cisco, IBM among them) had "software
    engineer" owned only by a full-time row, waiting to swallow the next plain
    "Software Engineer Intern" they posted.

    So a title from which norm_role stripped NO internship word gets a "|ft"
    suffix, and a title from which it did keeps exactly the key it always had:
      "Production Engineering Intern" -> "meta|production engineering"
      "Production Engineering"        -> "meta|production engineering|ft"

    Why the suffix goes on the non-internship side: internship keys are
    load-bearing — the notification ledger's sibling suppression
    (claim_job_notification), the dashboard's status widening
    (web/lib/siblings.ts) and the resume builder's sibling gate all match
    them. Full-time rows are INELIGIBLE, never pushed and never shown, so only
    dedup reads their keys. Changing only those is the smallest blast radius,
    and needs no SQL change: no SQL computes a key, it only compares strings.

    Why "an internship word was stripped" rather than the full pre-filter
    predicate (main._is_non_internship_title): the key then depends on nothing
    but this function, so tuning the pre-filter's vocabulary never silently
    changes stored keys; db.py never has to import main.py; and the two persona
    forks, whose key code is identical, can carry the same rule verbatim.
    Titles marked as internships by other words ("Summer Analyst", "Trainee")
    get the suffix, which can cost at worst a duplicate push, never a hidden
    job — and the marker word stays in their key, so they only split from
    identically-titled rows. Measured: 43 of 3,591 tracker rows (1.2%) have
    no stripped word, and each carries a program word like "Apprentice".

    No suffix on an empty role: make_norm_key("", "") must stay "|", the
    literal the SQL guards (unknown_candidates, claim_job_notification) treat
    as no key at all. "||ft" would slip past them and make every blank row
    match every other.
    """
    role, had_intern_word = _norm_role_parts(title)
    key = f"{norm_company(company)}|{role}"
    if role and not had_intern_word:
        key += FULL_TIME_KEY_SUFFIX
    return key


def find_known_candidates(jobs: Iterable[dict], batch_size: int = 100) -> tuple[set[str], set[str]]:
    """Return stored ids/norm_keys for only the supplied candidate rows.

    Ported from scraper/db.py. The Hassan fork downloaded the entire jobs table
    (id, norm_key) on every run -- 2.2 MB at 26.5k rows, paged with .range()
    and no .order(). Both columns are indexed, so two small ``IN`` lookups per
    batch stay proportional to what the sources returned instead of to the
    lifetime size of the database.

    The two-query shape is intentional. PostgREST's ``or`` expression requires
    hand-escaping arbitrary norm_key text; supabase-py's ``in_`` builder safely
    quotes it for us. A transient lookup failure keeps the scraper available by
    treating the batch as unknown; get_job_row() and the primary-key upsert are
    the backstops. A spent quota raises instead (QuotaExceeded).

    A row without a precomputed norm_key is keyed with make_norm_key: every
    source here (LinkedIn, ATS boards, jobright) lists full-time jobs, so they
    take the "|ft" suffix. There are no "gh:" tracker rows in this database.
    """
    rows = list(jobs)
    if not rows:
        return set(), set()
    ids = list(dict.fromkeys(str(j.get("id", "")) for j in rows if j.get("id")))
    norm_keys = list(dict.fromkeys(
        str(j.get("norm_key") or make_norm_key(j.get("company", ""), j.get("title", "")))
        for j in rows
    ))
    known_ids: set[str] = set()
    known_norm_keys: set[str] = set()

    try:
        client = get_client()
        for offset in range(0, len(ids), batch_size):
            result = (
                client.table("jobs")
                .select("id,norm_key")
                .in_("id", ids[offset:offset + batch_size])
                .execute()
            )
            for row in result.data or []:
                known_ids.add(row["id"])
                if row.get("norm_key"):
                    known_norm_keys.add(row["norm_key"])

        for offset in range(0, len(norm_keys), batch_size):
            result = (
                client.table("jobs")
                .select("id,norm_key")
                .in_("norm_key", norm_keys[offset:offset + batch_size])
                .execute()
            )
            for row in result.data or []:
                known_ids.add(row["id"])
                if row.get("norm_key"):
                    known_norm_keys.add(row["norm_key"])
    except Exception as exc:
        _raise_if_quota(exc)
        log.error("Failed to check candidate dedup keys (%s) — treating this batch as new", _exc_text(exc))
        return set(), set()

    return known_ids, known_norm_keys


def get_job_row(job_id: str) -> Optional[dict]:
    """Point-lookup the fields the notify decision reads, or None if absent.

    Ported from scraper/db.py: the guard of last resort for re-notification.
    find_known_candidates fails OPEN by design, so a transient Supabase blip
    makes every listing look unseen -- and insert_job's ON CONFLICT DO NOTHING
    then returns True for rows that already existed, so the caller would push
    all of them a second time. A primary-key lookup of three narrow columns
    runs only for candidates that already survived dedup, and also skips a
    description fetch and a Claude call for every job that was already stored.

    Returns None on a transient error (the caller proceeds as if new); a spent
    quota raises.
    """
    if not job_id:
        return None
    try:
        result = (
            get_client().table("jobs")
            .select("id,tier,status")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None
    except Exception as exc:
        _raise_if_quota(exc)
        log.warning("Existence check failed for %s (%s) — proceeding as if new", job_id, _exc_text(exc))
        return None


# 75 minutes: it must outlast the workflow's timeout-minutes (60), or a
# legitimately long run stops holding its own lock partway through and
# anything started meanwhile -- a local run, say -- would double-process
# alongside it. The Actions `concurrency:` group only covers Actions runs.
RUN_LOCK_MINUTES = 75


def start_run() -> Optional[int]:
    """Record the start of a run and acquire the run-lock. Fails CLOSED.

    Returns the new run's id (-1 if the insert returned no row), or None when
    an unfinished run started less than RUN_LOCK_MINUTES ago. A spent quota
    raises QuotaExceeded; any other error is retried once after 10 seconds (a
    blip should not cost a run) and then raises DbUnavailable -- never
    "proceed without the lock".
    """
    last_exc: Optional[BaseException] = None
    for attempt in (1, 2):
        try:
            client = get_client()
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=RUN_LOCK_MINUTES)).isoformat()
            active = (
                client.table("scrape_runs")
                .select("id")
                .gte("started_at", cutoff)
                .is_("finished_at", "null")
                .execute()
            )
            if active.data:
                return None
            result = (
                client.table("scrape_runs")
                .insert({"started_at": datetime.now(timezone.utc).isoformat()})
                .execute()
            )
            return result.data[0]["id"] if result.data else -1
        except Exception as exc:
            # The first database call of every run: a spent quota stops the run
            # here, before anything is fetched or classified.
            _raise_if_quota(exc)
            last_exc = exc
            if attempt == 1:
                log.warning("start_run failed (%s) — retrying once in 10s", _exc_text(exc))
                time.sleep(10)
    raise DbUnavailable(_redact(last_exc)[:200]) from last_exc


def finish_run(run_id: Optional[int], **stats) -> None:
    """Mark a run finished and record its stats. No-op without a real run id.

    Never raises: it runs in main.run()'s finally. No quota guard, for the
    reason _raise_if_quota gives. The stats keys must be scrape_runs columns
    (main.FINISH_RUN_KEYS): an unknown column fails the update, and the lock
    is then held for RUN_LOCK_MINUTES.
    """
    if not run_id or run_id < 0:
        return
    try:
        get_client().table("scrape_runs").update({
            "finished_at": datetime.now(timezone.utc).isoformat(),
            **stats,
        }, returning=ReturnMethod.minimal).eq("id", run_id).execute()
    except Exception as exc:
        log.error("Failed to record run completion for run %s (%s)", run_id, _exc_text(exc))


def insert_job(job: dict) -> bool:
    """Insert a job row. Returns True on success, False on a transient failure.

    NOTE the asymmetry with update_job_classification(): this upserts with
    ignore_duplicates=True (ON CONFLICT DO NOTHING), so calling it for a row
    that already exists changes NOTHING. Promoting a parked PENDING row must go
    through update_job_classification, not through here.
    """
    payload = {
        "id": job["id"],
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "location": job.get("location", ""),
        "url": job.get("url", ""),
        "search_term": job.get("search_term", ""),
        "description": job.get("description"),
        "logo_url": job.get("logo_url"),
        "norm_key": make_norm_key(job.get("company", ""), job.get("title", "")),
        "tier": job.get("tier", "APPLY_CAVEAT"),
        "reason": job.get("reason", ""),
        "posted_at": job.get("posted_at"),
        "apply_url": job.get("apply_url"),
        "is_easy_apply": job.get("is_easy_apply", False),
        "salary": job.get("salary"),
    }
    try:
        get_client().table("jobs").upsert(
            payload, on_conflict="id", ignore_duplicates=True, returning=ReturnMethod.minimal,
        ).execute()
        log.info("DB: stored %s", job.get("id"))          # never the tier: the log is public (main.process_job)
        return True
    except Exception as exc:
        # Raised here too, not only at the start of a run: a quota that runs
        # out mid-run must stop the loop, not let it classify the next jobs
        # and fail to store every one of them.
        _raise_if_quota(exc)
        log.error("DB insert failed for job %s (%s)", job.get("id"), _exc_text(exc))
        return False


# ── Pending-classification queue ─────────────────────────────────────────
#
# A job whose classification failed is parked as tier="PENDING" rather than
# dropped, and drained by main.retry_pending() on later runs. PENDING is a
# queue state, never a verdict. No migration needed: jobs.tier is
# unconstrained text and jobs_tier_idx covers this lookup.

_PENDING_COLUMNS = "id,title,company,location,url,description,salary,status"


def fetch_pending_jobs(limit: int) -> list[dict]:
    """Parked jobs, oldest first -- the oldest are closest to their deadlines.

    Returns [] on a transient error: a DB blip must not take down the rest of
    the run. A spent quota raises.
    """
    try:
        result = (
            get_client().table("jobs")
            .select(_PENDING_COLUMNS)
            .eq("tier", "PENDING")
            .order("found_at", desc=False)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        _raise_if_quota(exc)
        log.error("Could not fetch pending jobs (%s)", _exc_text(exc))
        return []


def count_pending_jobs() -> int:
    """How many jobs are parked in total -- for the owner alerts."""
    try:
        result = (
            get_client().table("jobs")
            .select("id", count="exact")
            .eq("tier", "PENDING")
            .limit(1)
            .execute()
        )
        return result.count or 0
    except Exception as exc:
        _raise_if_quota(exc)
        log.error("Could not count pending jobs (%s)", _exc_text(exc))
        return 0


def update_job_classification(job_id: str, tier: str, reason: str,
                              salary: Optional[str] = None) -> bool:
    """Promote a parked row to a real verdict.

    A real UPDATE, not an upsert: insert_job() upserts with
    ignore_duplicates=True (ON CONFLICT DO NOTHING), so calling it again for a
    row that already exists silently changes nothing.

    Deliberately never touches status, found_at, description, norm_key or
    search_term. found_at means "first seen" and drives dashboard ordering.
    """
    payload = {"tier": tier, "reason": reason}
    # Only when the classifier actually produced one; never blank an existing value.
    if salary:
        payload["salary"] = salary

    try:
        get_client().table("jobs").update(payload, returning=ReturnMethod.minimal).eq("id", job_id).execute()
        log.info("DB: promoted %s", job_id)
        return True
    except Exception as exc:
        # Without this, retry_pending() keeps calling Claude for the next
        # parked row after every refused UPDATE.
        _raise_if_quota(exc)
        log.error("DB update failed for job %s (%s)", job_id, _exc_text(exc))
        return False


# ── bot_state key/value helpers ──────────────────────────────────────────
# Owner-alert throttle markers and the jobright README ETags. Never raise, and
# no quota guard: nothing costly follows them.

def get_state(key: str) -> Optional[str]:
    try:
        result = get_client().table("bot_state").select("value").eq("key", key).execute()
        if result.data:
            return result.data[0]["value"]
    except Exception as exc:
        log.warning("bot_state unavailable reading %s (%s)", key, _exc_text(exc))
    return None


def set_state(key: str, value: str) -> None:
    try:
        get_client().table("bot_state").upsert(
            {"key": key, "value": value}, on_conflict="key", returning=ReturnMethod.minimal,
        ).execute()
    except Exception as exc:
        log.error("Could not write bot_state %s (%s)", key, _exc_text(exc))


def clear_state(key: str) -> None:
    try:
        get_client().table("bot_state").delete().eq("key", key).execute()
    except Exception as exc:
        log.error("Could not clear bot_state %s (%s)", key, _exc_text(exc))
