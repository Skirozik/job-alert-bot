"""jobright pass for the Brice pipeline: jobright-ai's new-grad lists, read from their GitHub READMEs.

jobright-ai publishes rolling 7-day new-grad job lists as Markdown tables in GitHub READMEs
(config.JOBRIGHT_LISTS). This module reads those README files from raw.githubusercontent.com and
nothing else. It never requests jobright.ai: the site's robots.txt disallows /jobs/ for AI crawlers,
so a row is judged on its README fields alone -- company, title, location, work model, date -- and
is classified title-only; the classifier's title-only cap keeps it off a loud APPLY. The
jobright.ai link is only stored, as the ping's Click target and the dashboard link, for a person to
open.

main.collect_jobright() calls, per list:

  fetch_readme(url, etag=None) -> (status, text, response_etag)
      The pass's only HTTP call: one GET, with If-None-Match when an ETag is given
      (304 -> (304, "", etag)). Refuses -- returns (0, "", None) without a request -- any URL that
      is not https on raw.githubusercontent.com, and does not follow redirects, so no request can
      leave that host. A failure is (0, "", None), logged by exception type only.

  parse_readme(text, list_name, today) -> (rows, unparsed_link_rows)
      Row = {id: "jr:<hexid>", title, company, location, work_model,
             posted: "YYYY-MM-DD" | None, url: "https://jobright.ai/jobs/info/<hexid>", list}

  rows_to_jobs(rows, list_name, *, support_only, today, samples=None) -> (jobs, dropped_by)
      Drops rows posted more than config.JOBRIGHT_MAX_AGE_DAYS ago, runs title_gate.source_gate
      (family filter + title gate; no program pass-through; on the Support list a support-type
      title must be a support-ENGINEER title, while solutions, network and IT-systems titles
      pass as on any list) and keeps one copy per company + title. dropped_by counts rules.

  canary_problems(status, parsed_rows, unparsed_link_rows) -> [problem, ...]
      [] when the list read cleanly; main alerts the owner per list, throttled 24 h.

main owns the ETag round trip (bot_state "jobright_etag:<name>", saved only when a list left no
candidates behind) and the 1-2 s pacing between lists. This module opens no database and calls no
Claude.
"""

import html
import logging
import re
from collections import Counter
from datetime import date
from typing import Iterator, Optional
from urllib.parse import urlparse

import requests

import families
import title_gate
from config import JOBRIGHT_MAX_AGE_DAYS
from db import make_norm_key   # a pure function: importing it creates no client

log = logging.getLogger(__name__)

RAW_HOST = "raw.githubusercontent.com"
USER_AGENT = "job-alert-bot (+https://github.com/Skirozik/job-alert-bot)"
TIMEOUT_S = 30
JOB_URL = "https://jobright.ai/jobs/info/"     # stored for people to open; never requested here
DRIFT_SHARE = 0.05                            # canary: more unparsed job-link rows than this share

STALE_RULE = f"posted more than {JOBRIGHT_MAX_AGE_DAYS} days ago"
DUPLICATE_RULE = "duplicate within the list"   # another copy of the same company + title (or id) was kept
SAMPLES_PER_RULE = 40                          # dropped rows kept per rule, for the dry-run report

# A row:  | **[Company](company_url)** | **[Job Title](https://jobright.ai/jobs/info/<hexid>?utm_...)** |
#         Location | Work Model | Date Posted |
# The company cell may be "↳" (same company as the row above) and its link text may contain "|"
# ("Savers | Value Village"); extra cells before the last two belong to the location ("Banfield
# Hollywood FL|005044, United States of America"). The id must end at "?", "#" or ")": an id with any
# other tail is left unparsed (and counted) rather than cut short.
ROW_RE = re.compile(
    r"^\|\s*(?P<company>\*\*\[(?P<cname>.+?)\]\((?P<curl>[^)]*)\)\*\*|↳|[^|]*?)\s*\|\s*"
    r"\*\*\[(?P<title>.+?)\]"
    r"\((?P<turl>https?://jobright\.ai/jobs/info/(?P<jid>[0-9a-f]{16,32})(?:[?#][^)\s]*)?)\)\*\*\s*"
    r"(?P<rest>\|.*)$")
DATE_RE = re.compile(r"^(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(?P<day>\d{1,2})$")
_MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                                       "Nov", "Dec"), 1)}
_LINK_MARK = "jobright.ai/jobs/info/"
_CONTINUED = "↳"
# Hundreds of titles end in the scraped page's breadcrumb: "Systems Engineer I Job Details / Aflac, Incorporated".
# It is not part of the title: left in, it splits one posting's company|title key from its LinkedIn copy and shows
# up in the ping. (About 630 rows on 2026-09-30.)
_JOB_DETAILS_RE = re.compile(r"\s+Job Details\s*/.*$", re.I)


