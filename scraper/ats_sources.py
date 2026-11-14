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


def _epoch_ms_posted_at(created_at: int | float | str | None) -> str | None:
    """Convert Lever's epoch-millisecond createdAt to an ISO timestamp.

    Lever's /v0/postings/ API reports createdAt as an integer number of
    milliseconds since the epoch (1692651521922), where Greenhouse
    (updated_at) and Ashby (publishedAt) both return ISO-8601 strings.
    posted_at is a Postgres timestamp column, so the raw integer was
    rejected with `22008 date/time field value out of range` on EVERY Lever
    row -- 789 failed inserts per sweep, meaning no posting from a Lever
    board was ever stored. The type hint on _make_job said `str | None` all
    along; nothing enforced it.

    Seconds-precision epochs are accepted too: Lever documents milliseconds,
    but a value small enough to be seconds would otherwise land in 1970
    rather than failing loudly.
    """
    if created_at is None or isinstance(created_at, str):
        return created_at
    try:
        seconds = float(created_at)
    except (TypeError, ValueError):
        return None
    if seconds > 1e11:  # milliseconds; 1e11 s is year 5138, so no real epoch-seconds value reaches it
        seconds /= 1000
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        log.warning("Lever createdAt out of range: %r", created_at)
        return None


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
                j.get("hostedUrl", ""), j.get("descriptionPlain"), _epoch_ms_posted_at(j.get("createdAt")),
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
# 300, not 200: RTX lists 4,577 postings (229 pages), and the old cap cut the
# daily full sweep off at 4,000.
_WORKDAY_MAX_PAGES = 300

# How far back the FAST path reads a Workday board. Workday lists postings
# newest first (checked 2026-11-14 on 43 boards at offsets 0/60/200), so once a
# whole page is older than this, everything after it is older too, and a watcher
# that runs every five minutes has already seen it. Without this, the 46 Workday
# boards cost ~1,800 page requests per pass and Northrop Grumman alone (187
# pages at ~1.7 s) took longer than the five-minute schedule.
#
# Two days, not zero: Workday's "Posted Today" is the posting's start date, and
# a job published late can show up already a day old.
WORKDAY_RECENT_DAYS = 2

# A board can only be early-stopped if it really is newest-first. These four
# were not on 2026-11-14 (page medians in days at offsets 0/60/200: Adobe
# 27/12/30, Cisco 4/18/1, CrowdStrike 19/3/17, Intel 17/2/9), so the fast path
# always reads them in full. The daily full sweep re-checks every board and
# ats_watch adds whatever else it finds; this set is the floor, not the list.
WORKDAY_UNSORTED_COMPANIES = frozenset({"Adobe", "Cisco", "CrowdStrike", "Intel"})

# A later page whose median age is this many days NEWER than an earlier page's
# means the board is not sorted by date. Slack for ties and the odd pinned post.
_WORKDAY_SORT_SLACK_DAYS = 3

_WORKDAY_DAYS_AGO = re.compile(r"(\d+)\+?\s+days?\s+ago", re.I)


def _workday_age_days(posted_on: str | None) -> int | None:
    """Days since posting, from Workday's display text ("Posted Today",
    "Posted Yesterday", "Posted 3 Days Ago", "Posted 30+ Days Ago"), or None
    when the tenant omits it or words it some other way."""
    if not posted_on:
        return None
    text = posted_on.strip().lower()
    if "today" in text:
        return 0
    if "yesterday" in text:
        return 1
    m = _WORKDAY_DAYS_AGO.search(text)
    return int(m.group(1)) if m else None


def _workday_posted_at(posted_on: str | None) -> str | None:
    """Convert Workday's relative postedOn string to an ISO timestamp.

    Workday reports recency as display text ("Posted Yesterday", "Posted 3
    Days Ago", "Posted 30+ Days Ago") rather than a date, and omits the
    field entirely on some tenants. Every other fetcher puts an ISO-8601
    string in posted_at, so normalise here instead of leaking prose into
    the column.
    """
    days = _workday_age_days(posted_on)
    if days is None:
        return None
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _median(values: list[int]) -> float:
    s = sorted(values)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def workday_sorted_by_date(page_medians: list[float]) -> bool | None:
    """True if a board's pages run newest to oldest, False if a later page is
    clearly newer than an earlier one, None if there are too few pages to say."""
    if len(page_medians) < 2:
        return None
    oldest_so_far = page_medians[0]
    for m in page_medians[1:]:
        if m < oldest_so_far - _WORKDAY_SORT_SLACK_DAYS:
            return False
        oldest_so_far = max(oldest_so_far, m)
    return True


