"""Fetch real job descriptions from external ATS platforms.

github_sources.py only scrapes company/title/location/apply_url from the
tracker README tables — no description — so GitHub-sourced jobs were being
classified almost blind (title/company/location only). This fills that gap
by hitting each ATS's public JSON API directly, keyed off the apply_url's
domain. Covers the four platforms that make up the bulk of tracked listings
(Greenhouse, Lever, Ashby, Workday); anything else falls back to a best-effort
generic HTML text scrape, which may or may not work depending on the site.

Every fetcher is best-effort: on any failure, returns None rather than
raising, so a broken/unrecognized posting just falls back to the previous
title-only classification instead of blocking the run.
"""

import html
import logging
import re
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}
# Was 4000 — a live DB check found 54.5% of stored descriptions were hitting
# that cap exactly (median length == cap), silently truncating out whatever
# came after it. Requirements/eligibility sections typically sit near the
# END of a posting (after the company blurb, responsibilities, etc.), so
# this was a systemic way for a hard disqualifier to never reach the
# classifier at all — not just the school-specific-co-op case that
# surfaced it. Haiku is cheap enough that a 3x cap costs nothing meaningful.
MAX_LEN = 12000


def fetch_external_description(apply_url: str) -> Optional[str]:
    if not apply_url:
        return None
    domain = urlparse(apply_url).netloc.lower()

    try:
        if "greenhouse.io" in domain:
            return _fetch_greenhouse(apply_url)
        if "lever.co" in domain:
            return _fetch_lever(apply_url)
        if "ashbyhq.com" in domain:
            return _fetch_ashby(apply_url)
        if "myworkdayjobs.com" in domain:
            return _fetch_workday(apply_url)
        if "icims.com" in domain:
            return _fetch_icims(apply_url)
        return _fetch_generic(apply_url)
    except Exception as exc:
        log.debug("External description fetch failed for %s: %s", apply_url, exc)
        return None


def _html_to_text(raw_html: str) -> str:
    return BeautifulSoup(raw_html, "lxml").get_text(separator=" ", strip=True)[:MAX_LEN]


def _fetch_greenhouse(apply_url: str) -> Optional[str]:
    # https://job-boards.greenhouse.io/{board}/jobs/{job_id}
    m = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", apply_url)
    if not m:
        return None
    board, job_id = m.group(1), m.group(2)
    resp = requests.get(
        f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}",
        headers=HEADERS, timeout=15,
    )
    if not resp.ok:
        return None
    content = resp.json().get("content", "")
    if not content:
        return None
    # Greenhouse's API delivers this field HTML-entity-escaped (literal
    # "&lt;p&gt;" instead of "<p>") — unescape before stripping tags.
    return _html_to_text(html.unescape(content)) or None


def _fetch_lever(apply_url: str) -> Optional[str]:
    # https://jobs.lever.co/{company}/{posting_id}[/apply]
    m = re.search(r"lever\.co/([^/]+)/([0-9a-fA-F-]{36})", apply_url)
    if not m:
        return None
    company, posting_id = m.group(1), m.group(2)
    resp = requests.get(
        f"https://api.lever.co/v0/postings/{company}/{posting_id}",
        headers=HEADERS, timeout=15,
    )
    if not resp.ok:
        return None
    data = resp.json()
    text = data.get("descriptionPlain", "")
    if not text:
        return None
    return text[:MAX_LEN]


def _fetch_ashby(apply_url: str) -> Optional[str]:
    # https://jobs.ashbyhq.com/{org}/{posting_id}[/application]
    m = re.search(r"ashbyhq\.com/([^/]+)/([0-9a-fA-F-]{36})", apply_url)
    if not m:
        return None
    org, posting_id = m.group(1), m.group(2)
    resp = requests.get(
        f"https://api.ashbyhq.com/posting-api/job-board/{org}",
        headers=HEADERS, timeout=15,
    )
    if not resp.ok:
        return None
    jobs = resp.json().get("jobs", [])
    job = next((j for j in jobs if j.get("id") == posting_id), None)
    if not job:
        return None
    text = job.get("descriptionPlain", "")
    return text[:MAX_LEN] if text else None


def _fetch_workday(apply_url: str) -> Optional[str]:
    # https://{tenant}.{wdN}.myworkdayjobs.com/[{locale}/]{site}/job/{...}
    parsed = urlparse(apply_url)
    tenant = parsed.netloc.split(".")[0]
    path = parsed.path.strip("/")
    if "/job/" not in path:
        return None
    pre_job, job_path = path.split("/job/", 1)
    candidates = [pre_job]
    segments = pre_job.split("/")
    if len(segments) > 1 and re.match(r"^[a-zA-Z]{2}(-[a-zA-Z]{2})?$", segments[0]):
        candidates.append("/".join(segments[1:]))  # drop a leading locale segment

    for site in candidates:
        cxs_url = f"https://{parsed.netloc}/wday/cxs/{tenant}/{site}/job/{job_path}"
        resp = requests.get(cxs_url, headers=HEADERS, timeout=15)
        if not resp.ok:
            continue
        data = resp.json()
        desc_html = data.get("jobPostingInfo", {}).get("jobDescription", "")
        if desc_html:
            return _html_to_text(desc_html) or None
    return None


