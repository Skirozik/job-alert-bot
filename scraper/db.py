"""Supabase client, dedup logic, and job insertion.

Dedup normalization is adapted from the existing dedup.py in the cowork folder.
"""

import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_SERVICE_KEY
from target_key import definitive_target_key

log = logging.getLogger(__name__)


class DedupUnavailable(RuntimeError):
    """The server-side dedup function is missing. Raised, never swallowed.

    Deliberately NOT folded into the fail-open path. A transient Supabase error
    is rare and brief, so reporting every candidate as new costs one wasted
    pass. An unapplied migration is PERSISTENT -- it recurs every run, forever
    -- and "every candidate is new" at ATS scale means ~30,700 listings each
    taking an insert_job round trip, every five minutes. That is far worse than
    the full-table read this replaced, and it would look healthy: runs stay
    green while the bill climbs.

    So this fails LOUD instead: the run dies, the workflow goes red, and the
    fix is a one-time migration rather than a silent degradation nobody sees.
    """

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
                "Copy .env.example to .env and fill in your values."
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


def norm_role(r: str) -> str:
    r = (r or "").lower().strip()
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
    r = re.sub(r"[^a-z0-9 ]", " ", r)
    r = re.sub(r"\b(internship|intern|co\s*op|coop)\b", "", r)
    r = re.sub(r"\s+", " ", r)
    return r.strip()


def make_norm_key(company: str, title: str) -> str:
    return f"{norm_company(company)}|{norm_role(title)}"


def find_known_candidates(jobs: Iterable[dict], batch_size: int = 100) -> tuple[set[str], set[str]]:
    """Return stored ids/norm_keys for only the supplied candidate rows.

    LinkedIn normally gives us roughly ten cards per page. Downloading the
    entire jobs table before checking those ten cards grew to 64,000 rows and
    ~16 seconds per run. Both columns are indexed, so two small ``IN`` lookups
    per batch stay proportional to what LinkedIn returned instead of to the
    lifetime size of the database.

    The two-query shape is intentional. PostgREST's ``or`` expression requires
    hand-escaping arbitrary norm_key text; supabase-py's ``in_`` builder safely
    quotes it for us. A transient lookup failure keeps the scraper available by
    treating the batch as unknown, matching find_unknown_candidates' existing
    fail-open behavior. The primary-key upsert remains the final backstop.
    """
    rows = list(jobs)
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
        log.error("Failed to check candidate dedup keys: %s — treating this batch as new", exc)
        return set(), set()

    return known_ids, known_norm_keys


def find_unknown_candidates(jobs: Iterable[dict], batch_size: int = 5000) -> set[str]:
    """Return the ids of the supplied candidates that are NOT already stored.

    Replaces load_dedup_index() at the watcher call sites. That function
    downloaded the whole jobs(id, norm_key) table -- 73 paginated requests,
    ~73,000 rows, roughly 5 MB -- on EVERY run. At the ATS watcher's 5-minute
    cadence that is ~1.4 GB/day against a 5 GB quota, which is what put the
    project over. See migrations/20260829_unknown_candidates.sql for the
    measurement.

    The inversion: the scraper never wanted the table, it wanted an answer.
    Sending ~30,700 candidate ids UP is ingress and is not billed; the reply is
    only the genuinely-new ids, typically single digits. Egress per run drops
    from megabytes to bytes.

    Fails OPEN, deliberately and unchanged from the function it replaces: on
    error every candidate is reported unknown, so a Supabase blip costs a
    re-classification rather than a halted scrape. That is safe in a way it was
    not before -- claim_notification() now fails CLOSED, so re-processing can
    no longer turn into a burst of duplicate pushes.

    Answers about STORED rows only. Two listings in the same sweep can share a
    norm_key without either being stored yet, so callers must still dedup
    within their own batch.
    """
    rows = list(jobs)
    if not rows:
        return set()

    ids: list[str] = []
    norm_keys: list[str] = []
    for j in rows:
        job_id = str(j.get("id") or "")
        if not job_id:
            continue
        ids.append(job_id)
        norm_keys.append(
            str(j.get("norm_key") or make_norm_key(j.get("company", ""), j.get("title", "")))
        )

    unknown: set[str] = set()
    try:
        client = get_client()
        for offset in range(0, len(ids), batch_size):
            result = client.rpc("unknown_candidates", {
                "p_ids": ids[offset:offset + batch_size],
                "p_norm_keys": norm_keys[offset:offset + batch_size],
            }).execute()
            for row in result.data or []:
                # A setof text comes back as bare strings; tolerate the wrapped
                # {"unknown_candidates": "..."} shape too rather than silently
                # returning an empty set, which would look like "nothing is new".
                unknown.add(row if isinstance(row, str) else next(iter(row.values())))
    except Exception as exc:
        message = str(exc).lower()
        if ("pgrst202" in message
                or "could not find the function" in message
                or ("schema cache" in message and "function" in message)):
            # Persistent, not transient. See DedupUnavailable.
            raise DedupUnavailable(
                "unknown_candidates() is missing — apply "
                "migrations/20260829_unknown_candidates.sql. Refusing to treat "
                f"{len(ids)} candidates as new, which would cost one write each, "
                "every run."
            ) from exc
        # Transient: rare and self-correcting, so fail open. A wasted pass is
        # cheap; skipping a real job is not.
        log.error("Candidate dedup lookup failed (%s) — treating every candidate as new", exc)
        return set(ids)

    return unknown


