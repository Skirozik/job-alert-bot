"""Apple: a 🍎 push the moment a new internship posting appears, and the checks
that prove the Apple reader works on the days Apple posts nothing new.

WHY THIS EXISTS: Apple posts its U.S. internships a handful of times a year (two
new ones between May and October 2026: MBA on Aug 5, Applied Data Solutions on
Aug 27), never to LinkedIn, only on its own careers site. A miss is expensive and
a working reader looks exactly like a broken one on a quiet day, so this module
both pushes and keeps proving that it can:

  - alert(): every Apple requisition read is recorded in a ledger (bot_state
    LEDGER_KEY). One the ledger has not seen gets a 🍎 push, whatever the
    classifier said, with its verdict in the body -- unless the title is a PhD,
    Masters or MBA pool. Keyed on the requisition, not the title: Apple reuses
    titles every year, and the jobs table's title key (norm_key) drops next
    year's "Software Undergrad Engineering Internships" as a duplicate before
    anything reads it. One requisition posted in two cities (Applied Data
    Solutions: Austin and Cupertino) is one push. The bot's usual push for an
    APPLY row still goes out too; this does not replace it. The first run seeds
    the ledger from the internships page and, at the same moment, the sitemap's
    internship titles (it lists months-old ones the page does not).
  - record_students_read(): an alarm when the internships page loads but cannot
    be read (or lists nothing) twice running, or has failed for an hour. On
    2026-10-29 two of six reads came back with nothing and nothing said so.
  - shadow_check(): hourly, the same reader on every U.S. Apple job -- the same
    page without the team filter, where Apple posted 15-43 roles a weekday and
    never fewer than 2 in any 48 hours of October 2026. It pushes nothing and
    stores nothing in the tracker; it logs how soon after Apple's posting time
    each new one was seen, and alarms after 48 hours of nothing new. It also
    alarms when the weekly drill has not passed in DRILL_STALE_S.
  - drill(): weekly (apple_drill.py), from GitHub's runners against the live
    site. It first checks that the 5-minute Apple check is running, then treats
    one real internship as new and takes it through the real reader,
    description fetch, classifier and both pushes, marked DRILL, with nothing
    written to the tracker; ntfy's copy is then read back.

Every alarm is marked sent only once ntfy has taken it, and a state read that
fails leaves the stored record alone rather than starting it again.
"""

import json
import logging
import os
import re
import statistics
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

import career_sitemaps as cs
import notifier
from ats_sources import _make_job
from classifier import classify
from db import (QuotaExceeded, StateUnavailable, clear_state, get_client, get_state, job_norm_key, read_state,
                set_state)

log = logging.getLogger(__name__)

LEDGER_KEY = "apple_alert_reqs"
HEALTH_KEY = "apple_students_health"
SHADOW_KEY = "apple_shadow_watch"
SHADOW_CHECKED_KEY = "apple_shadow_checked_at"   # small on purpose: it is read on every 5-minute pass
DRILL_KEY = "apple_drill_passed"

UNREADABLE_ALERT_AFTER = 2            # consecutive unreadable (or empty) reads, ~5-10 minutes apart
REFUSED_ALERT_AFTER_S = 3600
SHADOW_INTERVAL_S = 3600
SHADOW_SILENT_ALERT_S = 48 * 3600
SHADOW_SEEN_MAX = 300
SHADOW_LOG_DAYS = 7
DRILL_STALE_S = 8 * 86400             # a week, plus a day of slack for GitHub's late or dropped crons
# More new requisitions than this in one pass is one push listing them all. Apple's May pool launch is
# that size, and so is the failure that matters more: Apple changing its ids, so every posting looks new.
GROUPED_PUSH_OVER = 3
DRILL_TITLE = "Software Undergrad Engineering Internships"
DRILL_TRIES = 3
DRILL_RETRY_GAP_S = 30                # one odd answer from Apple must not fail the drill
DRILL_MAX_CHECK_AGE_S = 45 * 60       # the internships check runs every 5 minutes; 45 is a broken pinger
DRILL_MAX_SHADOW_AGE_S = 3 * 3600     # hourly; older means passes die before the end (sitemap, shadow watch)
# The external pinger runs the drill at 9 AM; GitHub's own schedule, the fallback, lands hours later
# (this repo's morning crons arrive ~4.5 h late). A scheduled run after a pass this recent stands down.
DRILL_SCHEDULED_SKIP_S = 20 * 3600
# ntfy.sh writes its message cache in batches (about a second), so a read straight after publishing
# can miss a message that was delivered. Poll after each of these waits before calling it missing.
DELIVERY_POLL_WAITS_S = (1.5, 2, 4)

