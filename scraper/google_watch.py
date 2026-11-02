"""Google: a 🔎 push the moment a new U.S. internship appears on Google's careers site, and the checks
that prove the Google reader works on the days Google posts nothing new.

WHY THIS EXISTS: the bot used to hear of Google internships only through LinkedIn and the GitHub
trackers. LinkedIn trailed Google by about an hour at best (Student Researcher, Fall 2026: created
15:38 UTC on 2026-07-20, found 16:40) and by six weeks at worst (Student Researcher, BS/MS,
Winter-Summer 2027: created Sep 14, found Oct 28); the trackers by about a day. And Google closes its
BS/MS internships fast: two posted on 2026-10-06 were off its list within four weeks.

The reader uses the results page a person browses at google.com/about/careers. It embeds every role
it shows as data, with Google's job id and the time the role was published, to the second. Google's
robots.txt allows that page for every crawler and disallows its later pages (page=), so only the
first page is ever read: 20 roles, newest first. The U.S. internship list was 18 roles on 2026-11-02,
so that page is all of it; past 20, a new role still arrives at the top.

  - check(): every 5-minute ATS pass, the U.S. internships list. Roles not in the tracker go through
    the usual ATS path (classified on the description the page carries, stored, the usual push for
    an APPLY), then alert() runs. The ATS title pre-filter takes Google's word that a role on this
    list is an internship: "Student Researcher, BS/MS" has no intern marker in its title.
  - alert(): every role read is recorded in a ledger (bot_state LEDGER_KEY), keyed on Google's job
    id. One the ledger has not seen gets a 🔎 push, whatever the classifier said, with its verdict in
    the body -- unless its title is for PhD, Masters or MBA students only, or is not an internship
    (the list carries CapitalG's "Open ... Career Opportunities"); such a role is pushed later if
    Google retitles it for undergraduates. The first run records the whole list and pushes nothing.
    The ledger keeps the newest LEDGER_MAX ids, so what it costs to read on every pass stays bounded.
  - record_read(): an alarm when the list loads but cannot be read (or lists nothing) twice running,
    or has failed for an hour. Google answers a filter it does not know with every U.S. job (1,728 on
    2026-11-02) and a 200, so a list too long to be internships is a failure, never a burst.
  - shadow_check(): hourly, the same reader on every U.S. Google job, where Google published 80 roles
    on the morning of Monday 2026-11-02. It pushes nothing and stores nothing; it logs how soon after
    Google's publish time each new one was seen, and alarms after SHADOW_SILENT_WEEKDAY_H weekday
    hours of nothing new -- weekday hours, because Google published nothing on the weekend of
    2026-10-31. It also alarms when the weekly drill has not passed in DRILL_STALE_S.
  - drill(): weekly (google_drill.py, in the same workflow as Apple's), from GitHub's runners against
    the live site. It first checks that the 5-minute Google check is running, then treats one real
    internship as new and takes it through the real reader, the classifier and both pushes, marked
    DRILL, with nothing written to the tracker; ntfy's copy is then read back.

The push body's dates and verdict, and the drill's read-back of ntfy, are apple_watch's own.
Every alarm is marked sent only once ntfy has taken it, and a state read that fails leaves the
stored record alone rather than starting it again.
"""

import json
import logging
import os
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

import notifier
from apple_watch import _APPLIED_OR_LATER, _ET, _day, _delivered, _posted, _verdict, _when
from ats_sources import MAX_LEN, _make_job
from career_sitemaps import HEADERS, _plain
from classifier import classify
from db import (QuotaExceeded, StateUnavailable, clear_state, find_unknown_candidates, get_client, get_state,
                job_norm_key, read_state, set_state)

log = logging.getLogger(__name__)

_RESULTS = "https://www.google.com/about/careers/applications/jobs/results/"
INTERNS_URL = _RESULTS + "?location=United%20States&target_level=INTERN_AND_APPRENTICE&sort_by=date"
# Every U.S. Google job, the same page without the level filter: the shadow watch's page.
ALL_US_URL = _RESULTS + "?location=United%20States&sort_by=date"
# A role's own page by id alone, which Google serves without the title slug. The tracker's id is made
# from this URL, so a title Google edits later is still the same row.
JOB_URL = _RESULTS + "{}"

STATE_KEY = "google_interns_checked_at"
LEDGER_KEY = "google_alert_ids"
HEALTH_KEY = "google_interns_health"
SHADOW_KEY = "google_shadow_watch"
SHADOW_CHECKED_KEY = "google_shadow_checked_at"   # small on purpose: it is read on every 5-minute pass
DRILL_KEY = "google_drill_passed"

