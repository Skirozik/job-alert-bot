"""Direct ATS job-board listing fetchers — the fast-path source.

Unlike external_descriptions.py (which fetches ONE job's description, keyed
off an apply_url already discovered elsewhere), these functions fetch a
company's ENTIRE open-jobs list directly from its ATS's public API. This is
what lets a specific company's new posting be caught within minutes of
going live, instead of waiting for LinkedIn's own syndication lag (which
ATS vendors themselves document as commonly 6-48+ hours).

Greenhouse, Lever, and Ashby all return a full job description in the same
listing call, so those three populate `description` immediately — no
follow-up detail fetch needed (cheaper than the LinkedIn or GitHub-tracker
paths, which both need a second request per job). SmartRecruiters' listing
endpoint does not include a description; those jobs get classified on
title/company/location only, same as any other job the description fetch
failed for elsewhere in the pipeline.

Every fetcher is best-effort: a failure for one company (bad token, ATS
outage, network error) is logged and returns an empty list rather than
raising, so one broken entry in ats_config.py never blocks the other 49.
"""

import hashlib
import html
import logging
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}
TIMEOUT = 15
MAX_LEN = 12000  # matches linkedin.py/external_descriptions.py's cap


def _make_job(company: str, title: str, location: str, url: str, description: str | None,
              posted_at: str | None) -> dict | None:
    if not company or not title or not url:
        return None
    return {
        "id": "ats:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
        "title": title,
        "company": company,
        "location": location or "",
        "url": url,
        "apply_url": url,
        "posted_at": posted_at,
        "description": description[:MAX_LEN] if description else None,
        "is_easy_apply": False,
        "search_term": "ats-watch",
    }


def fetch_greenhouse_listings(company: str, token: str) -> list[dict]:
    try:
        resp = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
            params={"content": "true"}, headers=HEADERS, timeout=TIMEOUT,
        )
        if not resp.ok:
            log.warning("Greenhouse fetch failed for %s (token=%s): HTTP %s", company, token, resp.status_code)
            return []
        jobs = resp.json().get("jobs", [])
        result = []
        for j in jobs:
            content = j.get("content", "")
            desc = html.unescape(content) if content else None
            job = _make_job(
                company, j.get("title", ""), j.get("location", {}).get("name", ""),
                j.get("absolute_url", ""), desc, j.get("updated_at"),
            )
            if job:
                result.append(job)
        return result
    except Exception as exc:
        log.error("Greenhouse fetch error for %s (token=%s): %s", company, token, exc)
        return []


def fetch_lever_listings(company: str, token: str) -> list[dict]:
    try:
        resp = requests.get(
            f"https://api.lever.co/v0/postings/{token}",
            params={"mode": "json"}, headers=HEADERS, timeout=TIMEOUT,
        )
        if not resp.ok:
            log.warning("Lever fetch failed for %s (token=%s): HTTP %s", company, token, resp.status_code)
            return []
        jobs = resp.json()
        result = []
        for j in jobs:
            job = _make_job(
                company, j.get("text", ""), j.get("categories", {}).get("location", ""),
                j.get("hostedUrl", ""), j.get("descriptionPlain"), j.get("createdAt"),
            )
            if job:
                result.append(job)
        return result
    except Exception as exc:
        log.error("Lever fetch error for %s (token=%s): %s", company, token, exc)
        return []


def fetch_ashby_listings(company: str, token: str) -> list[dict]:
    try:
        resp = requests.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{token}",
            headers=HEADERS, timeout=TIMEOUT,
        )
        if not resp.ok:
            log.warning("Ashby fetch failed for %s (token=%s): HTTP %s", company, token, resp.status_code)
            return []
        jobs = resp.json().get("jobs", [])
        result = []
        for j in jobs:
            job = _make_job(
                company, j.get("title", ""), j.get("location", ""),
                j.get("jobUrl") or j.get("applyUrl", ""), j.get("descriptionPlain"), j.get("publishedAt"),
            )
            if job:
                result.append(job)
        return result
    except Exception as exc:
        log.error("Ashby fetch error for %s (token=%s): %s", company, token, exc)
        return []


def fetch_smartrecruiters_listings(company: str, token: str) -> list[dict]:
    # SmartRecruiters' listing endpoint doesn't include a description —
    # jobs come back with description=None and get classified on
    # title/company/location, same as any job whose detail fetch failed.
    try:
        resp = requests.get(
            f"https://api.smartrecruiters.com/v1/companies/{token}/postings",
            headers=HEADERS, timeout=TIMEOUT,
        )
        if not resp.ok:
            log.warning("SmartRecruiters fetch failed for %s (token=%s): HTTP %s", company, token, resp.status_code)
            return []
        postings = resp.json().get("content", [])
        result = []
        for p in postings:
            loc = p.get("location", {})
            location = ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")]))
            ref = p.get("ref", "")
            job = _make_job(
                company, p.get("name", ""), location, ref, None, p.get("releasedDate"),
            )
            if job:
                result.append(job)
        return result
    except Exception as exc:
        log.error("SmartRecruiters fetch error for %s (token=%s): %s", company, token, exc)
        return []


_FETCHERS = {
    "greenhouse": fetch_greenhouse_listings,
    "lever": fetch_lever_listings,
    "ashby": fetch_ashby_listings,
    "smartrecruiters": fetch_smartrecruiters_listings,
}


def fetch_company_listings(company: str, platform: str, token: str) -> list[dict]:
    fetcher = _FETCHERS.get(platform)
    if fetcher is None:
        log.error("Unknown ATS platform '%s' for %s — skipping", platform, company)
        return []
    return fetcher(company, token)


def fetch_all_listings(companies: dict) -> list[dict]:
    """Fetch every configured company's open-jobs list. companies is
    ats_config.ATS_COMPANIES-shaped: {name: {platform, token}}."""
    all_jobs: list[dict] = []
    for company, cfg in companies.items():
        jobs = fetch_company_listings(company, cfg["platform"], cfg["token"])
        log.info("ATS %s (%s): %d listings", company, cfg["platform"], len(jobs))
        all_jobs.extend(jobs)
    return all_jobs