def _fetch_icims(apply_url: str) -> Optional[str]:
    # The main career-site page is a heavy JS shell with no description in
    # the static HTML, but iCIMS also serves a lightweight server-rendered
    # iframe view (used to embed postings elsewhere) via ?in_iframe=1.
    sep = "&" if "?" in apply_url else "?"
    resp = requests.get(f"{apply_url}{sep}in_iframe=1", headers=HEADERS, timeout=15)
    if not resp.ok:
        return None
    soup = BeautifulSoup(resp.text, "lxml")
    content = soup.select_one("div.iCIMS_JobContent")
    if not content:
        return None
    text = content.get_text(separator=" ", strip=True)
    return text[:MAX_LEN] if len(text) > 200 else None


# Containers career sites actually wrap a posting in, most specific first. The
# generic path used to skip this and take the whole body, which is how cookie
# banners and department menus ended up as "descriptions": decompose() only
# removes <nav>/<header>/<footer>, and these sites use plain <div>s. Measured on
# 124 live rows, 6 were affected and the real job text did not start until char
# 1,725-5,096 — on a 12,000-char budget whose tail is where eligibility rules
# live, that is up to half the window spent on boilerplate.
_JOB_CONTENT_SELECTORS = (
    '[data-automation-id*="jobPostingDescription"]',
    '[class*="job-description"]',
    '[class*="jobDescription"]',
    '[id*="job-description"]',
    '[id*="jobDescription"]',
    '[class*="job-details"]',
    '[class*="jobDetail"]',
    '[class*="posting-description"]',
    "article",
    '[role="main"]',
    "main",
)


# Site furniture, matched on a node's id/class. Subtractive cleanup, because
# the selector list above cannot reach every site: SAP SuccessFactors boards
# (Grainger, WEC Energy) render the posting body in bare <div>s with no id or
# class at all, so there is no container to select.
_CHROME_IDENT = re.compile(
    r"cookie|consent|gdpr|privacy|nav|menu|dropdown|breadcrumb|footer"
    r"|search-?(?:bar|box|form|widget)|job-?alert|alert-?(?:box|signup)"
    r"|social|share|skip-?link|banner|masthead|sidebar|toolbar",
    re.I,
)


def _strip_chrome(soup: BeautifulSoup) -> None:
    """Drop nodes whose id/class marks them as site furniture, in place."""
    # Collect before decomposing: mutating during find_all's iteration leaves
    # later tags detached, and tag.get() on a decomposed node raises.
    doomed = []
    for tag in soup.find_all(["div", "section", "aside", "ul", "form", "span"]):
        ident = f"{tag.get('id') or ''} {' '.join(tag.get('class') or [])}"
        if ident.strip() and _CHROME_IDENT.search(ident):
            doomed.append(tag)
    for tag in doomed:
        if not tag.decomposed:
            tag.decompose()


def _extract_job_content(soup: BeautifulSoup) -> Optional[str]:
    """Return just the posting's own content, or None if no container matched.

    Deliberately extraction, not rejection. A reject-the-page heuristic was
    tried against the 6 real chrome dumps and caught only 2 of them while
    wrongly rejecting 5 good descriptions — these pages DO contain genuine job
    vocabulary, just several kilobytes down. Finding the container works;
    scoring the text does not.
    """
    for selector in _JOB_CONTENT_SELECTORS:
        for node in soup.select(selector):
            text = node.get_text(separator=" ", strip=True)
            if len(text) > 200:
                return text[:MAX_LEN]
    return None


def _fetch_generic(apply_url: str) -> Optional[str]:
    """Best-effort fallback for ATS platforms without a known public API."""
    resp = requests.get(apply_url, headers=HEADERS, timeout=15)
    if not resp.ok:
        return None
    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    _strip_chrome(soup)

    scoped = _extract_job_content(soup)
    if scoped:
        return scoped

    # No recognizable container — fall back to the whole page, which is what
    # this function always did. Sites that work today keep working; the log
    # line is so the gap is visible in Actions output instead of silent.
    #
    # HONEST LIMIT: _strip_chrome measurably shrinks these pages (Grainger
    # 10,467 -> ~6,500, WEC 8,602 -> ~7,000, both verified live) but does NOT
    # fully clean the head on SuccessFactors — a job-alert widget without a
    # matching id/class still leads. That is a budget improvement, not a
    # guarantee the first line is job text. It is worth having because the
    # 12,000-char cap truncates from the END, where eligibility rules live.
    log.info("No job-content container matched, using whole-page text: %s", apply_url)
    text = soup.get_text(separator=" ", strip=True)
    # A too-short result usually means the real content is client-rendered
    # (SPA) and we only got an empty shell — not worth passing to the
    # classifier as if it were a real description.
    return text[:MAX_LEN] if len(text) > 200 else None