# Every 5-minute ATS pass. The gate only stops a pass that starts under 4 minutes after the last one
# (a manual dispatch landing next to a scheduled run) from doubling the requests.
INTERVAL_S = 240
# Half the shared 30 s: the read sits in front of the 104-board sweep on every pass, and answers in ~0.3 s.
GOOGLE_TIMEOUT_S = 15
# Ids kept in the ledger. Oldest go first, never one still on the list; a few hundred a year are posted.
LEDGER_MAX = 500
# A filtered internships list with more roles than this has lost its filter (18 on 2026-11-02; every
# U.S. job is ~1,700).
MAX_PLAUSIBLE = 300
UNREADABLE_ALERT_AFTER = 2            # consecutive unreadable (or empty) reads, ~5-10 minutes apart
REFUSED_ALERT_AFTER_S = 3600
SHADOW_INTERVAL_S = 3600
SHADOW_SILENT_WEEKDAY_H = 72          # three working days: Thanksgiving's Thursday and Friday stay under it
SHADOW_SEEN_MAX = 300
SHADOW_LOG_DAYS = 7
DRILL_STALE_S = 8 * 86400             # a week, plus a day of slack for GitHub's late or dropped crons
GROUPED_PUSH_OVER = 3
DRILL_MAX_CHECK_AGE_S = 45 * 60       # the internships check runs every 5 minutes; 45 is a broken pinger
DRILL_MAX_SHADOW_AGE_S = 3 * 3600     # hourly; older means passes die before the shadow watch
# A scheduled run of the drill stands down when one passed this recently (the 9 AM pinger's run).
DRILL_SCHEDULED_SKIP_S = 20 * 3600
DRILL_TRIES = 3
DRILL_RETRY_GAP_S = 30                # one odd answer from Google must not fail the drill

# Pushed: anything titled as an internship, a student role or an apprenticeship, unless only PhD,
# Masters or MBA students can take it. Google puts the degree in the title ("..., BS/MS, Summer 2027").
_INTERNSHIP = re.compile(r"\b(?:intern(?:s|ship|ships)?|student|apprentice(?:s|ship|ships)?)\b", re.I)
_GRAD_DEGREE = re.compile(r"\b(?:ph\.?\s?d|doctoral|masters?|mba|ms)\b", re.I)
_BACHELORS = re.compile(r"\b(?:bs|ba|bachelors?|undergrad(?:uate)?)\b", re.I)


def wanted(title: str) -> bool:
    """Whether a new Google role is pushed: an internship-like title open to undergraduates."""
    title = title or ""
    return bool(_INTERNSHIP.search(title)) and not (_GRAD_DEGREE.search(title) and not _BACHELORS.search(title))


# ── the reader ────────────────────────────────────────────────────────────────

class GooglePageUnreadable(ValueError):
    """Google answered 200 with a results page the reader cannot read: no job list embedded, roles none
    of which carry an id and a title, or a list its filters were plainly not applied to. That is Google
    changing its page, and it must never read as "no roles"."""


@dataclass
class GoogleRead:
    """One read of a Google results page. outcome is "ok", "refused" (not a 200, or no answer) or
    "unreadable" (see GooglePageUnreadable)."""
    postings: list[dict] = field(default_factory=list)
    total: int = 0
    outcome: str = "ok"
    problem: str = ""


@dataclass
class GoogleInterns:
    """collect()'s answer. rows: roles not in the tracker, ready for the ATS loop. read: the list as
    read this pass, for alert() and record_read(); None when the pass was inside INTERVAL_S."""
    rows: list[dict] = field(default_factory=list)
    read: GoogleRead | None = None


# The page hands its data to its own scripts as AF_initDataCallback({key: 'ds:N', ..., data:[...], ...}).
_CALLBACK = re.compile(r"AF_initDataCallback\(\{key: '[^']*', hash: '[^']*', data:(.*?), sideChannel: \{\}\}\);", re.S)


def _job_list(page: str) -> list:
    """The embedded data that holds the job list: [roles or null, null, total, page size]. Found by its
    shape rather than its key, which Google numbers by position."""
    for m in _CALLBACK.finditer(page or ""):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        if (isinstance(data, list) and len(data) >= 3 and (data[0] is None or isinstance(data[0], list))
                and isinstance(data[2], int) and not isinstance(data[2], bool)):
            return data
    raise GooglePageUnreadable("no job list embedded in the page")


def _html(value) -> str:
    """A rich-text field, [null, "<html>"], as plain text."""
    return _plain(value[1]) if isinstance(value, list) and len(value) > 1 and isinstance(value[1], str) else ""


def _stamp(value) -> str | None:
    """[seconds, nanos] as an ISO timestamp in UTC."""
    if isinstance(value, list) and value and isinstance(value[0], int) and value[0] > 0:
        return datetime.fromtimestamp(value[0], timezone.utc).isoformat()
    return None


