"""Supabase client, dedup logic, and job insertion.

Dedup normalization is adapted from the existing dedup.py in the cowork folder.
"""

import re
import logging
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
    toks = [t for t in toks if t not in _COMPANY_NOISE]
    return " ".join(toks).strip()


def norm_role(r: str) -> str:
    r = (r or "").lower().strip()
    r = re.sub(r"[-–—].*?(fall|spring|summer|winter)\s*20\d\d.*$", "", r)
    r = re.sub(r"\((fall|spring|summer|winter)\s*20\d\d\)", "", r)
    r = re.sub(r"[^a-z0-9 ]", " ", r)
    r = re.sub(r"\b(internship|intern|co\s*op|coop)\b", "", r)
    r = re.sub(r"\s+", " ", r)
    return r.strip()


def make_norm_key(company: str, title: str) -> str:
    return f"{norm_company(company)}|{norm_role(title)}"


def is_duplicate(job_id: str, company: str, title: str) -> bool:
    """Return True if this job is already in the database."""
    client = get_client()

    # Fast path: exact job ID match
    result = client.table("jobs").select("id").eq("id", job_id).execute()
    if result.data:
        return True

    # Fuzzy path: normalized company + role key
    nk = make_norm_key(company, title)
    result = client.table("jobs").select("id").eq("norm_key", nk).execute()
    return bool(result.data)


def insert_job(job: dict) -> None:
    """Insert a classified job into Supabase. Silently skips if already exists."""
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
    except Exception as exc:
        log.error("DB insert failed for job %s: %s", job.get("id"), exc)