def get_job_row(job_id: str) -> Optional[dict]:
    """Point-lookup the fields the notify decision reads, or None if absent.

    This is the guard of last resort for re-notification. Every other dedup
    path in this module fails OPEN by design -- find_unknown_candidates and
    find_known_candidates both report every candidate as new on error, so a
    transient Supabase blip degrades into "do the work again" rather than "stop
    scraping". That is the right trade for *work*, and the wrong one for
    *notifications*: treating everything as new makes every listing look
    unseen, and insert_job's ON CONFLICT DO NOTHING then returns True for rows
    that already existed, so the caller pushes all of them a second time.

    A primary-key lookup of four narrow columns is ~100 bytes on the wire and
    runs only for candidates that already survived dedup -- single digits per
    run, not per listing -- so this costs nothing measurable against the
    egress budget that forced the 5-minute poll.

    Returns None on error, which keeps the caller's existing fail-open
    behaviour unchanged. The database-side claim (see claim_notification) is
    what makes the notify decision fail CLOSED; this only stops the common
    case cheaply and, in doing so, also skips a description fetch and a
    Claude call for every job that was already stored.
    """
    if not job_id:
        return None
    try:
        result = (
            get_client().table("jobs")
            # Deliberately NOT selecting notified_at: it does not exist until the
            # notification-ledger migration lands, and PostgREST 400s on an unknown
            # column, which the except below would swallow into None -- silently
            # disabling this guard on every call.
            .select("id,tier,status")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None
    except Exception as exc:
        log.warning("Existence check failed for %s (%s) — proceeding as if new", job_id, exc)
        return None


def _start_run_legacy() -> Optional[int]:
    """Acquire the original global lock for databases not migrated yet."""
    client = get_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
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


def start_run(source: str = "linkedin") -> Optional[int]:
    """Record a run and acquire a source-specific lock.

    LinkedIn, ATS, and GitHub polling are independent. A global lock caused the
    punctual LinkedIn dispatch to lose a race with the five-minute ATS watcher
    and exit without searching at all. Locks are now scoped by source, while
    the main and fast LinkedIn workflows both use ``source="linkedin"`` so two
    LinkedIn passes still cannot overlap.

    Existing databases without scrape_runs.source fall back to the legacy
    global lock until the migration in migrations/ is applied. Returns the new
    run id, -1 when stats/locking are unavailable, or None when this source is
    already active.
    """
    try:
        client = get_client()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        active = (
            client.table("scrape_runs")
            .select("id")
            .eq("source", source)
            .gte("started_at", cutoff)
            .is_("finished_at", "null")
            .execute()
        )
        if active.data:
            return None
        result = (
            client.table("scrape_runs")
            .insert({
                "source": source,
                "started_at": datetime.now(timezone.utc).isoformat(),
            })
            .execute()
        )
        return result.data[0]["id"] if result.data else -1
    except Exception as exc:
        # PostgREST reports a missing column through its schema-cache error.
        # Keep old deployments safe until their one-line migration is run.
        message = str(exc).lower()
        if "source" in message and ("column" in message or "schema cache" in message):
            log.warning("scrape_runs.source is not migrated yet — using the legacy global lock")
            try:
                return _start_run_legacy()
            except Exception as legacy_exc:
                log.warning("scrape_runs table unavailable (%s) — proceeding without run-lock/stats",
                            legacy_exc)
                return -1
        log.warning("scrape_runs table unavailable (%s) — proceeding without run-lock/stats", exc)
        return -1


def finish_run(run_id: Optional[int], **stats) -> None:
    """Mark a run finished and record its stats. No-op if there's no real run id."""
    if not run_id or run_id < 0:
        return
    try:
        client = get_client()
        client.table("scrape_runs").update({
            "finished_at": datetime.now(timezone.utc).isoformat(),
            **stats,
        }).eq("id", run_id).execute()
    except Exception as exc:
        log.error("Failed to record run completion for run %s: %s", run_id, exc)


def insert_job(job: dict) -> bool:
    """Insert a classified job into Supabase. Returns True on success, False on failure.

    NOTE the asymmetry with update_job_classification(): this upserts with
    ignore_duplicates=True (ON CONFLICT DO NOTHING), so calling it for a row
    that already exists changes NOTHING. Promoting a parked PENDING row must go
    through update_job_classification, not through here.
    """
    client = get_client()
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
        # Identity is a property of FIRST SIGHT, like norm_key and found_at:
        # update_job_classification deliberately never touches it. None is the
        # honest value for an apply URL we cannot positively identify, and the
        # SQL sibling check relies on that -- `= NULL` never matches, so an
        # unidentifiable row can never suppress another row's notification.
        "target_key": definitive_target_key(job),
        "tier": job.get("tier", "APPLY_CAVEAT"),
        "reason": job.get("reason", ""),
        "suggested_resume": job.get("suggested_resume", "General"),
        "posted_at": job.get("posted_at"),
        "apply_url": job.get("apply_url"),
        "is_easy_apply": job.get("is_easy_apply", False),
        "salary": job.get("salary"),
    }
    try:
        client.table("jobs").upsert(payload, on_conflict="id", ignore_duplicates=True).execute()
        log.info("DB: stored %s [%s]", job.get("id"), job.get("tier"))
        return True
    except Exception as exc:
        log.error("DB insert failed for job %s: %s", job.get("id"), exc)
        return False