def _field(record: list, i: int):
    return record[i] if len(record) > i else None


def parse_results(page: str, interns: bool = True) -> tuple[list[dict], int]:
    """(postings, total) from one results page. A genuinely empty list is ([], 0); a page the reader
    cannot read raises GooglePageUnreadable. interns: the page is the internships list, which
    holds a few dozen roles at most -- far more means Google dropped the level filter. A role with
    no place in the U.S. is skipped; most of the page abroad means the location filter was dropped."""
    data = _job_list(page)
    records, total = data[0] or [], data[2]
    if interns and total > MAX_PLAUSIBLE:
        raise GooglePageUnreadable(f"{total} roles, too many for the internship filter")
    out, abroad = [], 0
    for r in records:
        if not isinstance(r, list) or len(r) < 2:
            continue
        gid, title = r[0], r[1]
        if not (isinstance(gid, str) and gid.isdigit() and isinstance(title, str) and title.strip()):
            continue
        places = _field(r, 9) if isinstance(_field(r, 9), list) else []
        names = [p[0] for p in places if isinstance(p, list) and p and isinstance(p[0], str) and p[0]]
        countries = {p[5] for p in places if isinstance(p, list) and len(p) > 5 and isinstance(p[5], str)}
        if countries and "US" not in countries:
            abroad += 1
            continue
        company = _field(r, 7)
        parts = [_html(_field(r, 10)), _html(_field(r, 3))]
        if _html(_field(r, 4)):
            parts.append("Qualifications: " + _html(_field(r, 4)))
        parts.append(_html(_field(r, 15)))
        description = " ".join(p for p in parts if p)
        out.append({"google_id": gid, "url": JOB_URL.format(gid), "title": title.strip(),
                    "company": company if isinstance(company, str) and company else "Google",
                    "location": names[0] + (f" +{len(names) - 1} more" if len(names) > 1 else "") if names
                    else "United States",
                    "places": names or ["United States"],
                    # Published, else last updated, else created: the publish time is when it went live.
                    "posted_at": _stamp(_field(r, 14)) or _stamp(_field(r, 13)) or _stamp(_field(r, 12)),
                    "description": description[:MAX_LEN] or None})
    if abroad * 2 > len(records):
        raise GooglePageUnreadable(f"{abroad} of {len(records)} roles outside the U.S.: the location filter was not applied")
    if abroad:
        log.info("Google list: %d role(s) with no U.S. location skipped", abroad)
    if records and not out and not abroad:
        raise GooglePageUnreadable(f"{len(records)} roles, none with an id and a title")
    return out, total