_DEGREE_POOL = re.compile(r"\b(?:ph\.?\s?d|doctoral|masters?|mba)\b", re.I)
_ET = ZoneInfo("America/New_York")
_VERDICT = {"APPLY": "APPLY", "APPLY_CAVEAT": "APPLY with a caveat", "INELIGIBLE": "INELIGIBLE",
            "PENDING": "pending (the AI was unavailable; it retries automatically)"}
# The dashboard's statuses run in order, so a later one means he applied (migrations/20260823_*).
_APPLIED_OR_LATER = {"applied", "heard_back", "interview", "offer", "rejected"}


def is_internship_title(title: str) -> bool:
    return bool(cs.INTERN_TITLE.search(title or ""))


def wanted(title: str, on_students_page: bool) -> bool:
    """Whether a new Apple posting is pushed. Everything on the internships page is, except PhD, Masters
    and MBA pools; from Apple's full job list (the sitemap) only internship titles."""
    if _DEGREE_POOL.search(title or ""):
        return False
    return on_students_page or is_internship_title(title)


def req_of(posting: dict) -> str:
    """The requisition a posting belongs to: '200673612' for '.../details/200673612-0836/...'."""
    if posting.get("req"):
        return posting["req"]
    m = re.search(r"/details/(\d+)", posting.get("url") or "")
    return m.group(1) if m else posting.get("url") or ""


def _job_id(posting: dict) -> str:
    return posting.get("id") or _make_job("Apple", "-", "", posting["url"], None, None)["id"]