def claim_notification(job_id: str) -> tuple[bool, str]:
    """Claim the exclusive right to push about this row. Returns (allowed, why).

    This is the ONE decision in the scraper that fails CLOSED, and the asymmetry
    is deliberate. Everywhere else a Supabase error degrades into "do the work
    again", which is merely expensive. Here it must degrade into silence: an
    error that made this return True is exactly how a blip turns into a burst of
    duplicate notifications, which is the failure the user actually feels.

    Postgres, not Python, makes the call -- see
    migrations/20260829_notification_ledger.sql. It takes advisory locks on
    every identity the row participates in, so three concurrent workflows
    racing on one posting produce three rows and ONE push, without depending on
    the GitHub Actions concurrency groups or on scrape_runs' 20-minute
    staleness window being correct.
    """
    try:
        result = get_client().rpc("claim_job_notification", {"p_id": job_id}).execute()
        rows = result.data or []
        row = rows[0] if isinstance(rows, list) and rows else rows
        if not isinstance(row, dict) or "should_notify" not in row:
            log.error("claim_job_notification returned %r for %s — not notifying", result.data, job_id)
            return False, "claim-malformed"
        return bool(row["should_notify"]), str(row.get("reason") or "")
    except Exception as exc:
        # "The function does not exist" means the migration has not been applied
        # yet. Fall open ONLY here, so deploy order is free and a
        # code-before-migration rollout keeps notifying as it does today rather
        # than going silent.
        #
        # Matched on BOTH the PostgREST error code and its prose, lowercased,
        # because the two existing detectors in this repo disagree about which
        # is available: the dashboard reads a structured body.code === 'PGRST202'
        # (route.ts), while start_run() above matches lowercased message text
        # because that is all the Python client surfaces. Getting this wrong in
        # the strict direction is the expensive one -- every push would fall into
        # the fail-closed branch below and the pipeline would go silently mute
        # until someone noticed the absence of notifications.
        message = str(exc).lower()
        if ("pgrst202" in message
                or "could not find the function" in message
                or ("schema cache" in message and "function" in message)):
            log.warning("claim_job_notification is not migrated yet — apply "
                        "migrations/20260829_notification_ledger.sql; notifying unguarded")
            return True, "rpc-missing"
        log.error("Notification claim failed for %s (%s) — NOT notifying", job_id, exc)
        return False, "claim-failed"