def _get(url: str) -> tuple[int | None, str]:
    """(HTTP status, body). Status None when no answer came back; logged when it is not 200."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=GOOGLE_TIMEOUT_S)
    except Exception as exc:
        log.warning("Google page unavailable: %s", type(exc).__name__)
        return None, ""
    if r.status_code != 200:
        log.warning("Google page refused: HTTP %s, %d bytes", r.status_code, len(r.text or ""))
    return r.status_code, r.text or ""


def read_list(url: str, interns: bool = True) -> GoogleRead:
    """The first page of a Google results list. Never raises for what Google sends: any surprise in the
    page is "unreadable", so the alarms that depend on this still run."""
    assert "page=" not in url, "Google's robots.txt disallows its later result pages"
    status, page = _get(url)
    if status != 200:
        return GoogleRead(outcome="refused", problem=f"HTTP {status}" if status else "no answer")
    try:
        postings, total = parse_results(page, interns=interns)
    except Exception as exc:
        # The type name only, for anything unexpected: the Actions logs are public.
        problem = str(exc) if isinstance(exc, GooglePageUnreadable) else f"parser error ({type(exc).__name__})"
        log.warning("Google page unreadable (%d bytes): %s", len(page), problem)
        return GoogleRead(outcome="unreadable", problem=problem)
    return GoogleRead(postings=postings, total=total)


def _stubs(postings: list[dict]) -> list[dict]:
    stubs = []
    for p in postings:
        job = _make_job(p["company"], p["title"], p["location"], p["url"], p["description"], p["posted_at"])
        if job:
            job["search_term"] = "google-careers"
            job["norm_key"] = job_norm_key(job)
            # On Google's internships list, so ats_watch's title pre-filter takes it at its word:
            # "Student Researcher, BS/MS" carries no intern marker but is one. CapitalG's standing
            # roles share the list and are not.
            job["internship_listing"] = bool(_INTERNSHIP.search(p["title"]))
            stubs.append(job)
    return stubs


def _since(key: str, now: float) -> float | None:
    """Seconds since the bot_state stamp at key, or None when there is none (or it is unreadable)."""
    when = _when(get_state(key))
    return now - when.timestamp() if when else None


def collect(force: bool = False) -> GoogleInterns:
    """Google's U.S. internships list: the roles not in the tracker, in the ATS job shape with their
    descriptions (rows), and the read itself. Nothing is read when the last pass was under INTERVAL_S
    ago (unless force)."""
    now = time.time()
    since = _since(STATE_KEY, now)
    if not force and since is not None and since < INTERVAL_S:
        return GoogleInterns()
    read = read_list(INTERNS_URL)
    stubs = _stubs(read.postings)
    unknown = find_unknown_candidates(stubs) if stubs else set()
    rows = [j for j in stubs if j["id"] in unknown]
    log.info("Google internships: %d listed (%d in all, %s), %d not in the tracker", len(read.postings),
             read.total, read.outcome if not read.problem else f"{read.outcome}: {read.problem}", len(rows))
    set_state(STATE_KEY, datetime.now(timezone.utc).isoformat())
    return GoogleInterns(rows=rows, read=read)


# ── the ledger ────────────────────────────────────────────────────────────────

def _strict_json(key: str) -> dict:
    """The JSON record at key: {} when absent or corrupt. Raises StateUnavailable when bot_state
    cannot be read, so a caller never takes a blip for a fresh start and overwrites the record."""
    try:
        value = json.loads(read_state(key) or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _ledger() -> dict | None:
    """{"ids": every job id seen, oldest first; "skipped": those recorded without a push because the
    title was graduate-only or not an internship}, or None before the first run. Raises
    StateUnavailable. A skipped role whose title Google later opens to undergraduates is pushed then."""
    raw = read_state(LEDGER_KEY)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return {"ids": [str(i) for i in data["ids"]], "skipped": {str(i) for i in data.get("skipped", [])}}
    except (ValueError, KeyError, TypeError, AttributeError):
        log.error("Google alert ledger is unreadable — starting it again from the current list")
        return None


def _write_ledger(ids: list[str], skipped: set[str], listed: set[str]) -> None:
    """ids oldest first, trimmed to LEDGER_MAX from the oldest end, never dropping one on the list now."""
    ids = list(dict.fromkeys(ids))
    over = len(ids) - LEDGER_MAX
    if over > 0:
        drop = set([i for i in ids if i not in listed][:over])
        ids = [i for i in ids if i not in drop]
    kept = set(ids)
    set_state(LEDGER_KEY, json.dumps({"ids": ids, "skipped": sorted(skipped & kept)}))


# ── the push ──────────────────────────────────────────────────────────────────

def _where(p: dict) -> str:
    places = p.get("places") or [p.get("location") or "United States"]
    return "; ".join(places[:3]) + (f" and {len(places) - 3} more" if len(places) > 3 else "")


def _stored_rows(p: dict, job_id: str) -> list[dict]:
    """Stored rows for this role, or for its exact title (the LinkedIn or tracker copy of it). [] on any
    error: the push then goes out without the lines these feed, rather than not at all."""
    key = job_norm_key({"company": p["company"], "title": p["title"]})
    try:
        return (get_client().table("jobs").select("id,status,tier,reason,found_at")
                .eq("company", p["company"]).or_(f'norm_key.eq."{key}",id.eq."{job_id}"').limit(50).execute().data or [])
    except Exception as exc:
        log.warning("Google push: tracker lookup failed (%s) — sending without it", type(exc).__name__)
        return []


def describe(p: dict, processed: dict, now: datetime, stored: list[dict] | None = None):
    """(title, body, url) for one role's push. processed: rows this pass ran through the classifier,
    by id -- their verdict is the freshest. A role the ATS loop dropped because its title is already
    stored (most often LinkedIn's copy of it) has none; that stored row's verdict is shown instead."""
    job_id = _stubs([p])[0]["id"]
    stored = _stored_rows(p, job_id) if stored is None else stored
    job = processed.get(job_id) if processed.get(job_id, {}).get("tier") else None
    own = [r for r in stored if r.get("id") == job_id]
    same_title = [r for r in stored if r.get("id") != job_id]
    lines = [_where(p)]
    if job:
        lines.append(_verdict(job["tier"], job.get("reason", "")))
    elif own:
        lines.append(_verdict(own[0].get("tier"), own[0].get("reason", "")) + f" (rated {_day(own[0].get('found_at'))})")
    elif same_title:
        first = min(same_title, key=lambda r: r.get("found_at") or "")
        lines.append(_verdict(first.get("tier"), first.get("reason", ""))
                     + f" (rated {_day(first.get('found_at'))}, from another listing of this title)")
    else:
        lines.append("Bot's verdict: not rated yet")
    lines.append(_posted([p], now))
    applied = [r for r in stored if r.get("status") in _APPLIED_OR_LATER]
    if applied:
        first = min((r.get("found_at") or "" for r in applied), default="")
        lines.append(f"You've applied to a role with this exact title (first seen {_day(first)}).")
    return p["title"], "\n".join(line for line in lines if line), p["url"]


def alert(read: GoogleRead, processed: list[dict] = (), now: datetime | None = None) -> int:
    """Push 🔎 for each role on the list the ledger has not seen, then record them. Returns the pushes
    sent. processed: the rows this pass classified (they carry the verdict). The first run records the
    list and pushes nothing. It is the whole list while Google lists 20 or fewer; past that, only the
    newest 20 can be read, and an older role Google edits later rises to the top and is pushed once.

    A push that fails is not recorded, so the next read sends it again: a duplicate costs one swipe,
    a role recorded but never pushed is the miss this module exists to prevent."""
    if read is None or read.outcome != "ok" or not read.postings:
        return 0
    now = now or datetime.now(timezone.utc)
    try:
        ledger = _ledger()
    except StateUnavailable as exc:
        log.error("Google alerts skipped this pass: ledger unreadable (%s)", exc)
        return 0
    listed = {p["google_id"] for p in read.postings}
    if ledger is None:
        _write_ledger([p["google_id"] for p in read.postings],
                      {p["google_id"] for p in read.postings if not wanted(p["title"])}, listed)
        log.info("Google alerts: first run, %d of %d roles recorded as already seen; nothing pushed",
                 len(read.postings), read.total)
        return 0

    seen, skipped = set(ledger["ids"]), ledger["skipped"]
    fresh = [p for p in read.postings
             if p["google_id"] not in seen or (p["google_id"] in skipped and wanted(p["title"]))]
    if not fresh:
        return 0
    to_push = [p for p in fresh if wanted(p["title"])]
    not_pushed = {p["google_id"] for p in fresh if not wanted(p["title"])}
    done = set(not_pushed)
    by_id = {j.get("id"): j for j in processed}
    sent = 0
    if len(to_push) > GROUPED_PUSH_OVER:
        lines = [f"• {p['title']} ({_where(p)})" for p in to_push]
        if len(fresh) == len(read.postings) and read.total > len(read.postings):
            # Only the first page may be read: the rest of a burst this size is on Google's own list.
            lines.append(f"Google lists {read.total} in all; the bot reads only the newest {len(read.postings)}.")
        if notifier.push_google(f"{len(to_push)} new internships", "\n".join(lines), INTERNS_URL):
            done |= {p["google_id"] for p in to_push}
            sent = 1
    else:
        for p in to_push:
            try:
                message = describe(p, by_id, now)
            except Exception as exc:
                # The push matters more than its details: never lose one to the body.
                log.error("Google push details unavailable (%s); sending it plain", type(exc).__name__)
                message = (p["title"], f"{_where(p)}\nDetails unavailable; open the posting.", p["url"])
            if notifier.push_google(*message):
                done.add(p["google_id"])
                sent += 1
    log.info("Google alerts: %d new roles, %d pushes sent, %d recorded without a push (graduate-only or not "
             "an internship)", len(fresh), sent, len(fresh) - len(to_push))
    if done:
        _write_ledger(ledger["ids"] + [p["google_id"] for p in fresh if p["google_id"] in done],
                      (skipped | not_pushed) - (done - not_pushed), listed)
    return sent


def check(sweep) -> tuple[int, int, int]:
    """One 5-minute pass: read the list, record its health, hand the roles not in the tracker to sweep
    (ats_watch's: dedup, classify, store, the usual push), then push the new ones. Returns (new,
    notified) from sweep and the number of rows handed to it. Three separate guards, as for Apple: a
    failure classifying a new role must not hold back its 🔎 push. A spent quota still stops the run."""
    got = None
    try:
        got = collect()
        record_read(got.read)
    except QuotaExceeded:
        raise
    except Exception as exc:
        log.error("Google internships list skipped this pass: %s", exc)
        # A reader that crashes is a reader that cannot read: it counts toward the alarm.
        try:
            record_read(GoogleRead(outcome="unreadable", problem=f"reader error ({type(exc).__name__})"))
        except QuotaExceeded:
            raise
        except Exception as exc2:
            log.error("Google list health not recorded: %s", exc2)
    new = notified = 0
    if got and got.rows:
        try:
            new, notified = sweep(got.rows)
        except QuotaExceeded:
            raise
        except Exception as exc:
            log.error("Google internships: processing the new roles failed (%s); pushing them anyway", exc)
    if got and got.read:
        try:
            alert(got.read, got.rows)
        except QuotaExceeded:
            raise
        except Exception as exc:
            log.error("Google alerts skipped this pass: %s", exc)
    return new, notified, len(got.rows) if got else 0


# ── health of the internships list ────────────────────────────────────────────

def record_read(read: GoogleRead | None, now: float | None = None) -> None:
    """Alarm once per outage: the list has been unreadable or empty twice running, or has failed for an
    hour. A recovery after an alarm is announced too. read: None when the pass was inside the interval.
    An empty list counts as a failure: CapitalG's three standing roles have been on it since 2023."""
    if read is None:
        return
    now = now or time.time()
    try:
        h = _strict_json(HEALTH_KEY)
    except StateUnavailable as exc:
        log.warning("Google list health not recorded this pass: bot_state unreadable (%s)", exc)
        return
    outcome, problem = read.outcome, read.problem
    if outcome == "ok" and not read.postings:
        outcome, problem = "empty", "the list has no roles"
    if outcome == "ok":
        if h.get("alerted") and not notifier.push_canary(
                "Google's internships list is readable again — new Google roles reach you as normal."):
            return      # keep the record, so the all-clear is tried again next pass
        if h:
            clear_state(HEALTH_KEY)
        return
    h.setdefault("failing_since", now)
    h["problem"] = f"{outcome}: {problem}"
    h["unreadable_streak"] = h.get("unreadable_streak", 0) + 1 if outcome in ("unreadable", "empty") else 0
    if not h.get("alerted"):
        message = None
        if outcome in ("unreadable", "empty") and h["unreadable_streak"] >= UNREADABLE_ALERT_AFTER:
            message = (f"Google's internships list loads, but the bot can't read any roles from it ({problem}). "
                       "Google has probably changed the page; new Google roles won't reach you until the reader is fixed.")
        elif now - h["failing_since"] >= REFUSED_ALERT_AFTER_S:
            message = (f"Google has turned away the bot's internship check for "
                       f"{int((now - h['failing_since']) // 60)} minutes ({h['problem']}). "
                       "New Google roles won't reach you until it stops.")
        if message and notifier.push_canary(message):
            h["alerted"] = True
    set_state(HEALTH_KEY, json.dumps(h))


def health_summary() -> str:
    try:
        h = _strict_json(HEALTH_KEY)
    except (StateUnavailable, QuotaExceeded):
        return "Internships list: status unknown (the bot's state could not be read)."
    if not h:
        return "Internships list: reading normally."
    since = datetime.fromtimestamp(h.get("failing_since", time.time()), timezone.utc)
    return f"Internships list: failing since {_day(since.isoformat())} {since.astimezone(_ET):%H:%M} ET ({h.get('problem')})."


# ── shadow watch: the same reader, on the list where Google publishes every weekday ──

def weekday_hours(start: float, end: float) -> float:
    """Hours between two times that fall on a Monday-to-Friday in Eastern time."""
    total, t = 0.0, start
    while t < end:
        day = datetime.fromtimestamp(t, _ET)
        midnight = (day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
        stop = min(end, midnight)
        if day.weekday() < 5:
            total += stop - t
        t = stop
    return total / 3600


def _minutes_after(posting: dict, now: float) -> int | None:
    when = _when(posting.get("posted_at"))
    return round((now - when.timestamp()) / 60) if when else None


def shadow_check(now: float | None = None) -> None:
    """Hourly: the newest 20 U.S. Google jobs, compared with the ids seen before. Then the same hour's
    check that the weekly drill is still passing."""
    now = now or time.time()
    last = _when(get_state(SHADOW_CHECKED_KEY))
    if last and now - last.timestamp() < SHADOW_INTERVAL_S:
        return
    try:
        s = _strict_json(SHADOW_KEY)
    except StateUnavailable as exc:
        log.warning("Google shadow watch put off to the next pass: bot_state unreadable (%s)", exc)
        return
    set_state(SHADOW_CHECKED_KEY, datetime.fromtimestamp(now, timezone.utc).isoformat())

    read = read_list(ALL_US_URL, interns=False)
    if read.outcome == "ok" and read.postings:
        seen = s.get("seen") or []
        ids = [p["google_id"] for p in read.postings]
        if not seen:
            new = []
            s["last_new_at"] = now
            log.info("Google shadow watch: first run, %d roles recorded", len(ids))
        else:
            known = set(seen)
            new = [p for p in read.postings if p["google_id"] not in known]
            s.setdefault("log", []).extend([round(now), _minutes_after(p, now)] for p in new)
            if new:
                if s.get("alerted") and not notifier.push_canary(
                        "Google's job list is showing new roles again — the Google reader works."):
                    pass    # all-clear not delivered: stay alerted, so it is sent with the next new role
                else:
                    s.pop("alerted", None)
                s["last_new_at"] = now
        s["seen"] = (ids + [i for i in seen if i not in ids])[:SHADOW_SEEN_MAX]
        delays = sorted(d for d in (_minutes_after(p, now) for p in new) if d is not None)
        log.info("Google shadow watch: %d listed, %d new%s", len(ids), len(new),
                 f", seen {delays[0]}-{delays[-1]} min after Google published them" if delays else "")
    else:
        log.warning("Google shadow watch read nothing this hour (%s: %s)", read.outcome, read.problem or "empty list")

    s["log"] = [e for e in s.get("log", []) if now - e[0] < SHADOW_LOG_DAYS * 86400][-2000:]
    s.setdefault("last_new_at", now)
    if weekday_hours(s["last_new_at"], now) >= SHADOW_SILENT_WEEKDAY_H and not s.get("alerted"):
        if notifier.push_canary(f"For {SHADOW_SILENT_WEEKDAY_H} weekday hours the bot has seen no new U.S. Google jobs. "
                                "Google publishes dozens every weekday, so its Google reader has probably stopped "
                                "working — new Google internships may not reach you."):
            s["alerted"] = True
    set_state(SHADOW_KEY, json.dumps(s))
    _check_drill_ran(now)


def _check_drill_ran(now: float) -> None:
    """An alarm when the weekly drill has not passed in DRILL_STALE_S. The clock starts at the first
    check, so a fresh install gets its first week."""
    try:
        d = _strict_json(DRILL_KEY)
    except StateUnavailable:
        return
    passed = _when(d.get("passed_at"))
    if passed is None:
        set_state(DRILL_KEY, json.dumps({"passed_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                                         "clock_started": True}))
        return
    if now - passed.timestamp() >= DRILL_STALE_S and not d.get("alerted"):
        what = (f"has never passed (watching since {_day(passed.isoformat())})" if d.get("clock_started")
                else f"has not passed since {_day(passed.isoformat())}")
        if notifier.push_canary(f"The weekly Google drill {what}. Run it from GitHub: Actions → Apple and Google "
                                "Alert Drill → Run workflow, and check why it stopped."):
            set_state(DRILL_KEY, json.dumps(dict(d, alerted=True)))


def shadow_summary(now: float | None = None) -> str:
    now = now or time.time()
    try:
        s = _strict_json(SHADOW_KEY)
        checked = _when(read_state(SHADOW_CHECKED_KEY))
    except (StateUnavailable, QuotaExceeded):
        return "Shadow watch: status unknown (the bot's state could not be read)."
    if "last_new_at" not in s:
        return "Shadow watch: no record yet."
    if not checked or now - checked.timestamp() > 3 * SHADOW_INTERVAL_S:
        return "Shadow watch: has not run in over 3 hours."
    week = [e for e in s.get("log", []) if now - e[0] < SHADOW_LOG_DAYS * 86400]
    if not week:
        return "Shadow watch: no new U.S. Google jobs seen in 7 days — the reader may be broken."
    delays = [d for _, d in week if d is not None and d >= 0]
    typical = f", typically {round(statistics.median(delays))} min after Google published them" if delays else ""
    return f"Shadow watch: {len(week)} new U.S. Google jobs seen in the last 7 days{typical}."


# ── the weekly drill ──────────────────────────────────────────────────────────

def _production_problem(now: float) -> str | None:
    """Why the 5-minute Google check is not working as it should, or None. Raises StateUnavailable.
    The drill's own reads retry; production's do not, so a production outage must fail the drill
    rather than hide behind a drill that got through."""
    last = _when(read_state(STATE_KEY))
    if last is None:
        return "it has never run"
    age = now - last.timestamp()
    if age > DRILL_MAX_CHECK_AGE_S:
        return f"it last ran {int(age // 60)} minutes ago"
    if read_state(LEDGER_KEY) is None:
        return "it has not recorded Google's internships yet"
    shadow = _when(read_state(SHADOW_CHECKED_KEY))
    if shadow is None or now - shadow.timestamp() > DRILL_MAX_SHADOW_AGE_S:
        return "its passes stop before the shadow-watch check"
    h = _strict_json(HEALTH_KEY)
    if h.get("alerted") or (h.get("failing_since") and now - h["failing_since"] >= REFUSED_ALERT_AFTER_S):
        since = datetime.fromtimestamp(h.get("failing_since", now), timezone.utc)
        return (f"its reads of the internships list have failed since {_day(since.isoformat())} "
                f"{since.astimezone(_ET):%H:%M} ET ({h.get('problem')})")
    return None


def _retry(attempt, ok):
    """attempt() up to DRILL_TRIES times, DRILL_RETRY_GAP_S apart, until ok(result); the last result."""
    for n in range(DRILL_TRIES):
        if n:
            time.sleep(DRILL_RETRY_GAP_S)
        result = attempt()
        if ok(result):
            break
    return result


def drill(now: datetime | None = None) -> int:
    """Check the 5-minute Google check is running, then treat one real role as new and send what a new
    one would send, marked DRILL. Reads the live list and the tracker, and calls the classifier;
    writes nothing to the tracker, only DRILL_KEY's pass stamp. Returns 0 when every step worked;
    otherwise 1, with a canary naming the step that failed."""
    now = now or datetime.now(timezone.utc)

    def fail(step: str, detail: str) -> int:
        log.error("Google drill FAILED at %s: %s", step, detail)
        if notifier.push_canary(f"Weekly Google drill FAILED at {step} ({detail}). "
                                "A new Google internship may not reach you until this is fixed."):
            # The workflow's own alarm step is for a run that dies without reaching here; not twice.
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a") as out:
                    out.write("alerted=true\n")
        return 1

    if not notifier.NTFY_TOPIC:
        log.error("Google drill: NTFY_TOPIC is not set")
        return 1
    problem, last_error = None, None
    for n in range(DRILL_TRIES):
        if n:
            time.sleep(DRILL_RETRY_GAP_S)
        try:
            if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
                d = _strict_json(DRILL_KEY)
                passed = _when(d.get("passed_at"))
                if passed and not d.get("clock_started") and now.timestamp() - passed.timestamp() < DRILL_SCHEDULED_SKIP_S:
                    log.info("Google drill: passed at %s already (the 9 AM pinger); this scheduled run stands down",
                             passed.isoformat())
                    return 0
            problem, last_error = _production_problem(now.timestamp()), None
            break
        except StateUnavailable as exc:
            last_error = exc        # one bot_state blip must not fail the drill
        except QuotaExceeded as exc:
            return fail("reading the bot's state", type(exc).__name__)
    if last_error:
        return fail("reading the bot's state", type(last_error).__name__)
    if problem:
        return fail("checking the 5-minute Google check", problem)

    read = _retry(lambda: read_list(INTERNS_URL), lambda r: r.outcome == "ok" and r.postings)
    if read.outcome != "ok" or not read.postings:
        return fail("reading Google's internships list", read.problem or "no roles on it")
    # An undergraduate internship when the list has one; otherwise any role on it still proves the path.
    pick = next((p for p in read.postings if wanted(p["title"])), read.postings[0])
    job = _stubs([pick])[0]
    if not job.get("description"):
        return fail("reading the role", "no description on the list")
    result = classify(job)
    if result.get("failed"):
        job.update(tier="PENDING", reason="")
        log.warning("Google drill: the classifier failed (%s); the push says so", result.get("failed_kind"))
    else:
        job.update(tier=result.get("tier", "APPLY_CAVEAT"), reason=result.get("reason", ""),
                   suggested_resume=result.get("suggested_resume", "General"))

    try:
        title, body, url = describe(pick, {job["id"]: job}, now)
    except Exception as exc:
        log.error("Google drill: push details unavailable (%s); sending it plain", type(exc).__name__)
        title, body, url = pick["title"], f"{_where(pick)}\nDetails unavailable; open the posting.", pick["url"]
    lines = ["Weekly drill, not a new posting: if this reached your phone, the Google alert works."]
    if not wanted(pick["title"]):
        lines.append("No undergraduate internship is on Google's list today, so the drill used this role.")
    body = "\n".join(lines + [body, shadow_summary(), health_summary()])
    sent_at = time.time()
    msg_id = notifier.push_google(title, body, url, drill=True)
    if not msg_id or msg_id == "sent":
        return fail("sending the 🔎 push", "ntfy did not take it" if not msg_id else "ntfy gave no message id")
    if job["tier"] in ("APPLY", "APPLY_CAVEAT") and not notifier.push_job(job, title_prefix="DRILL - "):
        return fail("sending the usual push", "ntfy did not take it")
    if not _delivered(msg_id, sent_at):
        return fail("confirming delivery", "ntfy took the push but its feed does not have it")
    # Verified, because set_state swallows errors: a pass that is not recorded would set off the
    # "drill has not passed" alarm a week later.
    stamp = json.dumps({"passed_at": now.isoformat()})
    for n in range(DRILL_TRIES):
        if n:
            time.sleep(DRILL_RETRY_GAP_S)
        set_state(DRILL_KEY, stamp)
        try:
            if read_state(DRILL_KEY) == stamp:
                break
        except StateUnavailable:
            pass
    else:
        return fail("recording the pass", "bot_state would not take the write")
    log.info("Google drill passed: %s, verdict %s", pick["title"], job["tier"])
    return 0