# ── fetch ────────────────────────────────────────────────────────────────────

def _scheme_host(url: str) -> tuple[str, str]:
    try:
        parts = urlparse(url or "")
        return parts.scheme, parts.hostname or ""
    except ValueError:
        return "", ""


def _allowed(url: str) -> bool:
    """https on raw.githubusercontent.com itself: no other host, no user info, no other port."""
    try:
        parts = urlparse(url or "")
        return (parts.scheme == "https" and parts.hostname == RAW_HOST and parts.port in (None, 443)
                and parts.username is None and parts.password is None)
    except ValueError:
        return False


def fetch_readme(url: str, etag: Optional[str] = None) -> tuple[int, str, Optional[str]]:
    """GET one README, conditionally when an ETag is given. Returns (status, text, response_etag).

    200 -> (200, text, the response's ETag); 304 -> (304, "", etag); any other status ->
    (status, "", None); a refused URL or a failed request -> (0, "", None). Timeout 30 s.
    Redirects are not followed: a moved list surfaces as a 3xx status (a canary), never as a request
    to another host. Paced 1-2 s between lists by the caller.
    """
    if not _allowed(url):
        scheme, host = _scheme_host(url)
        log.error("jobright: refusing to request %s://%s — only https://%s is allowed",
                  scheme or "?", host or "?", RAW_HOST)
        return 0, "", None
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    try:
        resp = requests.get(url, headers=headers, timeout=TIMEOUT_S, allow_redirects=False)
    except Exception as exc:  # noqa: BLE001 -- one list failing must not stop the others
        log.error("jobright README fetch failed (%s)", type(exc).__name__)
        return 0, "", None
    status = resp.status_code
    if status == 304:
        return 304, "", etag
    if status != 200:
        log.warning("jobright README fetch: HTTP %s", status)
        return status, "", None
    # Decode explicitly: the tables are full of "↳" and a missing charset must not garble them.
    text = resp.content.decode("utf-8", errors="replace")
    return 200, text, resp.headers.get("ETag")


# ── parse ────────────────────────────────────────────────────────────────────

