"""Employers whose jobs live only on their own careers sites, read from the
sitemaps those sites publish for crawlers.

Some employers never syndicate a role to LinkedIn or the GitHub trackers, and
run their own careers site rather than a Greenhouse/Lever/Workday board the
ATS fetchers already read. Meta's "Software Engineering Intern" (2026-10-06)
was the case that exposed it: on metacareers.com only, so no source this bot
reads could ever have seen it.

What these sites DO publish is a sitemap -- a list of every job page, offered
to search-engine crawlers on purpose (Meta names it in its own robots.txt).
This module reads those sitemaps and nothing else that a crawler is not meant
to read: no search pages, no private APIs, nothing behind a login. A site that
refuses a request is left alone, never retried around.

  - Meta: the sitemap lists job pages by numeric id with no title, so a page is
    opened once, only the first time its id is seen, and read only as far as
    the schema.org JobPosting block it carries (title, locations, date,
    description). DETAIL_CAP bounds how many are opened per pass; leftovers are
    still unknown next pass, so the first full read spreads over a few passes.
  - Apple, D. E. Shaw: the job title is in the page URL itself, so nothing
    beyond the sitemap is fetched. Those rows are classified on the title alone.

Runs inside ats_watch.py, at most once per MIN_INTERVAL_S (bot_state stamp), so
the sitemaps are fetched about hourly rather than on every 5-minute ATS pass.
Rows reuse the ATS id scheme and job shape, so everything downstream -- dedup,
title pre-filter, classification, pings, the dashboard's "Direct" source -- is
the ATS path unchanged.
"""

import html
import json
import logging
import re
import time
from datetime import datetime, timezone

import requests

from ats_sources import MAX_LEN, _make_job
from db import find_unknown_candidates, get_state, set_state

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job-alert-bot/1.0; +https://github.com/Skirozik/job-alert-bot)"}
TIMEOUT = 30
STATE_KEY = "career_sitemaps_checked_at"
MIN_INTERVAL_S = 3600
DETAIL_CAP = 150      # job pages opened per pass (Meta-style sources only); ~6 min at the polite pace
DETAIL_GAP_S = 1.0    # politeness gap between page opens
# find_unknown_candidates is an RPC whose reply PostgREST caps at 1,000 rows. A
# first pass has thousands of genuinely new ids, so ask in batches small enough
# that no reply can be truncated.
UNKNOWN_BATCH = 500

SOURCES = [
    {"company": "Meta", "sitemap": "https://www.metacareers.com/jobsearch/sitemap.xml",
     "url_re": r"/profile/job_details/\d+/?$", "detail": "jsonld", "location": ""},
    {"company": "Apple", "sitemap": "https://jobs.apple.com/sitemap/sitemap-jobs-en-us.xml",
     "url_re": r"/en-us/details/[^/]+/[a-z0-9-]+$", "detail": "slug", "location": "United States"},
    {"company": "D. E. Shaw", "sitemap": "https://www.deshaw.com/sitemap.xml",
     "url_re": r"/careers/[a-z0-9-]+-\d{3,}$", "detail": "slug", "location": ""},
]


def _sitemap_urls(source: dict) -> list[str]:
    try:
        r = requests.get(source["sitemap"], headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as exc:
        log.warning("Career sitemap %s unavailable: %s", source["company"], type(exc).__name__)
        return []
    locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", r.text)
    return [u for u in locs if re.search(source["url_re"], u)]


def title_from_slug(url: str) -> str:
    """'.../software-engineering-internships' -> 'Software Engineering Internships';
    a trailing requisition number ('...-summer-2027-5894') is dropped."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"-\d{3,}$", "", slug)
    words = [w for w in slug.split("-") if w]
    return " ".join(w.upper() if len(w) <= 2 and w.isalpha() and w not in ("of", "in", "to", "at") else w.capitalize()
                     for w in words)


def _plain(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_jobposting(page: bytes) -> dict | None:
    """The schema.org JobPosting block a job page carries, or None."""
    m = re.search(rb'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(1))
    except ValueError:
        return None
    if isinstance(d, list):
        d = next((x for x in d if isinstance(x, dict) and x.get("@type") == "JobPosting"), None)
    if not isinstance(d, dict) or d.get("@type") != "JobPosting":
        return None
    locs = d.get("jobLocation") or []
    locs = locs if isinstance(locs, list) else [locs]
    names = [l.get("name") for l in locs if isinstance(l, dict) and l.get("name")]
    location = names[0] + (f" +{len(names) - 1} more" if len(names) > 1 else "") if names else ""
    return {"title": (d.get("title") or "").strip(), "location": location,
            "posted_at": d.get("datePosted"), "description": _plain(d.get("description") or "")[:MAX_LEN]}


def _fetch_jobposting(url: str) -> dict | None:
    """Read a job page only as far as its JobPosting block."""
    try:
        with requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as r:
            if r.status_code != 200:
                return None
            buf = b""
            for chunk in r.iter_content(16384):
                buf += chunk
                if b"</script>" in buf and b"ld+json" in buf:
                    got = parse_jobposting(buf)
                    if got:
                        return got
                if len(buf) > 800_000:
                    break
            return parse_jobposting(buf)
    except Exception as exc:
        log.warning("Career page unavailable (%s): %s", url.rsplit("/", 2)[-2], type(exc).__name__)
        return None


def _due(now: float) -> bool:
    last = get_state(STATE_KEY)
    if not last:
        return True
    try:
        return now - datetime.fromisoformat(last).timestamp() >= MIN_INTERVAL_S
    except ValueError:
        return True


def collect_listings(force: bool = False) -> list[dict]:
    """New listings from every career sitemap, in the ATS job shape. Returns []
    when the last pass was under MIN_INTERVAL_S ago (unless force)."""
    now = time.time()
    if not force and not _due(now):
        return []

    stubs: list[dict] = []
    for source in SOURCES:
        for url in _sitemap_urls(source):
            job = _make_job(source["company"], title_from_slug(url) if source["detail"] == "slug" else "?",
                            source["location"], url, None, None)
            if job:
                job["_source"] = source
                job["search_term"] = "career-sitemap"
                stubs.append(job)

    # Id-only question: a stub's norm_key is '|', which the SQL ignores, so
    # only the id decides. (Titles for Meta are not known yet.)
    unknown: set[str] = set()
    for i in range(0, len(stubs), UNKNOWN_BATCH):
        batch = stubs[i:i + UNKNOWN_BATCH]
        unknown |= find_unknown_candidates([{"id": j["id"], "norm_key": "|"} for j in batch],
                                           batch_size=UNKNOWN_BATCH)

    out: list[dict] = []
    opened = 0
    for job in stubs:
        if job["id"] not in unknown:
            continue
        source = job.pop("_source")
        if source["detail"] == "jsonld":
            if opened >= DETAIL_CAP:
                continue
            if opened:
                time.sleep(DETAIL_GAP_S)
            opened += 1
            got = _fetch_jobposting(job["url"])
            if not got or not got["title"]:
                continue
            job.update(title=got["title"], location=got["location"] or job["location"],
                       posted_at=got["posted_at"], description=got["description"] or None)
        out.append(job)
    for job in stubs:
        job.pop("_source", None)

    left = sum(1 for j in stubs if j["id"] in unknown) - len(out)
    log.info("Career sitemaps: %d listed, %d new, %d pages opened, %d returned, %d left for later",
             len(stubs), len(unknown), opened, len(out), max(left, 0))
    set_state(STATE_KEY, datetime.now(timezone.utc).isoformat())
    return out