def fetch_workday_listings(company: str, token: str, full: bool = True,
                           sort_report: dict | None = None) -> list[dict]:
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
    /wday/cxs/<tenant>/<site>/jobs and is paginated by offset. With
    full=True this walks every page so the caller gets the whole board. With
    full=False it stops at the first page whose postings are ALL older than
    WORKDAY_RECENT_DAYS, which on a newest-first board is everything a
    five-minute watcher can still be missing. A posting with no readable age
    counts as recent, so a tenant that omits postedOn is always read in full.

    sort_report, if given, receives {token: True/False/None}: whether this
    board's pages ran newest to oldest (see workday_sorted_by_date). Only a
    full read can answer that.

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
    page_medians: list[float] = []
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
            ages: list[int | None] = []
            for p in postings:
                path = p.get("externalPath", "")
                if not path or path in seen_paths:
                    continue  # wrap-around guard, see _WORKDAY_MAX_PAGES
                seen_paths.add(path)
                new_on_page += 1
                ages.append(_workday_age_days(p.get("postedOn")))
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

            known = [a for a in ages if a is not None]
            if known:
                page_medians.append(_median(known))

            # The whole page is old, and every age on it was readable: on a
            # newest-first board nothing further down is new.
            if (not full and len(known) == len(ages)
                    and all(a > WORKDAY_RECENT_DAYS for a in known)):
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
    finally:
        if sort_report is not None and full:
            sort_report[token] = workday_sorted_by_date(page_medians)


_FETCHERS = {
    "greenhouse": fetch_greenhouse_listings,
    "lever": fetch_lever_listings,
    "ashby": fetch_ashby_listings,
    "smartrecruiters": fetch_smartrecruiters_listings,
    "workday": fetch_workday_listings,
}


def fetch_company_listings(company: str, platform: str, token: str, **workday_opts) -> list[dict]:
    """workday_opts (full, sort_report) reach the Workday fetcher only: it is the
    one platform that pages, so the only one where a partial read means anything."""
    fetcher = _FETCHERS.get(platform)
    if fetcher is None:
        log.error("Unknown ATS platform '%s' for %s — skipping", platform, company)
        return []
    if platform == "workday":
        return fetcher(company, token, **workday_opts)
    return fetcher(company, token)


# Sized for ~160 boards on ~160 unrelated hosts. Higher gains little — the
# sweep converges on the slowest single board (a large Workday tenant paging
# 20 rows per POST) — and this is polite enough that no one host sees more
# than one connection from us anyway.
_SWEEP_WORKERS = 12


def fetch_all_listings(companies: dict, full: bool = True, always_full: frozenset = frozenset(),
                       sort_report: dict | None = None) -> list[dict]:
    """Fetch every configured company's open-jobs list, in parallel.

    companies is ats_config.ATS_COMPANIES-shaped: {name: {platform, token}}.

    full=False reads Workday boards only back to WORKDAY_RECENT_DAYS (see
    fetch_workday_listings), except the tokens in always_full, which are read
    in full regardless because they are not sorted newest first. The other
    platforms answer in one request either way. sort_report is passed to every
    Workday board read in full (see fetch_workday_listings).

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
    def opts(cfg: dict) -> dict:
        if cfg["platform"] != "workday":
            return {}
        return {"full": full or cfg["token"] in always_full, "sort_report": sort_report}

    all_jobs: list[dict] = []
    with ThreadPoolExecutor(max_workers=_SWEEP_WORKERS) as pool:
        futures = {
            pool.submit(fetch_company_listings, company, cfg["platform"], cfg["token"], **opts(cfg)):
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