def _strict_json(key: str) -> dict:
    """The JSON record at key: {} when absent or corrupt. Raises StateUnavailable when bot_state
    cannot be read, so a caller never takes a blip for a fresh start and overwrites the record."""
    try:
        value = json.loads(read_state(key) or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _ledger() -> dict | None:
    """{"reqs": set, "sitemap_seeded": bool}, or None before the first run. Raises StateUnavailable."""
    raw = read_state(LEDGER_KEY)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return {"reqs": set(data["reqs"]), "sitemap_seeded": bool(data.get("sitemap_seeded"))}
    except (ValueError, KeyError, TypeError):
        log.error("Apple alert ledger is unreadable — starting it again from the current page")
        return None


def _write_ledger(reqs: set[str], sitemap_seeded: bool) -> None:
    set_state(LEDGER_KEY, json.dumps({"reqs": sorted(reqs), "sitemap_seeded": sitemap_seeded}))


def needs_full_read() -> bool:
    """True before the first run: the ledger is seeded from every page, never from page 1 alone, or the
    postings further down would each look new an hour later."""
    try:
        return _ledger() is None
    except StateUnavailable:
        return False


# ── the push ──────────────────────────────────────────────────────────────────

def _when(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _day(value) -> str:
    when = _when(value)
    return f"{when.astimezone(_ET):%b} {when.astimezone(_ET).day}" if when else "?"


def _locations(postings: list[dict]) -> str:
    names: list[str] = []
    for p in postings:
        for name in (p.get("location") or "").split(", "):
            if name and name not in names:
                names.append(name)
    return ", ".join(names) or "United States"


def _posted(postings: list[dict], now: datetime) -> str:
    dates = [d for d in (_when(p.get("posted_at")) for p in postings) if d]
    if not dates:
        return ""
    minutes = (now - min(dates)).total_seconds() / 60
    if minutes < 60:
        return f"Posted {max(int(minutes), 0)} min ago"
    if minutes < 48 * 60:
        return f"Posted {int(minutes // 60)} h ago"
    return f"Posted {_day(min(dates).isoformat())}"


def _verdict(tier: str, reason: str) -> str:
    label = _VERDICT.get(tier, tier or "unknown")
    return f"Bot's verdict: {label}" + (f" — {reason}" if reason and tier != "PENDING" else "")


def _stored_rows(postings: list[dict]) -> list[dict]:
    """Stored Apple rows for this requisition (any city) or this title. [] on any error: the push then
    goes out without the lines these feed, rather than not at all."""
    keys = sorted({job_norm_key({"company": "Apple", "title": p["title"]}) for p in postings})
    filters = [f'norm_key.eq."{k}"' for k in keys] + [f'url.like."*/details/{req_of(postings[0])}-*"']
    try:
        return (get_client().table("jobs").select("id,status,tier,reason,found_at")
                .eq("company", "Apple").or_(",".join(filters)).limit(50).execute().data or [])
    except Exception as exc:
        log.warning("Apple push: tracker lookup failed (%s) — sending without it", type(exc).__name__)
        return []


def describe(postings: list[dict], processed: dict, now: datetime, stored: list[dict] | None = None):
    """(title, body, url) for one requisition's push. processed: rows this pass ran through the
    classifier, by id -- their verdict is the freshest. A requisition the ATS loop dropped as a stored
    title has none; the stored rows say what it was taken for, and whether he applied to it before."""
    ids = [_job_id(p) for p in postings]
    stored = _stored_rows(postings) if stored is None else stored
    job = next((processed[i] for i in ids if processed.get(i, {}).get("tier")), None)
    own = [r for r in stored if r.get("id") in ids]
    lines = [_locations(postings)]
    if job:
        lines.append(_verdict(job["tier"], job.get("reason", "")))
    elif own:
        lines.append(_verdict(own[0].get("tier"), own[0].get("reason", "")) + f" (rated {_day(own[0].get('found_at'))})")
    elif stored:
        lines.append("Not rated: a role with this title is already in your tracker, so the bot took this "
                     "one for the same job.")
    else:
        lines.append("Bot's verdict: not rated yet")
    lines.append(_posted(postings, now))
    if any(r.get("status") in _APPLIED_OR_LATER for r in own):
        lines.append("You've already applied to this posting.")
    earlier = [r for r in stored if r.get("id") not in ids and r.get("status") in _APPLIED_OR_LATER]
    if earlier:
        first = min((r.get("found_at") or "" for r in earlier), default="")
        lines.append(f"You applied to an earlier posting of this title (first seen {_day(first)}).")
    return postings[0]["title"], "\n".join(line for line in lines if line), postings[0]["url"]


def alert(postings: list[dict], processed: list[dict] = (), on_students_page: bool = True,
          complete: bool = True, now: datetime | None = None) -> int:
    """Push 🍎 for each requisition in postings the ledger has not seen, then record them. Returns the
    pushes sent. processed: the rows this pass classified (they carry the verdict). complete: whether
    postings is the whole internships page, which the first run needs before it seeds the ledger.
    From the sitemap (on_students_page=False), postings is every internship title it lists; the first
    sitemap read after seeding records them all without a push (the sitemap carries months-old
    postings the internships page does not, such as Feb 2026's Finance Development Program).

    A push that fails is not recorded, so the next read sends it again: a duplicate costs one swipe,
    a requisition recorded but never pushed is the miss this module exists to prevent."""
    if not postings and on_students_page:
        return 0
    now = now or datetime.now(timezone.utc)
    try:
        ledger = _ledger()
    except StateUnavailable as exc:
        log.error("Apple alerts skipped this pass: ledger unreadable (%s)", exc)
        return 0

    groups: dict[str, list[dict]] = {}
    for p in postings:
        groups.setdefault(req_of(p), []).append(p)
    if ledger is None:
        if on_students_page and complete:
            # The sitemap is seeded in the same instant, so nothing can arrive between the two seeds and
            # be recorded unpushed. If it cannot be fetched now, its first later read seeds it instead.
            sitemap = cs.apple_sitemap_internships()
            reqs = set(groups) | {req_of(j) for j in sitemap or []}
            _write_ledger(reqs, sitemap_seeded=sitemap is not None)
            log.info("Apple alerts: first run, %d requisitions recorded as already seen (%s); nothing pushed", len(reqs),
                     "internships page and sitemap" if sitemap is not None else "internships page; sitemap unavailable")
        else:
            log.info("Apple alerts: waiting for one complete read of the internships page before the first run")
        return 0
    seen = ledger["reqs"]
    if not on_students_page and not ledger["sitemap_seeded"]:
        _write_ledger(seen | set(groups), sitemap_seeded=True)
        log.info("Apple alerts: first sitemap read, %d internship requisitions recorded as already seen; "
                 "nothing pushed", len(set(groups) - seen))
        return 0

    fresh = {r: ps for r, ps in groups.items() if r not in seen}
    if not fresh:
        return 0
    to_push = {r: ps for r, ps in fresh.items() if wanted(ps[0]["title"], on_students_page)}
    done = set(fresh) - set(to_push)
    by_id = {j.get("id"): j for j in processed}
    sent = 0
    if len(to_push) > GROUPED_PUSH_OVER:
        body = "\n".join(f"• {ps[0]['title']} ({_locations(ps)})" for ps in to_push.values())
        if notifier.push_apple(f"{len(to_push)} new internship postings", body, cs.APPLE_STUDENTS_URL):
            done |= set(to_push)
            sent = 1
    else:
        for r, ps in to_push.items():
            if notifier.push_apple(*describe(ps, by_id, now)):
                done.add(r)
                sent += 1
    log.info("Apple alerts: %d new requisitions, %d pushes sent, %d recorded without a push (degree pool or "
             "not an internship title)", len(fresh), sent, len(set(fresh) - set(to_push)))
    if done:
        _write_ledger(seen | done, ledger["sitemap_seeded"])
    return sent


# ── health of the internships page ────────────────────────────────────────────

def record_students_read(read: "cs.AppleRead | None", now: float | None = None) -> None:
    """Alarm once per outage: the page has been unreadable or empty twice running, or has failed for an
    hour. A recovery after an alarm is announced too. read is page 1's read (None: inside the interval).
    An empty page counts as a failure: Apple has listed 25 for months, and an empty page served to
    GitHub's runners is the very symptom of 2026-10-29."""
    if read is None:
        return
    now = now or time.time()
    try:
        h = _strict_json(HEALTH_KEY)
    except StateUnavailable as exc:
        log.warning("Apple page health not recorded this pass: bot_state unreadable (%s)", exc)
        return
    outcome, problem = read.outcome, read.problem
    if outcome == "ok" and not read.postings:
        outcome, problem = "empty", "the page lists no postings"
    if outcome == "ok":
        if h.get("alerted") and not notifier.push_canary(
                "Apple's internships page is readable again — new Apple roles reach you as normal."):
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
            message = (f"Apple's internships page loads, but the bot can't read any postings from it ({problem})."
                       " Apple has probably changed the page; new Apple roles won't reach you until the reader is fixed.")
        elif now - h["failing_since"] >= REFUSED_ALERT_AFTER_S:
            message = (f"Apple has turned away the bot's internship check for "
                       f"{int((now - h['failing_since']) // 60)} minutes ({h['problem']}). "
                       "New Apple roles won't reach you until it stops.")
        if message and notifier.push_canary(message):
            h["alerted"] = True
    set_state(HEALTH_KEY, json.dumps(h))


def health_summary() -> str:
    try:
        h = _strict_json(HEALTH_KEY)
    except (StateUnavailable, QuotaExceeded):
        return "Internships page: status unknown (the bot's state could not be read)."
    if not h:
        return "Internships page: reading normally."
    since = datetime.fromtimestamp(h.get("failing_since", time.time()), timezone.utc)
    return f"Internships page: failing since {_day(since.isoformat())} {since.astimezone(_ET):%H:%M} ET ({h.get('problem')})."


# ── shadow watch: the same reader, on a page where Apple posts every day ──────

def _minutes_after(posting: dict, now: float) -> int | None:
    when = _when(posting.get("posted_at"))
    return round((now - when.timestamp()) / 60) if when else None


def shadow_check(now: float | None = None) -> None:
    """Hourly: page 1 of every U.S. Apple job, newest first, compared with the ids seen before. Then
    the same hour's check that the weekly drill is still passing."""
    now = now or time.time()
    last = _when(get_state(SHADOW_CHECKED_KEY))
    if last and now - last.timestamp() < SHADOW_INTERVAL_S:
        return
    try:
        s = _strict_json(SHADOW_KEY)
    except StateUnavailable as exc:
        log.warning("Apple shadow watch put off to the next pass: bot_state unreadable (%s)", exc)
        return
    set_state(SHADOW_CHECKED_KEY, datetime.fromtimestamp(now, timezone.utc).isoformat())

    read = cs.read_apple_pages(cs.APPLE_ALL_US_URL, 1, 1)
    if read.outcome == "ok" and read.postings:
        seen = s.get("seen") or []
        ids = [p["apple_id"] for p in read.postings]
        if not seen:
            new = []
            s["last_new_at"] = now
            log.info("Apple shadow watch: first run, %d postings recorded", len(ids))
        else:
            known = set(seen)
            new = [p for p in read.postings if p["apple_id"] not in known]
            s.setdefault("log", []).extend([round(now), _minutes_after(p, now)] for p in new)
            if new:
                if s.get("alerted") and not notifier.push_canary(
                        "Apple's job list is showing new postings again — the Apple reader works."):
                    pass    # all-clear not delivered: stay alerted, so it is sent with the next new posting
                else:
                    s.pop("alerted", None)
                s["last_new_at"] = now
        s["seen"] = (ids + [i for i in seen if i not in ids])[:SHADOW_SEEN_MAX]
        delays = sorted(d for d in (_minutes_after(p, now) for p in new) if d is not None)
        log.info("Apple shadow watch: %d listed, %d new%s", len(ids), len(new),
                 f", seen {delays[0]}-{delays[-1]} min after Apple's posting time" if delays else "")
    else:
        log.warning("Apple shadow watch read nothing this hour (%s: %s)", read.outcome, read.problem or "empty page")

    s["log"] = [e for e in s.get("log", []) if now - e[0] < SHADOW_LOG_DAYS * 86400][-2000:]
    s.setdefault("last_new_at", now)
    if now - s["last_new_at"] >= SHADOW_SILENT_ALERT_S and not s.get("alerted"):
        if notifier.push_canary("For 48 hours the bot has seen no new U.S. Apple jobs. Apple posts dozens a day, "
                                "so its Apple reader has probably stopped working — new Apple internships may not "
                                "reach you."):
            s["alerted"] = True
    set_state(SHADOW_KEY, json.dumps(s))
    _check_drill_ran(now)


def _check_drill_ran(now: float) -> None:
    """An alarm when the weekly drill has not passed in DRILL_STALE_S: GitHub drops some scheduled runs
    and disables schedules after 60 days without a commit, and a drill that never runs proves nothing.
    The clock starts at the first check, so a fresh install gets its first week."""
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
        if notifier.push_canary(f"The weekly Apple drill {what}. Run it from GitHub: Actions → Apple Alert Drill → "
                                "Run workflow, and check why it stopped."):
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
        return "Shadow watch: no new U.S. Apple jobs seen in 7 days — the reader may be broken."
    delays = [d for _, d in week if d is not None and d >= 0]
    typical = f", typically {round(statistics.median(delays))} min after Apple posted them" if delays else ""
    return f"Shadow watch: {len(week)} new U.S. Apple jobs seen in the last 7 days{typical}."


# ── the weekly drill ──────────────────────────────────────────────────────────

def _delivered(msg_id: str, since: float) -> bool:
    """Whether ntfy holds the message: the topic's own feed (what the phone app reads), polled after
    each of DELIVERY_POLL_WAITS_S."""
    for wait in DELIVERY_POLL_WAITS_S:
        time.sleep(wait)
        try:
            r = requests.get(f"{notifier.NTFY_BASE}/{notifier.NTFY_TOPIC}/json",
                             params={"poll": "1", "since": str(int(since) - 5)}, timeout=15)
            r.raise_for_status()
            if any(json.loads(line).get("id") == msg_id for line in r.text.splitlines() if line.strip()):
                return True
        except Exception as exc:
            log.warning("Apple drill: could not read the push back (%s)", type(exc).__name__)
    return False


def _retry(attempt, ok):
    """attempt() up to DRILL_TRIES times, DRILL_RETRY_GAP_S apart, until ok(result); the last result."""
    for n in range(DRILL_TRIES):
        if n:
            time.sleep(DRILL_RETRY_GAP_S)
        result = attempt()
        if ok(result):
            break
    return result


def _production_problem(now: float) -> str | None:
    """Why the 5-minute Apple check is not working as it should, or None. Raises StateUnavailable.
    The drill's own reads retry; production's do not, so a production outage must fail the drill
    rather than hide behind a drill that got through."""
    last = _when(read_state(cs.APPLE_STUDENTS_STATE_KEY))
    if last is None:
        return "it has never run"
    age = now - last.timestamp()
    if age > DRILL_MAX_CHECK_AGE_S:
        return f"it last ran {int(age // 60)} minutes ago"
    if read_state(LEDGER_KEY) is None:
        return "it has not recorded Apple's postings yet"
    shadow = _when(read_state(SHADOW_CHECKED_KEY))
    if shadow is None or now - shadow.timestamp() > DRILL_MAX_SHADOW_AGE_S:
        return "its passes stop before the sitemap and shadow-watch checks"
    h = _strict_json(HEALTH_KEY)
    if h.get("alerted") or (h.get("failing_since") and now - h["failing_since"] >= REFUSED_ALERT_AFTER_S):
        since = datetime.fromtimestamp(h.get("failing_since", now), timezone.utc)
        return (f"its reads of the internships page have failed since {_day(since.isoformat())} "
                f"{since.astimezone(_ET):%H:%M} ET ({h.get('problem')})")
    return None


def _detail_ok(got) -> bool:
    status, page = got
    try:
        return status == 200 and bool(cs.parse_apple_detail(page))
    except Exception:
        return False


def drill(now: datetime | None = None) -> int:
    """Check the 5-minute Apple check is running, then treat one real posting as new and send what a new
    one would send, marked DRILL. Reads the live site, the posting's page and the tracker, and calls the
    classifier; writes nothing to the tracker, only DRILL_KEY's pass stamp. Returns 0 when every step
    worked; otherwise 1, with a canary naming the step that failed."""
    now = now or datetime.now(timezone.utc)

    def fail(step: str, detail: str) -> int:
        log.error("Apple drill FAILED at %s: %s", step, detail)
        if notifier.push_canary(f"Weekly Apple drill FAILED at {step} ({detail}). "
                                "A new Apple internship may not reach you until this is fixed."):
            # The workflow's last step alarms for a run that dies without reaching here; not twice.
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a") as out:
                    out.write("alerted=true\n")
        return 1

    if not notifier.NTFY_TOPIC:
        log.error("Apple drill: NTFY_TOPIC is not set")
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
                    log.info("Apple drill: passed at %s already (the 9 AM pinger); this scheduled run stands down",
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
        return fail("checking the 5-minute Apple check", problem)

    read = _retry(lambda: cs.read_apple_pages(cs.APPLE_STUDENTS_URL, 1, 1, expect_team="INTRN"),
                  lambda r: r.outcome == "ok" and r.postings)
    if read.outcome != "ok" or not read.postings:
        return fail("reading Apple's internships page", read.problem or "no postings on it")
    pick = (next((p for p in read.postings if p["title"].lower() == DRILL_TITLE.lower()), None)
            or next((p for p in read.postings if wanted(p["title"], True) and is_internship_title(p["title"])), None))
    if not pick:
        return fail("choosing a posting", "no undergrad internship on the page")

    job = cs._apple_stubs([pick])[0]
    status, page = _retry(lambda: cs._apple_get(job["url"]), _detail_ok)
    unreadable = None
    try:
        job["description"] = cs.parse_apple_detail(page) if status == 200 else None
    except Exception as exc:
        job["description"], unreadable = None, f"its page is unreadable ({type(exc).__name__})"
    if not job["description"]:
        return fail("opening the posting", unreadable or (f"HTTP {status}" if status != 200 else "no description on its page"))
    result = classify(job)
    if result.get("failed"):
        job.update(tier="PENDING", reason="")
        log.warning("Apple drill: the classifier failed (%s); the push says so", result.get("failed_kind"))
    else:
        job.update(tier=result.get("tier", "APPLY_CAVEAT"), reason=result.get("reason", ""),
                   suggested_resume=result.get("suggested_resume", "General"))

    title, body, url = describe([pick], {job["id"]: job}, now)
    body = "\n".join(["Weekly drill, not a new posting: if this reached your phone, the Apple alert works.",
                      body, shadow_summary(), health_summary()])
    sent_at = time.time()
    msg_id = notifier.push_apple(title, body, url, drill=True)
    if not msg_id or msg_id == "sent":
        return fail("sending the 🍎 push", "ntfy did not take it" if not msg_id else "ntfy gave no message id")
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
    log.info("Apple drill passed: %s, verdict %s", pick["title"], job["tier"])
    return 0