def infer_date(mon_day: str, today: date) -> Optional[date]:
    """Year-less 'Sep 29' -> the latest date with that month and day that is not more than 1 day
    after today (tolerates timezone skew). 'Dec 29' on 2027-01-03 -> 2026-12-29; 'Jan 1' on
    2026-12-31 -> 2027-01-01; 'Feb 30' -> None."""
    m = DATE_RE.match((mon_day or "").strip())
    if not m:
        return None
    month, day = _MONTHS[m["mon"]], int(m["day"])
    for year in (today.year + 1, today.year, today.year - 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if (d - today).days <= 1:
            return d
    return None


def _clean(text: str) -> str:
    """README cell text as a person reads it: entities decoded ('&amp;' -> '&'), whitespace collapsed."""
    return " ".join(html.unescape(text or "").split())


def _table_rows(text: str) -> Iterator[str]:
    """Table rows, one per logical row. A cell with a line break in it (the Sales list had a title split
    across two lines on 2026-09-30) continues on lines that do not start with '|'; they are rejoined."""
    row = None
    for line in (text or "").splitlines():
        if line.startswith("|"):
            if row is not None:
                yield row
            row = line
        elif (row is not None and not row.rstrip().endswith("|") and line.strip()
              and not line.lstrip().startswith(("<", "#"))):
            row = f"{row} {line.strip()}"
        else:
            if row is not None:
                yield row
            row = None
    if row is not None:
        yield row


def parse_readme(text: str, list_name: str, today: date) -> tuple[list[dict], int]:
    """Every job row of one README, in file order, and the number of rows that carry a jobright job link
    but could not be read (the format-drift canary)."""
    rows: list[dict] = []
    unparsed = 0
    last_company = ""
    for line in _table_rows(text):
        m = ROW_RE.match(line)
        if not m:
            if _LINK_MARK in line:
                unparsed += 1
            continue
        raw_company = m["company"].strip()
        if m["cname"] is not None:
            company = _clean(m["cname"])
        elif raw_company == _CONTINUED:
            company = last_company
        else:
            company = _clean(raw_company)
        if raw_company != _CONTINUED:
            last_company = company       # a blank company cell also ends the run of "↳" rows
        cells = [c.strip() for c in m["rest"].strip().strip("|").split("|")]
        title = _JOB_DETAILS_RE.sub("", _clean(m["title"]))
        if not company or not title or len(cells) < 3:
            unparsed += 1                # "↳" with no company above it, a blank cell, or cells missing
            continue
        posted = infer_date(cells[-1], today)
        rows.append({
            "id": "jr:" + m["jid"],
            "title": title,
            "company": company,
            "location": _clean(" | ".join(cells[:-2])),
            "work_model": _clean(cells[-2]),
            "posted": posted.isoformat() if posted else None,
            "url": JOB_URL + m["jid"],
            "list": list_name,
        })
    return rows, unparsed


# ── rows -> candidates ───────────────────────────────────────────────────────

def _posted(value: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _location(location: str, work_model: str) -> str:
    """The README location, plus ' (Remote)' / ' (Hybrid)' when the work model says so."""
    model = (work_model or "").strip().lower()
    for word in ("remote", "hybrid"):
        if model == word and word not in (location or "").lower():
            return f"{location} ({word.title()})" if location else word.title()
    return location or ""


def rows_to_jobs(rows: list[dict], list_name: str, *, support_only: bool, today: date,
                 samples: Optional[dict] = None) -> tuple[list[dict], Counter]:
    """Parsed rows -> classifier candidates, listing fields only.

    In file order: a row posted more than JOBRIGHT_MAX_AGE_DAYS ago is dropped (a row whose date could
    not be read is kept); then title_gate.source_gate(title, company, location, "",
    support_list=support_only, program_passthrough=False); then one copy per company + title (the
    database keeps one row per norm_key): the first copy with a confirmed U.S. location, else the first.

    Returns (jobs, dropped_by). dropped_by is a Counter of rules, len(rows) == len(jobs) +
    sum(dropped_by). samples, when given, collects up to SAMPLES_PER_RULE "company | title | location"
    lines per rule for the dry-run report.
    """
    dropped: Counter = Counter()

    def drop(rule: str, row: dict) -> None:
        dropped[rule] += 1
        if samples is not None:
            kept = samples.setdefault(rule, [])
            if len(kept) < SAMPLES_PER_RULE:
                kept.append(f"{row.get('company') or ''} | {row.get('title') or ''} | {row.get('location') or ''}")

    passed = []      # (row, label, norm_key, U.S.-confirmed) for each row the gate keeps, in file order
    for row in rows:
        title, company, location = row.get("title") or "", row.get("company") or "", row.get("location") or ""
        posted = _posted(row.get("posted"))
        if posted is not None and (today - posted).days > JOBRIGHT_MAX_AGE_DAYS:
            drop(STALE_RULE, row)
            continue
        keep, label = title_gate.source_gate(title, company, location, "", support_list=support_only,
                                             program_passthrough=False)
        if not keep:
            drop(label, row)
            continue
        passed.append((row, label, make_norm_key(company, title), families.is_us(location, "", title) is True))

    chosen: dict[str, int] = {}
    for index, (_row, _label, nk, us) in enumerate(passed):
        if nk not in chosen or (us and not passed[chosen[nk]][3]):
            chosen[nk] = index
    winners = set(chosen.values())

    jobs: list[dict] = []
    seen_ids: set[str] = set()
    for index, (row, label, nk, _us) in enumerate(passed):
        if index not in winners or row["id"] in seen_ids:
            drop(DUPLICATE_RULE, row)
            continue
        seen_ids.add(row["id"])
        posted = row.get("posted")
        jobs.append({
            "id": row["id"],
            "title": row["title"],
            "company": row["company"],
            "location": _location(row.get("location") or "", row.get("work_model") or ""),
            "url": row["url"],
            "apply_url": None,
            "posted_at": f"{posted}T00:00:00+00:00" if posted else None,
            "description": None,
            "is_easy_apply": False,
            "logo_url": None,
            "search_term": f"jobright:{list_name}",
            "source": "jobright",
            "family": label,
            "norm_key": nk,
        })
    return jobs, dropped


# ── canary ───────────────────────────────────────────────────────────────────

def canary_problems(status: int, parsed_rows: int, unparsed_link_rows: int) -> list[str]:
    """Why a list needs the owner's attention: [] when it read cleanly (a 304 is clean).

    HTTP status not 200/304 (0 = no response); a 200 with no parsed rows; more than 5 % of the rows
    that carry a jobright job link left unparsed (the README format drifted)."""
    problems = []
    if status not in (200, 304):
        problems.append(f"HTTP {status}" if status else "no HTTP response (request failed or refused)")
    elif status == 200 and parsed_rows == 0:
        problems.append("HTTP 200 with 0 parsed rows")
    link_rows = parsed_rows + unparsed_link_rows
    if link_rows and unparsed_link_rows / link_rows > DRIFT_SHARE:
        problems.append(f"format drift: {unparsed_link_rows} of {link_rows} job-link rows unparsed")
    return problems
