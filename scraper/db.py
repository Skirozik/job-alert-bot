"""Supabase client, dedup logic, and job insertion.

Dedup normalization is adapted from the existing dedup.py in the cowork folder.
"""

import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_SERVICE_KEY

log = logging.getLogger(__name__)

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
    r = re.sub(r"[-–—]\s*(fall|spring|summer|winter)\s*20\d\d\s*$", "", r)
    r = re.sub(r"\((fall|spring|summer|winter)\s*20\d\d\)", "", r)
    r = re.sub(r"[^a-z0-9 ]", " ", r)
    r = re.sub(r"\b(internship|intern|co\s*op|coop)\b", "", r)
    r = re.sub(r"\s+", " ", r)
    return r.strip()


def make_norm_key(company: str, title: str) -> str:
    return f"{norm_company(company)}|{norm_role(title)}"


def load_dedup_index() -> tuple[set[str], set[str]]:
    """Fetch every known job id + norm_key once, for in-memory dedup.

    Replaces the old per-listing "2 Supabase queries per job" approach (up to
    ~1,400 queries/run across all search terms/pages). One paginated fetch of
    two narrow columns is far cheaper, and lets the caller distinguish "this
    listing is a real DB duplicate" from "I've merely already queued it under
    another search term this run" — the two were conflated before, which
    could cut pagination short and miss listings.

    On a transient DB error, returns empty sets so every listing is treated
    as new for this run rather than aborting the whole scrape; duplicates
    inserted this way are harmlessly caught by Supabase's upsert on job id
    (norm_key collisions are the only residual risk, and self-heal next run).
    """
    ids: set[str] = set()
    norm_keys: set[str] = set()
    try:
        client = get_client()
        page_size = 1000
        offset = 0
        while True:
            result = (
                client.table("jobs")
                .select("id, norm_key")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            rows = result.data or []
            for row in rows:
                ids.add(row["id"])
                if row.get("norm_key"):
                    norm_keys.add(row["norm_key"])
            if len(rows) < page_size:
                break
            offset += page_size
    except Exception as exc:
        log.error("Failed to load dedup index: %s — treating all listings as new this run", exc)
        return set(), set()
    return ids, norm_keys


def start_run() -> Optional[int]:
    """Record the start of a scrape run and acquire a simple run-lock.

    Guards against two schedulers (e.g. GitHub Actions + Modal) firing within
    the same window and double-processing/double-notifying. Returns the new
    run's id, -1 if the scrape_runs table doesn't exist yet (proceeds without
    locking/stats — see README for the migration), or None if another run
    looks to still be in progress and this run should be skipped.
    """
    try:
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
    except Exception as exc:
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
    """Insert a classified job into Supabase. Returns True on success, False on failure."""
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
        "tier": job.get("tier", "MAYBE"),
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
