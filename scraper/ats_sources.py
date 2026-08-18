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
paths, which both need a second request per job). SmartRecruiters' and
Workday's listing endpoints do not include a description; those jobs get
classified on title/company/location only, same as any other job the
description fetch failed for elsewhere in the pipeline.

Workday is the odd one out mechanically: its list is a POST to a per-tenant
CXS endpoint and is paginated 20 at a time, so it needs several requests per
company where the other four need exactly one. Its config entry also carries
a data-centre host ("wd1"/"wd5"/...) the others have no equivalent of — see
fetch_workday_listings' docstring for the token format.

Every fetcher is best-effort: a failure for one company (bad token, ATS
outage, network error) is logged and returns an empty list rather than
raising, so one broken entry in ats_config.py never blocks the other 49.
"""

import hashlib
import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

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


# Workday rejects any limit above 20 with HTTP 400 (verified against
# crowdstrike/wd5: limit=20 -> 200, limit=50/100/200 -> 400), so the page
# size is fixed rather than tunable.
_WORKDAY_PAGE_SIZE = 20

# Out-of-range offsets do NOT return an empty page — Workday silently wraps
# back to the first page (verified: offset=500 and offset=100000 against a
# 448-job board both returned page 0 byte-for-byte). A "loop until the page
# comes back empty" termination check would therefore spin forever, so
# pagination is bounded by `total` and this hard cap is the backstop.
_WORKDAY_MAX_PAGES = 200

_WORKDAY_DAYS_AGO = re.compile(r"(\d+)\+?\s+days?\s+ago", re.I)


def _workday_posted_at(posted_on: str | None) -> str | None:
    """Convert Workday's relative postedOn string to an ISO timestamp.

    Workday reports recency as display text ("Posted Yesterday", "Posted 3
    Days Ago", "Posted 30+ Days Ago") rather than a date, and omits the
    field entirely on some tenants. Every other fetcher puts an ISO-8601
    string in posted_at, so normalise here instead of leaking prose into
    the column.
    """
    if not posted_on:
        return None
    text = posted_on.strip().lower()
    days: int | None = None
    if "today" in text:
        days = 0
    elif "yesterday" in text:
        days = 1
    else:
        m = _WORKDAY_DAYS_AGO.search(text)
        if m:
            days = int(m.group(1))
    if days is None:
        return None
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def fetch_workday_listings(company: str, token: str) -> list[dict]:
    """Fetch a Workday-hosted board via its public CXS API.

    Token format (3 colon-separated parts, no scheme, no slashes):

        "<tenant>:<host>:<site>"

    taken straight off the board's public URL —
    https://<tenant>.<host>.myworkdayjobs.com/<site> — e.g. the board at
    https://crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers is
    "crowdstrike:wd5:crowdstrikecareers".

      tenant  first DNS label ("crowdstrike", "unitytech", "goodrx")
      host    Workday data-centre label, WITHOUT ".myworkdayjobs.com"
              ("wd1", "wd3", "wd5", "wd12", ...) — this is the piece the
              other four ATSes have no equivalent of, since every tenant
              lives on whichever cell Workday assigned it
      site    external career-site name, case-sensitive, as it appears in
              the path ("crowdstrikecareers", "Etsy_Careers", "Careers")

    Unlike the other four, the job list is a POST to
    /wday/cxs/<tenant>/<site>/jobs and is paginated by offset; this walks
    every page so the caller gets the full board, not just the first 20.

    Like SmartRecruiters, the listing response carries no description —
    these jobs come back with description=None and are classified on
    title/company/location.
    """
    parts = token.split(":")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        log.error("Workday token for %s must be 'tenant:host:site', got %r — skipping", company, token)
        return []
    tenant, host, site = (p.strip() for p in parts)

    base = f"https://{tenant}.{host}.myworkdayjobs.com"
    endpoint = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    headers = {**HEADERS, "Accept": "application/json", "Content-Type": "application/json"}

    result: list[dict] = []
    seen_paths: set[str] = set()
    try:
        offset = 0
        total: int | None = None
        for _ in range(_WORKDAY_MAX_PAGES):
            resp = requests.post(
                endpoint,
                json={"appliedFacets": {}, "limit": _WORKDAY_PAGE_SIZE,
                      "offset": offset, "searchText": ""},
                headers=headers, timeout=TIMEOUT,
            )
            if not resp.ok:
                # Keep whatever earlier pages produced — a page-3 blip
                # shouldn't discard 40 good jobs.
                log.warning("Workday fetch failed for %s (token=%s) at offset %d: HTTP %s",
                            company, token, offset, resp.status_code)
                break

            payload = resp.json()
            postings = payload.get("jobPostings", [])
            if not postings:
                break

            # `total` is only populated on the first page — every later
            # page reports total=0 (verified against crowdstrike/wd5:
            # offset=0 -> 448, offset=20/40/60 -> 0). Latch the first
            # page's value; trusting the per-page one truncates every
            # board to two pages.
            if total is None:
                page_total = payload.get("total")
                total = page_total if isinstance(page_total, int) and page_total > 0 else None

            new_on_page = 0
            for p in postings:
                path = p.get("externalPath", "")
                if not path or path in seen_paths:
                    continue  # wrap-around guard, see _WORKDAY_MAX_PAGES
                seen_paths.add(path)
                new_on_page += 1
                job = _make_job(
                    company, p.get("title", ""), p.get("locationsText", ""),
                    f"{base}/{site}{path}", None, _workday_posted_at(p.get("postedOn")),
                )
                if job:
                    result.append(job)

            # Every posting was one we'd already seen: Workday has wrapped
            # us back to an earlier page, so the board is exhausted.
            if new_on_page == 0:
                break

            offset += _WORKDAY_PAGE_SIZE
            if len(postings) < _WORKDAY_PAGE_SIZE or (total is not None and offset >= total):
                break
        else:
            log.warning("Workday pagination for %s (token=%s) hit the %d-page cap",
                        company, token, _WORKDAY_MAX_PAGES)
        return result
    except Exception as exc:
        log.error("Workday fetch error for %s (token=%s): %s", company, token, exc)
        return result


_FETCHERS = {
    "greenhouse": fetch_greenhouse_listings,
    "lever": fetch_lever_listings,
    "ashby": fetch_ashby_listings,
    "smartrecruiters": fetch_smartrecruiters_listings,
    "workday": fetch_workday_listings,
}


def fetch_company_listings(company: str, platform: str, token: str) -> list[dict]:
    fetcher = _FETCHERS.get(platform)
    if fetcher is None:
        log.error("Unknown ATS platform '%s' for %s — skipping", platform, company)
        return []
    return fetcher(company, token)


# Sized for ~100 boards on ~100 unrelated hosts. Higher gains little — the
# sweep converges on the slowest single board (a large Workday tenant paging
# 20 rows per POST) — and this is polite enough that no one host sees more
# than one connection from us anyway.
_SWEEP_WORKERS = 12


def fetch_all_listings(companies: dict) -> list[dict]:
    """Fetch every configured company's open-jobs list, in parallel.

    companies is ats_config.ATS_COMPANIES-shaped: {name: {platform, token}}.

    Parallel because the boards are independent hosts and the sequential
    sweep was most of this fast path's latency budget: ~100 boards and
    ~30,000 postings fetched one after another took minutes per pass (Workday
    tenants alone need dozens of back-to-back 20-row POSTs each), and held
    the run-lock long enough that overlapping github_watch passes were
    skipped outright. Every fetcher is self-contained and fails soft
    (returns [] and logs), each request is an independent requests call with
    no shared session, and results are only combined here — so threads change
    nothing but the wall-clock, which drops to roughly the slowest single
    board.

    The returned order follows completion, not config order. Nothing
    downstream cares: ats_watch diffs against the dedup index by id.
    """
    all_jobs: list[dict] = []
    with ThreadPoolExecutor(max_workers=_SWEEP_WORKERS) as pool:
        futures = {
            pool.submit(fetch_company_listings, company, cfg["platform"], cfg["token"]):
                (company, cfg["platform"])
            for company, cfg in companies.items()
        }
        for future in as_completed(futures):
            company, platform = futures[future]
            try:
                jobs = future.result()
            except Exception as exc:
                # The fetchers all catch their own exceptions; this guard
                # preserves the module's "one broken entry never blocks the
                # other 49" contract even if one ever leaks.
                log.error("ATS %s (%s): unexpected sweep error: %s", company, platform, exc)
                continue
            log.info("ATS %s (%s): %d listings", company, platform, len(jobs))
            all_jobs.extend(jobs)
    return all_jobs