# ── Pending-classification queue ─────────────────────────────────────────
#
# A job whose classification failed is parked as tier="PENDING" rather than
# dropped, and drained by main.retry_pending() on later runs. PENDING is a
# queue state, never a verdict — see the comment above MAX_CLASSIFY_ATTEMPTS
# in classifier.py for why storing a fake verdict is the one thing this
# pipeline must never do.
#
# No migration needed: jobs.tier is unconstrained text and jobs_tier_idx
# already covers this lookup.

def fetch_pending_jobs(limit: int) -> list[dict]:
    """Parked jobs, oldest first — the oldest are closest to their deadlines.

    Returns [] on any error rather than raising: a DB blip must not take down
    the scrape that follows it.
    """
    try:
        result = (
            get_client().table("jobs")
            .select("*")
            .eq("tier", "PENDING")
            .order("found_at", desc=False)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        log.error("Could not fetch pending jobs: %s", exc)
        return []


def count_pending_jobs() -> int:
    """How many jobs are parked in total — for the canary message."""
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
        log.error("Could not count pending jobs: %s", exc)
        return 0


def update_job_classification(job_id: str, tier: str, reason: str,
                              suggested_resume: str,
                              salary: Optional[str] = None) -> bool:
    """Promote a parked row to a real verdict.

    A real UPDATE, not an upsert, and that is not a style choice: insert_job()
    upserts with ignore_duplicates=True (ON CONFLICT DO NOTHING), so calling it
    again for a row that already exists silently changes nothing. Promotion has
    to go through this.

    Deliberately never touches status, found_at, description, norm_key or
    search_term. found_at means "first seen" and drives dashboard ordering and
    date filters — moving it would misrepresent when the job appeared.
    """
    payload = {
        "tier": tier,
        "reason": reason,
        "suggested_resume": suggested_resume,
    }
    # Only when the classifier actually produced one; never blank an existing value.
    if salary:
        payload["salary"] = salary

    try:
        get_client().table("jobs").update(payload).eq("id", job_id).execute()
        log.info("DB: promoted %s [%s]", job_id, tier)
        return True
    except Exception as exc:
        log.error("DB update failed for job %s: %s", job_id, exc)
        return False


# ── bot_state key/value helpers ──────────────────────────────────────────
# Same table digest.py uses for its send watermark.

def get_state(key: str) -> Optional[str]:
    try:
        result = get_client().table("bot_state").select("value").eq("key", key).execute()
        if result.data:
            return result.data[0]["value"]
    except Exception as exc:
        log.warning("bot_state unavailable reading %s: %s", key, exc)
    return None


def set_state(key: str, value: str) -> None:
    try:
        get_client().table("bot_state").upsert({"key": key, "value": value},
                                               on_conflict="key").execute()
    except Exception as exc:
        log.error("Could not write bot_state %s: %s", key, exc)


def clear_state(key: str) -> None:
    try:
        get_client().table("bot_state").delete().eq("key", key).execute()
    except Exception as exc:
        log.error("Could not clear bot_state %s: %s", key, exc)
