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

One exception, Apple's student postings: Apple's sitemap is not a dependable
list of them. On 2026-10-06 it carried the 2027 internship pools; on 2026-10-22
it carried none of the 25 U.S. internship postings on jobs.apple.com. So the
Students search page for the U.S. -- the page a person browses, server-rendered
with its results embedded -- is read as well (collect_apple_students), and a
posting's own page is opened once, the first time its id is seen, for the
qualifications the classifier needs (degree field, return-to-school rule).
That page is newest-first, so a normal pass reads only page 1 (the 20 newest);
every page is read once an hour. apple_watch.py pushes the new postings it
finds and alarms when the page stops being readable.

Runs inside ats_watch.py, at most once per MIN_INTERVAL_S (bot_state stamp), so
the sitemaps are fetched about hourly rather than on every 5-minute ATS pass;
the Apple students page on every pass (APPLE_STUDENTS_INTERVAL_S).
Rows reuse the ATS id scheme and job shape, so everything downstream -- dedup,
title pre-filter, classification, pings, the dashboard's "Direct" source -- is
the ATS path unchanged.
"""

import html
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from ats_sources import MAX_LEN, _make_job
from db import find_unknown_candidates, get_state, job_norm_key, set_state

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


def _sitemap_urls(source: dict) -> list[str] | None:
    """Job-page URLs from a source's sitemap, or None when the sitemap could not be fetched."""
    try:
        r = requests.get(source["sitemap"], headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as exc:
        log.warning("Career sitemap %s unavailable: %s", source["company"], type(exc).__name__)
        return None
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


# "Intern" as a word: "Internal Communications Strategist" is not an internship.
INTERN_TITLE = re.compile(r"\bintern(?:s|ship|ships)?\b", re.I)


def _sitemap_stub(source: dict, url: str) -> dict | None:
    job = _make_job(source["company"], title_from_slug(url) if source["detail"] == "slug" else "?",
                    source["location"], url, None, None)
    if job:
        job["search_term"] = "career-sitemap"
    return job


def apple_sitemap_internships() -> list[dict] | None:
    """Every internship-titled posting in Apple's sitemap, or None when it could not be fetched.
    apple_watch reads it once, the moment its ledger is created, so the sitemap's months-old postings
    are recorded at the same instant as the internships page's."""
    source = next(s for s in SOURCES if s["company"] == "Apple")
    urls = _sitemap_urls(source)
    if urls is None:
        return None
    return [j for j in (_sitemap_stub(source, u) for u in urls) if j and INTERN_TITLE.search(j["title"])]


def collect_listings(force: bool = False, apple_sitemap: dict | None = None) -> list[dict]:
    """New listings from every career sitemap, in the ATS job shape. Returns []
    when the last pass was under MIN_INTERVAL_S ago (unless force).

    apple_sitemap, when given, gets "internships": EVERY Apple internship-titled
    posting in the sitemap, stored or not, for apple_watch to diff against its
    own ledger -- set only when Apple's sitemap was actually read this call (an
    empty list then means it lists none). The new-listing answer cannot serve
    there: a posting is stored before its push is sent, and the second city of
    a two-city role is never stored at all, so neither would come back."""
    now = time.time()
    if not force and not _due(now):
        return []

    stubs: list[dict] = []
    apple_read = False
    for source in SOURCES:
        urls = _sitemap_urls(source)
        apple_read = apple_read or (source["company"] == "Apple" and urls is not None)
        for url in urls or []:
            job = _sitemap_stub(source, url)
            if job:
                job["_source"] = source
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
    if apple_sitemap is not None and apple_read:
        apple_sitemap["internships"] = [j for j in stubs if j["company"] == "Apple" and INTERN_TITLE.search(j["title"])]

    left = sum(1 for j in stubs if j["id"] in unknown) - len(out)
    log.info("Career sitemaps: %d listed, %d new, %d pages opened, %d returned, %d left for later",
             len(stubs), len(unknown), opened, len(out), max(left, 0))
    set_state(STATE_KEY, datetime.now(timezone.utc).isoformat())
    return out


# ── Apple student postings, from the Students search page ──────────────────────
APPLE_STUDENTS_URL = "https://jobs.apple.com/en-us/search?location=united-states-USA&team=internships-STDNT-INTRN"
# Every U.S. Apple job, the same page without the team filter. Apple posts dozens a day here, so
# apple_watch.shadow_check reads it to prove the reader still catches real new postings.
APPLE_ALL_US_URL = "https://jobs.apple.com/en-us/search?location=united-states-USA"
APPLE_STUDENTS_STATE_KEY = "apple_students_checked_at"
APPLE_STUDENTS_FULL_STATE_KEY = "apple_students_full_read_at"
# Every 5-minute ATS pass. The gate only stops a pass that starts under 4 minutes after the last one
# (a manual dispatch landing next to a scheduled run) from doubling the requests.
APPLE_STUDENTS_INTERVAL_S = 240
# The search is newest first, so page 1 (20 postings) holds anything new. Every page is still read
# once an hour, in case a posting lands further down (a repost keeping its old date).
APPLE_STUDENTS_FULL_READ_S = 3600
APPLE_STUDENTS_MAX_PAGES = 5      # 25 postings on 2026-10-30, 20 to a page
_APPLE_DATA = re.compile(r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("(.*?)"\);', re.S)


class ApplePageUnreadable(ValueError):
    """Apple answered 200 with a search page the reader cannot read: no embedded search data, or
    results none of which carry the fields read here. That is Apple changing its page, and it must
    never read as "no postings"."""


@dataclass
class AppleRead:
    """One read of an Apple search. outcome is "ok", "refused" (not a 200, or no answer) or
    "unreadable" (see ApplePageUnreadable); a read that fails part-way keeps what it got."""
    postings: list[dict] = field(default_factory=list)
    total: int = 0
    outcome: str = "ok"
    problem: str = ""

    @property
    def complete(self) -> bool:
        return self.outcome == "ok" and len(self.postings) >= self.total


@dataclass
class AppleStudents:
    """collect_apple_students' answer. rows: postings not stored yet, ready for the ATS loop. read:
    every posting read this pass, for apple_watch.alert. health: page 1's read alone, for
    apple_watch.record_students_read -- page 1 is what finds a new posting, so a refused page 2 on the
    hourly read is not an outage. Both None when the pass was inside the interval."""
    rows: list[dict] = field(default_factory=list)
    read: AppleRead | None = None
    health: AppleRead | None = None


def _apple_loader_data(page: str) -> dict:
    """The data an Apple jobs page embeds for its own rendering: a JSON document inside a JS string."""
    m = _APPLE_DATA.search(page or "")
    if not m:
        return {}
    try:
        data = json.loads(json.loads('"' + m.group(1) + '"'))
    except ValueError:
        return {}
    loader = data.get("loaderData") if isinstance(data, dict) else None
    return loader if isinstance(loader, dict) else {}


# A filtered internships search with more postings than this has lost its filter (25 on 2026-10-30;
# every U.S. job is ~4,500).
APPLE_STUDENTS_MAX_PLAUSIBLE = 300


def parse_apple_search(page: str, expect_team: str | None = None) -> tuple[list[dict], int]:
    """(postings, total) from one search page. A posting's URL is built from its id -- '200673612-0836', one
    per posting location -- in the same form the sitemap uses, so a role already stored from the sitemap
    keeps its id. The part before the dash is the requisition, shared by a role's locations ("req").
    A genuinely empty search is ([], 0); a page the reader cannot read raises ApplePageUnreadable.

    expect_team: the team code the search must echo back as applied ("INTRN"). Apple answers an unknown
    team slug with HTTP 200 and EVERY U.S. job, so without this check a retired slug would turn each
    new full-time role into an internship push."""
    search = _apple_loader_data(page).get("search")
    if not isinstance(search, dict) or not isinstance(search.get("searchResults"), list):
        raise ApplePageUnreadable("no search results embedded in the page")
    try:
        total = int(search.get("totalRecords") or 0)
    except (TypeError, ValueError):
        raise ApplePageUnreadable("the posting count is not a number")
    if expect_team:
        teams = (search.get("filters") or {}).get("teams") if isinstance(search.get("filters"), dict) else None
        if not any(isinstance(t, dict) and t.get("code") == expect_team for t in teams or []):
            raise ApplePageUnreadable(f"the {expect_team} team filter was not applied")
        if total > APPLE_STUDENTS_MAX_PLAUSIBLE:
            raise ApplePageUnreadable(f"{total} postings, too many for the {expect_team} filter")
    results = search["searchResults"]
    out = []
    for r in results:
        if not isinstance(r, dict):
            continue
        rid, slug, title = r.get("id"), r.get("transformedPostingTitle"), r.get("postingTitle")
        if not (rid and isinstance(slug, str) and slug and isinstance(title, str) and title.strip()):
            continue
        locations = r.get("locations") if isinstance(r.get("locations"), list) else []
        names = [l.get("name") if isinstance(l, dict) else l for l in locations]
        names = [n for n in names if isinstance(n, str) and n]
        posted = r.get("postDateInGMT")
        out.append({"url": f"https://jobs.apple.com/en-us/details/{rid}/{slug}", "title": title.strip(),
                    "location": ", ".join(names) or "United States",
                    "posted_at": posted if isinstance(posted, str) else None,
                    "apple_id": str(rid), "req": str(rid).split("-")[0]})
    if results and not out:
        raise ApplePageUnreadable(f"{len(results)} results, none with an id, title and link")
    return out, total


def parse_apple_detail(page: str) -> str | None:
    """A posting's summary, description and qualifications as plain text, or None."""
    j = ((_apple_loader_data(page).get("jobDetails") or {}).get("jobsData")) or {}
    parts = [_plain(j.get("jobSummary")), _plain(j.get("description"))]
    for label, key in (("Minimum qualifications", "minimumQualifications"),
                       ("Preferred qualifications", "preferredQualifications")):
        if _plain(j.get(key)):
            parts.append(f"{label}: {_plain(j.get(key))}")
    text = " ".join(p for p in parts if p)
    return text[:MAX_LEN] if text else None


def _apple_get(url: str) -> tuple[int | None, str]:
    """(HTTP status, body). Status None when no answer came back. The status is logged when it is not
    200: on 2026-10-29 two of six passes read nothing from GitHub's runners and nothing said why."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except Exception as exc:
        log.warning("Apple page unavailable: %s", type(exc).__name__)
        return None, ""
    if r.status_code != 200:
        log.warning("Apple page refused: HTTP %s, %d bytes", r.status_code, len(r.text or ""))
    return r.status_code, r.text or ""


def read_apple_pages(url: str, first: int = 1, last: int = 1, total: int | None = None,
                     expect_team: str | None = None) -> AppleRead:
    """Pages first..last of an Apple search, stopping early once `total` postings are in hand (or the
    search's own total, once page 1 has said it). A refused or unreadable page ends the read there,
    with that outcome; it is retried at the next pass, never around. Never raises for what Apple
    sends: any surprise in the page is "unreadable", so the alarms that depend on this still run."""
    read = AppleRead(total=total or 0)
    for page_no in range(first, last + 1):
        if page_no > first:
            time.sleep(DETAIL_GAP_S)
        status, page = _apple_get(url + (f"&page={page_no}" if page_no > 1 else ""))
        if status != 200:
            read.outcome, read.problem = "refused", f"HTTP {status}" if status else "no answer"
            break
        try:
            got, page_total = parse_apple_search(page, expect_team=expect_team)
        except Exception as exc:
            # The type name only, for anything unexpected: the Actions logs are public.
            read.outcome = "unreadable"
            read.problem = str(exc) if isinstance(exc, ApplePageUnreadable) else f"parser error ({type(exc).__name__})"
            log.warning("Apple page unreadable (page %d, %d bytes): %s", page_no, len(page), read.problem)
            break
        read.postings += got
        read.total = page_total or read.total
        if not got or len(read.postings) + (first - 1) * 20 >= read.total:
            break
    return read


def _newest_first(postings: list[dict]) -> bool:
    dates = [str(p.get("posted_at") or "") for p in postings]
    return all(a >= b for a, b in zip(dates, dates[1:]))


def _since(key: str, now: float) -> float | None:
    """Seconds since the bot_state stamp at key, or None when there is none (or it is unreadable)."""
    last = get_state(key)
    try:
        return now - datetime.fromisoformat(last).timestamp() if last else None
    except ValueError:
        return None


def _apple_stubs(postings: list[dict]) -> list[dict]:
    stubs = []
    for p in postings:
        job = _make_job("Apple", p["title"], p["location"], p["url"], None, p["posted_at"])
        if job:
            job["search_term"] = "apple-students"
            job["norm_key"] = job_norm_key(job)
            stubs.append(job)
    return stubs


def collect_apple_students(force: bool = False, full: bool = False) -> AppleStudents:
    """Apple's U.S. student postings: the ones not stored yet, in the ATS job shape with descriptions
    (rows), and everything read this pass (read). Reads page 1 only, unless a full read is asked for
    (full -- apple_watch asks before its first run), is due (hourly), or page 1 says there may be more
    to find: it is not newest-first, or every posting on it is new. Nothing is read when the last pass
    was under APPLE_STUDENTS_INTERVAL_S ago (unless force)."""
    now = time.time()
    since = _since(APPLE_STUDENTS_STATE_KEY, now)
    if not force and since is not None and since < APPLE_STUDENTS_INTERVAL_S:
        return AppleStudents()
    seeding = full
    since_full = _since(APPLE_STUDENTS_FULL_STATE_KEY, now)
    full = full or since_full is None or since_full >= APPLE_STUDENTS_FULL_READ_S

    read = read_apple_pages(APPLE_STUDENTS_URL, 1, 1, expect_team="INTRN")
    health = AppleRead(list(read.postings), read.total, read.outcome, read.problem)
    stubs = _apple_stubs(read.postings)
    unknown = find_unknown_candidates(stubs) if stubs else set()
    burst = bool(stubs) and len(unknown) == len(stubs)
    why = ("a full read is due" if full else "page 1 is not newest-first" if not _newest_first(read.postings)
           else "every posting on page 1 is new" if burst else "")
    if why and read.outcome == "ok" and len(read.postings) < read.total:
        time.sleep(DETAIL_GAP_S)
        rest = read_apple_pages(APPLE_STUDENTS_URL, 2, APPLE_STUDENTS_MAX_PAGES, total=read.total,
                                expect_team="INTRN")
        log.info("Apple students: read every page (%s)%s", why,
                 "" if rest.outcome == "ok" else f"; a later page failed ({rest.outcome}: {rest.problem})")
        rest_stubs = _apple_stubs(rest.postings)
        read.postings += rest.postings
        read.outcome, read.problem = rest.outcome, rest.problem
        if (seeding or burst) and rest.outcome != "ok":
            # Here the later pages matter: the ledger cannot be seeded without them, or a burst runs onto
            # them. So their failure is an outage, and counts toward the alarms.
            health.outcome, health.problem = rest.outcome, f"page 2 or later: {rest.problem}"
        stubs += rest_stubs
        unknown |= find_unknown_candidates(rest_stubs) if rest_stubs else set()
        # Stamped when ATTEMPTED, not only when complete: a page 2 that Apple refuses is tried again at
        # the next hourly slot, not on every pass. (The first run's seeding read, asked for with full,
        # keeps retrying each pass until it completes; apple_watch needs that read.)
        set_state(APPLE_STUDENTS_FULL_STATE_KEY, datetime.now(timezone.utc).isoformat())
    elif read.complete:
        set_state(APPLE_STUDENTS_FULL_STATE_KEY, datetime.now(timezone.utc).isoformat())

    out = []
    for job in stubs:
        if job["id"] not in unknown:
            continue
        if out:
            time.sleep(DETAIL_GAP_S)
        status, detail = _apple_get(job["url"])
        try:
            job["description"] = parse_apple_detail(detail) if status == 200 else None
        except Exception as exc:
            # Classified on its title instead; the posting must not be held back by its own page.
            log.warning("Apple posting page unreadable (%s): %s", job["id"], type(exc).__name__)
            job["description"] = None
        out.append(job)
    log.info("Apple students: %d listed (%d in all, %s), %d new, %d returned", len(read.postings), read.total,
             read.outcome if not read.problem else f"{read.outcome}: {read.problem}", len(unknown), len(out))
    set_state(APPLE_STUDENTS_STATE_KEY, datetime.now(timezone.utc).isoformat())
    return AppleStudents(rows=out, read=read, health=health)
