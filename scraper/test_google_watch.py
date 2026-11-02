"""The Google alert, end to end, on Google's real pages.

fixtures/google/ holds google.com/about/careers as it was served to the bot on 2026-11-02: the U.S.
internships list (18 roles, all on its first page) and the first page of every U.S. job (1,728). A
scenario changes only the job list inside a real page -- the data Google embeds for its own scripts
-- and serves the result.

Faked, at the edges only: HTTP to Google and to ntfy, the database (dicts), and the classifier (a
fixed verdict). Real, everything between: the reader, ats_watch.run(), the title pre-filters,
process_job and its push, the 🔎 push, the ledger, the alarms, the shadow watch and the drill.

What it proves without Google posting anything new:
  - a new internship at the top of the list: one 🔎 push with its verdict and how long ago Google
    published it, the usual push too, before the board sweep, and nothing on the pass after.
  - the cases a real role would hit: a republished role, PhD/Masters/MBA-only titles, CapitalG's
    standing roles, LinkedIn's copy already in the tracker, a burst, a failed push, bot_state down,
    Google refusing the bot or changing its page, Google dropping a filter.
  - the shadow watch (weekday hours, so a weekend is not an outage) and the drill.
  - only the first page of results is ever requested: Google's robots.txt disallows the others.

Run: cd scraper && python test_google_watch.py
"""

import copy
import gzip
import hashlib
import json
import logging
import os
import re
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

import requests

import apple_watch as aw
import ats_watch as w
import db
import google_watch as gw
import main
import notifier
from db import job_norm_key

logging.getLogger().setLevel(logging.WARNING)
_dumps = json.dumps
_fails = 0
ET = ZoneInfo("America/New_York")


def check(label, cond, why=""):
    global _fails
    if cond:
        print(f"  PASS  {label}")
    else:
        _fails += 1
        print(f"  FAIL  {label}" + (f"\n        {why}" if why else ""))


# ── Google's real pages ───────────────────────────────────────────────────────
FIX = Path(__file__).parent.parent / "fixtures" / "google"


def _real(name: str) -> str:
    return gzip.open(FIX / f"{name}.html.gz", "rt", encoding="utf-8").read()


INTERNS, ALL_US = _real("interns_us"), _real("all_us")


def _block(page: str):
    """The match of the embedded job list: [roles, null, total, page size]."""
    for m in gw._CALLBACK.finditer(page):
        data = json.loads(m.group(1))
        if isinstance(data, list) and len(data) >= 3 and isinstance(data[2], int):
            return m
    raise AssertionError("no job list in the fixture")


def _data(page: str) -> list:
    return json.loads(_block(page).group(1))


def _with(page: str, records: list | None, total: int | None = None) -> str:
    """The real page with its job list replaced and put back in Google's own form. Every other byte is
    Google's."""
    m = _block(page)
    data = json.loads(m.group(1))
    data[0] = records
    data[2] = len(records or []) if total is None else total
    return page[:m.start(1)] + _dumps(data) + page[m.end(1):]


REAL = _data(INTERNS)[0]
FDE_ID = "75665705386549958"
FDE = next(r for r in REAL if r[0] == FDE_ID)
FDE_TITLE = "Forward Deployed Engineering Intern, BS/MS, Summer 2027"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_record(title: str, gid: str, published: datetime | None = None, countries=("US",)) -> list:
    """A role in Google's own record shape: the real Forward Deployed Engineering record with its
    identifying fields replaced."""
    r = copy.deepcopy(FDE)
    at = int((published or _now() - timedelta(minutes=4, seconds=10)).timestamp())
    r[0], r[1] = gid, title
    r[12], r[13], r[14] = [at - 3600, 0], [at, 0], [at, 0]
    if countries != ("US",):
        r[9] = [[f"Somewhere, {c}", [], "Somewhere", "", "", c] for c in countries]
    return r


def url_of(gid: str) -> str:
    return gw.JOB_URL.format(gid)


def id_of(gid: str) -> str:
    return "ats:" + hashlib.sha1(url_of(gid).encode("utf-8")).hexdigest()[:16]


# ── the outside world, faked ──────────────────────────────────────────────────
ALL_FETCHED: list[str] = []
SITE: dict = {"interns": INTERNS, "all_us": ALL_US, "status": {}, "seq": [], "boom": False}
FETCHED: list[str] = []
ORDER: list[str] = []
PUSHES: list[dict] = []
NTFY = {"google_status": 200, "canary_status": 200, "feed_drop": False, "polls": 0}


class _Resp:
    def __init__(self, status: int, text: str = ""):
        self.status_code, self.text, self.ok = status, text, status < 400

    def json(self):
        return json.loads(self.text or "{}")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def fake_get(url, headers=None, timeout=None, params=None, **kw):
    if url.startswith(notifier.NTFY_BASE):
        assert url == f"{notifier.NTFY_BASE}/{notifier.NTFY_TOPIC}/json", f"read-back from {url}"
        assert (params or {}).get("poll") == "1", f"read-back without poll=1: {params}"
        since = int((params or {}).get("since", 0))
        NTFY["polls"] += 1
        lines = [] if NTFY["feed_drop"] else [_dumps({"id": p["id"], "event": "message", "time": p["time"]})
                                              for p in PUSHES if p["time"] >= since]
        return _Resp(200, "\n".join(lines))
    FETCHED.append(url)
    ALL_FETCHED.append(url)
    if url == gw.INTERNS_URL:
        ORDER.append("google-list")
        if SITE["boom"]:
            raise requests.ConnectionError("reset by peer")
        status = SITE["seq"].pop(0) if SITE["seq"] else SITE["status"].get("interns", 200)
        return _Resp(status, SITE["interns"] if status == 200 else "Too Many Requests")
    if url == gw.ALL_US_URL:
        status = SITE["status"].get("all_us", 200)
        return _Resp(status, SITE["all_us"] if status == 200 else "Forbidden")
    raise AssertionError(f"unexpected fetch: {url}")


def fake_post(url, data=None, headers=None, json=None, timeout=None):
    if json is not None:
        assert url == notifier.NTFY_BASE, f"JSON published to {url}"
        assert json.get("topic") == notifier.NTFY_TOPIC, f"JSON publish without the topic: {json}"
        push = {"kind": "google" if json["title"].startswith("🔎") else "other", "title": json["title"],
                "body": json["message"], "priority": json["priority"], "click": json["click"], "tags": json["tags"]}
        if NTFY["google_status"] != 200:
            return _Resp(NTFY["google_status"])
        ORDER.append("google-push")
    else:
        assert url == f"{notifier.NTFY_BASE}/{notifier.NTFY_TOPIC}", f"published to {url}"
        title = headers.get("Title", "")
        push = {"kind": "canary" if title == "Job scraper alert" else "job", "title": title,
                "body": data.decode("utf-8"), "priority": headers.get("Priority"), "click": headers.get("Click")}
        if push["kind"] == "canary" and NTFY["canary_status"] != 200:
            return _Resp(NTFY["canary_status"])
    push["time"] = int(time.time())
    push["id"] = f"msg{len(PUSHES) + 1}"
    PUSHES.append(push)
    return _Resp(200, _dumps({"id": push["id"], "event": "message"}))


requests.get, requests.post = fake_get, fake_post
notifier.NTFY_TOPIC = "test-topic"
gw.DRILL_RETRY_GAP_S = 0
aw.DELIVERY_POLL_WAITS_S = tuple(0 for _ in aw.DELIVERY_POLL_WAITS_S)

STORE: dict[str, dict] = {}
STATE: dict[str, str] = {}
# keys: reads of these keys fail. drop_writes: writes to these keys are lost (db.set_state swallows it).
STATE_DOWN = {"keys": set(), "drop_writes": set()}
CLAIMED: set[str] = set()
CLASSIFIED: list[str] = []
VERDICT = {"tier": "APPLY", "reason": "Undergrad CS internship, Summer 2027"}
QUERIES: list[str] = []


def store_row(job: dict, tier="APPLY_CAVEAT", reason="stored earlier", status="new",
              found_at="2026-10-12T10:20:00+00:00") -> None:
    STORE[job["id"]] = {"id": job["id"], "title": job["title"], "company": job["company"], "url": job["url"],
                        "norm_key": job_norm_key(job), "tier": tier, "reason": reason, "status": status,
                        "found_at": found_at}


def fake_unknown(jobs, batch_size=5000):
    keys = {r["norm_key"] for r in STORE.values()}
    return {j["id"] for j in jobs if j["id"] not in STORE and (j.get("norm_key") or job_norm_key(j)) not in keys}


def fake_insert(job):
    if job["id"] not in STORE:
        store_row(job, tier=job.get("tier", "APPLY_CAVEAT"), reason=job.get("reason", ""),
                  found_at=_now().isoformat())
    return True


def fake_classify(job):
    CLASSIFIED.append(job["title"])
    assert job.get("description"), f"classified without a description: {job['title']}"
    if VERDICT.get("failed"):
        return {"failed": True, "failed_kind": "billing"}
    return dict(VERDICT, suggested_resume="General")


def fake_claim(job_id):
    if job_id in CLAIMED:
        return False, "already-notified"
    CLAIMED.add(job_id)
    return True, "claimed"


def read_state(key):
    if key in STATE_DOWN["keys"]:
        raise db.StateUnavailable(key)
    return STATE.get(key)


def get_state(key):
    """db.get_state reports a failed read as a missing key."""
    return None if key in STATE_DOWN["keys"] else STATE.get(key)


def set_state(key, value):
    if key not in STATE_DOWN["drop_writes"]:
        STATE[key] = value


class _Query:
    """Just enough of the Supabase query builder for google_watch._stored_rows, evaluating its filter."""

    def __init__(self):
        self.filters, self.expr, self.cols = {}, "", []

    def table(self, name):
        assert name == "jobs", name
        return self

    def select(self, cols):
        self.cols = cols.split(",")
        return self

    def eq(self, col, value):
        self.filters[col] = value
        return self

    def or_(self, expr):
        self.expr = expr
        QUERIES.append(expr)
        return self

    def limit(self, n):
        return self

    def execute(self):
        terms = re.findall(r'(\w+)\.eq\."([^"]*)"', self.expr)
        assert terms and len(terms) == self.expr.count('."'), f"malformed filter: {self.expr}"
        rows = [r for r in STORE.values() if all(r.get(k) == v for k, v in self.filters.items())
                and any(r.get(col) == val for col, val in terms)]
        return types.SimpleNamespace(data=[{c: r.get(c) for c in self.cols} for r in rows])


gw.get_state, gw.set_state, gw.read_state = get_state, set_state, read_state
gw.clear_state = lambda key: STATE.pop(key, None)
gw.find_unknown_candidates = w.find_unknown_candidates = fake_unknown
gw.get_client = _Query
gw.classify = main.classify = fake_classify
main.get_job_row = lambda jid: ({k: STORE[jid][k] for k in ("id", "tier", "status")} if jid in STORE else None)
main.insert_job = w.insert_job = fake_insert
main.claim_notification = fake_claim
w.start_run = lambda source="ats": 1
w.finish_run = lambda run_id, **kw: None
BOARD_LISTINGS: list[dict] = []
w.fetch_all_listings = lambda cfg: (ORDER.append("boards"), [dict(j) for j in BOARD_LISTINGS])[1]
# Apple's checks run in the same pass; test_apple_watch.py covers them. Here they read nothing.
w.career_sitemaps.collect_apple_students = lambda force=False, full=False, seen_reqs=None: w.career_sitemaps.AppleStudents()
w.career_sitemaps.collect_listings = lambda force=False, apple_sitemap=None: []
w.apple_watch.seen_reqs = lambda: set()
w.apple_watch.shadow_check = lambda now=None: None


def reset(records=REAL, stored=REAL, applied=()):
    """A world where Google lists `records` and the tracker already holds LinkedIn's copy of each of
    `stored` (same company and title, a LinkedIn id), found Oct 12."""
    for d in (STORE, STATE, SITE["status"]):
        d.clear()
    SITE["seq"], SITE["boom"] = [], False
    SITE["interns"], SITE["all_us"] = _with(INTERNS, list(records)), ALL_US
    CLAIMED.clear()
    QUERIES.clear()
    BOARD_LISTINGS.clear()
    STATE_DOWN.update(keys=set(), drop_writes=set())
    os.environ.pop("GITHUB_EVENT_NAME", None)
    os.environ.pop("GITHUB_OUTPUT", None)
    NTFY.update(google_status=200, canary_status=200, feed_drop=False, polls=0)
    VERDICT.clear()
    VERDICT.update(tier="APPLY", reason="Undergrad CS internship, Summer 2027")
    for r in stored:
        job = {"id": f"li:{r[0]}", "company": "Google", "title": r[1].strip(),
               "url": f"https://www.linkedin.com/jobs/view/{r[0]}"}
        store_row(job, status="applied" if r[1] in applied else "new")


def run_pass() -> list[dict]:
    """One 5-minute ATS pass, the last one 5 minutes ago. The shadow watch is held back; it is tested
    on its own below."""
    STATE[gw.STATE_KEY] = (_now() - timedelta(minutes=5)).isoformat()
    STATE[gw.SHADOW_CHECKED_KEY] = _now().isoformat()
    for lst in (FETCHED, ORDER, PUSHES, CLASSIFIED):
        lst.clear()
    w.run()
    return list(PUSHES)


def kinds(pushes: list[dict], kind: str) -> list[dict]:
    return [p for p in pushes if p["kind"] == kind]


def ledger() -> set[str]:
    return set(json.loads(STATE[gw.LEDGER_KEY])["ids"]) if gw.LEDGER_KEY in STATE else set()


def serve(records: list, total: int | None = None) -> None:
    SITE["interns"] = _with(INTERNS, records, total)



# ═════════════════════════════════════════════════════════════════════════════
print("-- the reader, on Google's real pages --")
posts, total = gw.parse_results(INTERNS)
check("the U.S. internships list: 18 roles, all of them on the first page", len(posts) == 18 and total == 18,
      f"{len(posts)} of {total}")
fde = next((p for p in posts if p["google_id"] == FDE_ID), None)
check("Forward Deployed Engineering Intern is read with its id, title and company",
      fde and fde["title"] == FDE_TITLE and fde["company"] == "Google", str(fde and fde["title"]))
check("...linked by its id alone, which Google serves", fde and fde["url"] == url_of(FDE_ID), fde and fde["url"])
check("...in the tracker's location style", fde and re.fullmatch(r".+, USA \+\d+ more", fde["location"]),
      fde and fde["location"])
check("...with Google's publish time (Oct 21, 12:39 UTC), not its creation time (Oct 12)",
      fde and fde["posted_at"].startswith("2026-10-21T12:39"), fde and fde["posted_at"])
d = (fde or {}).get("description") or ""
check("...and the description and qualifications the classifier needs, as plain text",
      len(d) > 1000 and "Qualifications:" in d and "Minimum qualifications" in d and "<" not in d, d[:120])
check("every role has a numeric id, a title and a publish time",
      all(p["google_id"].isdigit() and p["title"] and p["posted_at"] for p in posts))
check("the list is newest first, by publish time", [p["posted_at"] for p in posts]
      == sorted((p["posted_at"] for p in posts), reverse=True))
check("the tracker's id is made from the id-only link, so a title Google edits is the same row",
      gw._stubs([fde])[0]["id"] == id_of(FDE_ID) if fde else False)
all_posts, all_total = gw.parse_results(ALL_US, interns=False)
check("every U.S. job: the newest 20 of 1,728", len(all_posts) == 20 and all_total == 1728,
      f"{len(all_posts)} of {all_total}")

print("\n-- a page the reader cannot read is never 'no roles' --")
for label, page, interns in (
        ("the embedded data is gone", INTERNS.replace("AF_initDataCallback", "AF_dataServiceRequests"), True),
        ("a challenge page", "<html><body>Our systems have detected unusual traffic</body></html>", True),
        ("Google moved the id and title", _with(INTERNS, [[None, None] + r[2:] for r in REAL]), True),
        ("every U.S. job served for the internships list (Google ignores a level it does not know)", ALL_US, True),
        ("most of the U.S. list abroad (Google dropped the location filter)",
         _with(INTERNS, [new_record(f"Software Engineering Intern {i}, Summer 2027", f"90000000000000100{i}",
                                    countries=("IN",)) for i in range(4)] + REAL[:3]), True)):
    try:
        gw.parse_results(page, interns=interns)
        check(f"{label}: unreadable", False, "parsed as if it were fine")
    except gw.GooglePageUnreadable as exc:
        check(f"{label}: unreadable ({exc})", True)
try:
    one_abroad, _ = gw.parse_results(_with(INTERNS, [new_record("Software Engineering Intern, Summer 2027",
                                                                 "900000000000000001", countries=("PR",))] + REAL))
except gw.GooglePageUnreadable as exc:
    one_abroad = []
    print(f"        (raised: {exc})")
check("one role with no U.S. place on the U.S. list is skipped, and the other 18 are read",
      len(one_abroad) == 18 and "900000000000000001" not in {p["google_id"] for p in one_abroad}, str(len(one_abroad)))
check("...and left out of the count, so the whole list still reads as on the page",
      gw.parse_results(_with(INTERNS, [new_record("Software Engineering Intern, Summer 2027", "900000000000000001",
                                                  countries=("PR",))] + REAL))[1] == 18)
empty = _with(INTERNS, None, 0)
check("a genuinely empty list ([null, null, 0, 20], as Google serves one) is no roles, not an error",
      gw.parse_results(empty) == ([], 0))
check("a role in the U.S. and abroad is a U.S. role", len(gw.parse_results(_with(INTERNS, [new_record(
    "Software Engineering Intern, Summer 2027", "900000000000000002", countries=("US", "CA"))]))[0]) == 1)
bare = copy.deepcopy(FDE)
bare[7], bare[9], bare[12], bare[13], bare[14], bare[3], bare[4], bare[10], bare[15] = (None,) * 9
got, _ = gw.parse_results(_with(INTERNS, [bare]))
check("a role missing everything but its id and title is still read, with defaults",
      got and got[0]["company"] == "Google" and got[0]["location"] == "United States" and got[0]["posted_at"] is None
      and got[0]["description"] is None, str(got))
check("a short record is skipped, not a crash", gw.parse_results(_with(INTERNS, [["1"]] + REAL))[0][0]["google_id"]
      == REAL[0][0])

print("\n-- which roles push --")
for title, want in [(FDE_TITLE, True),
                    ("Student Researcher, BS/MS, Winter-Summer 2027", True),
                    ("Software Engineering Intern, BS, Summer 2027", True),
                    ("Software Engineering Intern, Summer 2027", True),
                    ("User Experience Design Intern, Summer 2027, YouTube UX", True),
                    ("Software Engineering Intern, MS, Summer 2027", False),
                    ("Business Data Scientist Intern, PhD, Summer 2027", False),
                    ("Research Scientist PhD Intern, Summer 2027", False),
                    ("MBA Intern, Summer 2027", False),
                    ("Student Researcher, PhD, Fall 2026", False),
                    ("Open Engineering Career Opportunities, CapitalG Portfolio Companies", False),
                    ("Software Engineer II, Early Career", False),
                    ("Internal Systems Analyst", False)]:
    check(f"{title!r} -> {'push' if want else 'no push'}", gw.wanted(title) is want)

# ═════════════════════════════════════════════════════════════════════════════
print("\n-- the first pass after deploy: record what is there, push nothing --")
reset()
pushes = run_pass()
check("reads the internships list once", FETCHED == [gw.INTERNS_URL], str(FETCHED))
check("records all 18 roles", ledger() == {r[0] for r in REAL}, str(len(ledger())))
check("pushes nothing; classifies nothing the tracker already has", pushes == [] and CLASSIFIED == [], str(pushes))
check("the list's health is recorded as fine", gw.HEALTH_KEY not in STATE)
pushes = run_pass()
check("the pass after is quiet", pushes == [] and FETCHED == [gw.INTERNS_URL], str(pushes))

print("\n-- a pass inside 4 minutes of the last one reads nothing --")
STATE[gw.STATE_KEY] = (_now() - timedelta(minutes=2)).isoformat()
FETCHED.clear()
gw.check(lambda rows: (0, 0))
check("no request to Google", FETCHED == [], str(FETCHED))

print("\n-- a new internship appears at the top of the list --")
reset()
run_pass()
NEW_ID, NEW_TITLE = "900000000000000010", "Software Engineering Intern, BS, Summer 2028"
serve([new_record(NEW_TITLE, NEW_ID)] + REAL)
pushes = run_pass()
google = kinds(pushes, "google")
check("exactly one 🔎 push", len(google) == 1, str([p["title"] for p in google]))
g = google[0] if google else {"title": "", "body": "", "priority": None, "click": None, "tags": None}
check("titled with the 🔎 and Google's own title", g["title"] == f"🔎 GOOGLE - {NEW_TITLE}", g["title"])
check("its cities first, all three", g["body"].splitlines()[0] == "Sunnyvale, CA, USA; New York, NY, USA; Reston, VA, USA",
      g["body"])
five = gw.parse_results(_with(INTERNS, [new_record(NEW_TITLE, NEW_ID, countries=("US",) * 5)]))[0][0]
check("five cities: the first three and how many more", gw._where(five) ==
      "Somewhere, US; Somewhere, US; Somewhere, US and 2 more", gw._where(five))
check("with the bot's verdict and reason", "Bot's verdict: APPLY — Undergrad CS internship, Summer 2027" in g["body"],
      g["body"])
check("with how long ago Google published it", "Posted 4 min ago" in g["body"], g["body"])
check("at ntfy's top priority, opening the role", g["priority"] == 5 and g["click"] == url_of(NEW_ID),
      f"{g['priority']} {g['click']}")
check("the usual push goes out too", [p["title"] for p in kinds(pushes, "job")]
      and all(p["title"].endswith(f"Google - {NEW_TITLE}") for p in kinds(pushes, "job")),
      str([p["title"] for p in kinds(pushes, "job")]))
check("classified once, on the description from the list itself", CLASSIFIED == [NEW_TITLE], str(CLASSIFIED))
check("stored in the tracker, under the id-only link", STORE.get(id_of(NEW_ID), {}).get("url") == url_of(NEW_ID))
check("the 🔎 push lands before the 104-board sweep starts",
      "google-push" in ORDER and ORDER.index("google-push") < ORDER.index("boards"), str(ORDER))
check("recorded", NEW_ID in ledger())
pushes = run_pass()
check("the pass after: no push, no second classification", pushes == [] and CLASSIFIED == [], str(pushes))

print("\n-- a role Google republishes rises to the top: not new --")
reset()
run_pass()
bumped = copy.deepcopy(REAL[5])
bumped[14] = [int(_now().timestamp()) - 60, 0]
serve([bumped] + REAL[:5] + REAL[6:])
check("no push", run_pass() == [])

print("\n-- graduate-only titles, CapitalG's standing roles, and a BS/MS role together --")
reset()
run_pass()
batch = [new_record("Software Engineering Intern, PhD, Summer 2028", "900000000000000021"),
         new_record("MBA Intern, Summer 2028", "900000000000000022"),
         new_record("Open Product Career Opportunities, CapitalG Portfolio Companies", "900000000000000023"),
         new_record("Data Analyst Intern, BS/MS, Summer 2028", "900000000000000024")]
serve(batch + REAL)
pushes = run_pass()
check("one 🔎 push, for the BS/MS role only", [p["title"] for p in kinds(pushes, "google")]
      == ["🔎 GOOGLE - Data Analyst Intern, BS/MS, Summer 2028"], str([p["title"] for p in kinds(pushes, "google")]))
check("all four are recorded, so the others never push later", {r[0] for r in batch} <= ledger())
check("the CapitalG role is stored by the title pre-filter, never classified",
      "Open Product Career Opportunities, CapitalG Portfolio Companies" not in CLASSIFIED
      and any(r["title"].startswith("Open Product") and r["tier"] == "INELIGIBLE" for r in STORE.values()))

print("\n-- a Student Researcher role: Google lists it as an internship, so the classifier rates it --")
reset()
run_pass()
SR_ID, SR_TITLE = "900000000000000040", "Student Researcher, BS/MS, Summer 2028"
serve([new_record(SR_TITLE, SR_ID)] + REAL)
pushes = run_pass()
google = kinds(pushes, "google")
check("classified, not dropped by the title pre-filter", CLASSIFIED == [SR_TITLE], str(CLASSIFIED))
check("its 🔎 push carries the classifier's verdict", google and "Bot's verdict: APPLY — Undergrad CS" in google[0]["body"],
      str(google))
check("stored with that verdict", STORE.get(id_of(SR_ID), {}).get("tier") == "APPLY")
check("the tracker's title pre-filter takes Student Researcher as an internship from any source (LinkedIn too)",
      not main._is_non_internship_title(SR_TITLE) and not main._is_non_internship_title("Student Researcher (Seed) - 2027"))
check("...and still not a student-department job", main._is_non_internship_title("Research Coordinator, Student Affairs"))

print("\n-- a graduate-only role Google later opens to undergraduates --")
reset()
run_pass()
phd = next(r for r in REAL if r[1] == "Software Engineering Intern, PhD, Summer 2027")
check("seeded: the PhD role is recorded as skipped", phd[0] in set(json.loads(STATE[gw.LEDGER_KEY])["skipped"]))
retitled = copy.deepcopy(phd)
retitled[1] = "Software Engineering Intern, BS/MS/PhD, Summer 2027"
serve([retitled] + [r for r in REAL if r is not phd])
pushes = run_pass()
check("retitled to take undergraduates: one 🔎 push", [p["title"] for p in kinds(pushes, "google")]
      == ["🔎 GOOGLE - Software Engineering Intern, BS/MS/PhD, Summer 2027"], str(pushes))
check("...no longer skipped, so it never pushes again", phd[0] not in set(json.loads(STATE[gw.LEDGER_KEY])["skipped"])
      and run_pass() == [])
reset(stored=[r for r in REAL if r is not phd])
run_pass()
check("a PhD role not in the tracker is rated and stored under its Google id on the first pass",
      STORE.get(id_of(phd[0]), {}).get("title") == phd[1], str(CLASSIFIED))
serve([retitled] + [r for r in REAL if r is not phd])
google = kinds(run_pass(), "google")
check("retitled: the push says its verdict was for the old title", google and
      f'(rated {aw._day(STORE[id_of(phd[0])]["found_at"])} as "{phd[1]}")' in google[0]["body"], google and google[0]["body"])
ms_id = "900000000000000050"
serve([new_record("Software Engineering Intern, MS, Summer 2028", ms_id)] + REAL)
check("a new MS-only role: no push", kinds(run_pass(), "google") == [])
serve([new_record("Software Engineering Intern, BS/MS, Summer 2028", ms_id)] + REAL)
NTFY["google_status"] = 502
run_pass()
check("retitled BS/MS, but ntfy refuses the push: still skipped, so it is tried again",
      ms_id in set(json.loads(STATE[gw.LEDGER_KEY])["skipped"]))
NTFY["google_status"] = 200
check("...and sent on the next pass", len(kinds(run_pass(), "google")) == 1)

print("\n-- the ledger is capped, never dropping a role still on the list --")
reset()
_max = gw.LEDGER_MAX
gw.LEDGER_MAX = 22
run_pass()
older = [new_record(f"Data Intern {i}, BS, Summer 2028", f"9000000000000006{i:02d}") for i in range(4)]
serve(older + REAL)
run_pass()
newer = [new_record(f"Product Intern {i}, BS, Summer 2028", f"9000000000000007{i:02d}") for i in range(3)]
serve(newer + REAL)
run_pass()
ids = json.loads(STATE[gw.LEDGER_KEY])["ids"]
gw.LEDGER_MAX = _max
check("trimmed to the cap", len(ids) == 22, str(len(ids)))
check("every role on the list now is kept", {r[0] for r in newer + REAL} <= set(ids))
check("the oldest recorded that left the list went first", set(ids) - {r[0] for r in newer + REAL} == {older[3][0]},
      str(sorted(set(ids) - {r[0] for r in newer + REAL})))

print("\n-- LinkedIn's copy is already in the tracker, and he applied to it --")
reset(applied=(FDE_TITLE,))
run_pass()
STORE[f"li:{FDE_ID}"]["tier"], STORE[f"li:{FDE_ID}"]["reason"] = "APPLY", "BS/MS internship, CS majors"
repost = new_record(FDE_TITLE, "900000000000000030")
serve([repost] + REAL)
pushes = run_pass()
google = kinds(pushes, "google")
body = google[0]["body"] if google else ""
check("a 🔎 push all the same: Google gave it a new id", len(google) == 1, str(pushes))
check("with the stored copy's verdict, and where it came from",
      "Bot's verdict: APPLY — BS/MS internship, CS majors (rated Oct 12, from another listing of this title)" in body, body)
check("and that he applied to a role with this exact title", "You've applied to a role with this exact title (first seen Oct 12)."
      in body, body)
check("no classification and no usual push: the tracker path is unchanged",
      CLASSIFIED == [] and kinds(pushes, "job") == [], str(CLASSIFIED))
check("the tracker lookup asked for this title and this role's own id",
      QUERIES and f'id.eq."{id_of(repost[0])}"' in QUERIES[-1] and "norm_key.eq." in QUERIES[-1], str(QUERIES[-1:]))

print("\n-- a burst: one grouped push --")
reset()
run_pass()
burst = [new_record(f"Software Engineering Intern {i}, BS, Summer 2028", f"90000000000000010{i}") for i in range(5)]
serve(burst + REAL)
pushes = run_pass()
google = kinds(pushes, "google")
check("one push naming all five", len(google) == 1 and google[0]["title"] == "🔎 GOOGLE - 5 new internships"
      and all(f"Intern {i}," in google[0]["body"] for i in range(5)), str(google))
check("opening the list itself", google and google[0]["click"] == gw.INTERNS_URL)
check("no note about more: the whole list is on the page", google and "in all" not in google[0]["body"])
check("all five recorded", {r[0] for r in burst} <= ledger())

print("\n-- a burst bigger than the one page the bot may read --")
reset(records=REAL[:10], stored=REAL[:10])
run_pass()
big = [new_record(f"Hardware Engineering Intern {i}, BS, Summer 2028", f"9000000000000002{i:02d}") for i in range(20)]
serve(big, total=31)
pushes = run_pass()
google = kinds(pushes, "google")
check("the grouped push says the rest is on Google's list", google and "Google lists 31 in all; the bot reads only "
      "the newest 20." in google[0]["body"], google and google[0]["body"][-120:])

print("\n-- a push that fails is sent again, once --")
reset()
run_pass()
serve([new_record(NEW_TITLE, NEW_ID)] + REAL)
NTFY["google_status"] = 502
run_pass()
check("ntfy refused it: not recorded", NEW_ID not in ledger())
NTFY["google_status"] = 200
pushes = run_pass()
check("the next pass sends it", len(kinds(pushes, "google")) == 1 and NEW_ID in ledger(), str(pushes))
check("and the one after does not", run_pass() == [])

print("\n-- bot_state down: no pushes, no re-seeding, then a normal push --")
reset()
run_pass()
seeded = STATE[gw.LEDGER_KEY]
serve([new_record(NEW_TITLE, NEW_ID)] + REAL)
STATE_DOWN["keys"] = {gw.LEDGER_KEY}
pushes = run_pass()
STATE_DOWN["keys"] = set()
check("ledger unreadable: no 🔎 push, and the ledger is left as it was",
      kinds(pushes, "google") == [] and STATE[gw.LEDGER_KEY] == seeded, str(pushes))
pushes = run_pass()
check("readable again: the new role is pushed", len(kinds(pushes, "google")) == 1, str(pushes))

print("\n-- the bot failing to rate a new role does not hold back its 🔎 push --")
reset()
run_pass()
serve([new_record(NEW_TITLE, NEW_ID)] + REAL)
VERDICT["failed"] = True
pushes = run_pass()
google = kinds(pushes, "google")
check("pushed, saying the rating is pending", len(google) == 1 and "Bot's verdict: pending" in google[0]["body"],
      str(google))

print("\n-- Google turns the bot away --")
reset()
run_pass()
t = time.time()
gw.record_read(gw.GoogleRead(outcome="refused", problem="HTTP 429"), now=t)
gw.record_read(gw.GoogleRead(outcome="refused", problem="HTTP 429"), now=t + 30 * 60)
check("refused for half an hour: no alarm", kinds(PUSHES, "canary") == [], str(PUSHES))
PUSHES.clear()
gw.record_read(gw.GoogleRead(outcome="refused", problem="HTTP 429"), now=t + 61 * 60)
canary = kinds(PUSHES, "canary")
check("refused for an hour: one alarm, saying how long", len(canary) == 1 and "61 minutes" in canary[0]["body"]
      and "HTTP 429" in canary[0]["body"], str(canary))
gw.record_read(gw.GoogleRead(outcome="refused", problem="HTTP 429"), now=t + 66 * 60)
check("...not repeated", len(kinds(PUSHES, "canary")) == 1)
PUSHES.clear()
SITE["status"]["interns"] = 200
run_pass()
canary = kinds(PUSHES, "canary")
check("the list readable again: one all-clear, and the record is cleared",
      len(canary) == 1 and "readable again" in canary[0]["body"] and gw.HEALTH_KEY not in STATE, str(canary))

reset()
run_pass()
SITE["status"]["interns"] = 429
pushes = run_pass()
check("a real refused pass: recorded, no alarm yet", gw.HEALTH_KEY in STATE and kinds(pushes, "canary") == []
      and "refused: HTTP 429" in json.loads(STATE[gw.HEALTH_KEY])["problem"], STATE.get(gw.HEALTH_KEY))

print("\n-- Google changes its page, or drops the internship filter --")
for label, page in (("the job data is gone", INTERNS.replace("AF_initDataCallback", "AF_dataServiceRequests")),
                    ("every U.S. job is served for the internships list", ALL_US),
                    ("the list is empty", _with(INTERNS, None, 0))):
    reset()
    run_pass()
    SITE["interns"] = page
    first = run_pass()
    second = run_pass()
    canary = kinds(second, "canary")
    check(f"{label}: no alarm on the first pass, one on the second", kinds(first, "canary") == [] and len(canary) == 1
          and "can't read any roles" in canary[0]["body"], str(first + second))
    check(f"{label}: no 🔎 pushes for whatever the page held", kinds(first + second, "google") == [])
    check(f"{label}: the ledger is untouched", ledger() == {r[0] for r in REAL})

reset()
run_pass()
SITE["boom"] = True
first, second = run_pass(), run_pass()
SITE["boom"] = False
check("no answer at all from Google: counted, and the board sweep still runs", "boards" in ORDER
      and json.loads(STATE[gw.HEALTH_KEY])["problem"] == "refused: no answer", STATE.get(gw.HEALTH_KEY))

reset()
run_pass()
_orig_parse = gw.parse_results
gw.parse_results = lambda page, interns=True: 1 / 0
first, second = run_pass(), run_pass()
gw.parse_results = _orig_parse
check("a reader that crashes: counted as unreadable, alarmed on the second pass, boards still swept",
      len(kinds(second, "canary")) == 1 and "parser error (ZeroDivisionError)" in kinds(second, "canary")[0]["body"]
      and "boards" in ORDER, str(second))

reset()
run_pass()
serve([new_record(NEW_TITLE, NEW_ID)] + REAL)
_orig_record = gw.record_read
gw.record_read = lambda read, now=None: 1 / 0
pushes = run_pass()
gw.record_read = _orig_record
check("the health record failing to save after a good read: not counted as unreadable, and the push still goes",
      gw.HEALTH_KEY not in STATE and len(kinds(pushes, "google")) == 1, str(pushes))

reset()
run_pass()
_orig_collect = gw.collect
gw.collect = lambda force=False: 1 / 0
first, second = run_pass(), run_pass()
gw.collect = _orig_collect
check("a check that crashes before reading: the alarm still counts it, boards still swept",
      len(kinds(second, "canary")) == 1 and "reader error (ZeroDivisionError)" in kinds(second, "canary")[0]["body"]
      and "boards" in ORDER, str(second))

print("\n-- an alarm ntfy did not take is sent again --")
reset()
run_pass()
SITE["interns"] = ALL_US
NTFY["canary_status"] = 503
run_pass()
run_pass()
check("refused by ntfy: not marked sent", not json.loads(STATE[gw.HEALTH_KEY]).get("alerted"))
NTFY["canary_status"] = 200
check("...sent on the next pass", len(kinds(run_pass(), "canary")) == 1)

print("\n-- a board sweep that crashes still lets the Google shadow watch run --")
reset()
run_pass()
BOARD_LISTINGS.append({"id": "ats:board1", "company": "Acme", "title": "Software Engineer Intern", "location": "Atlanta"})
_orig_process = w.process_job
w.process_job = lambda job: 1 / 0
ran = []
_orig_shadow = gw.shadow_check
gw.shadow_check = lambda now=None: ran.append(1)
try:
    run_pass()
    check("the run fails", False, "w.run() returned")
except ZeroDivisionError:
    check("the run fails", True)
w.process_job, gw.shadow_check = _orig_process, _orig_shadow
check("...but only after the shadow watch ran", ran == [1])

# ═════════════════════════════════════════════════════════════════════════════
print("\n-- weekday hours: Google publishes nothing on weekends --")
FRI_6PM = datetime(2026, 10, 30, 18, 0, tzinfo=ET).timestamp()
MON_9AM = datetime(2026, 11, 2, 9, 0, tzinfo=ET).timestamp()
check("Friday 6 PM to Monday 9 AM is 15 weekday hours", round(gw.weekday_hours(FRI_6PM, MON_9AM), 6) == 15,
      str(gw.weekday_hours(FRI_6PM, MON_9AM)))
check("a Saturday is none", gw.weekday_hours(FRI_6PM + 7 * 3600, FRI_6PM + 30 * 3600) == 0)
WED = datetime(2026, 11, 4, 10, 0, tzinfo=ET).timestamp()
check("Wednesday 10 to 11 AM is one", gw.weekday_hours(WED, WED + 3600) == 1)
check("Saturday to Tuesday across the end of daylight time (Sunday Nov 1, 25 hours long): Monday's 24",
      round(gw.weekday_hours(datetime(2026, 10, 31, tzinfo=ET).timestamp(),
                             datetime(2026, 11, 3, tzinfo=ET).timestamp()), 6) == 24)

print("\n-- the shadow watch: the same reader, where Google publishes every weekday --")
reset()
STATE.pop(gw.SHADOW_CHECKED_KEY, None)
FETCHED.clear()
PUSHES.clear()
gw.shadow_check(now=WED)
s = json.loads(STATE[gw.SHADOW_KEY])
check("first check: the newest 20 U.S. jobs are read and recorded", FETCHED == [gw.ALL_US_URL]
      and len(s["seen"]) == 20 and not s.get("log"), str(FETCHED))
FETCHED.clear()
gw.shadow_check(now=WED + 1800)
check("half an hour later: not read again (hourly)", FETCHED == [])


def all_us_with_new(published_minutes_ago: list[int], at: float, base: int) -> str:
    records = _data(ALL_US)[0]
    new = [new_record(f"Software Engineer {i}", str(base + i), published=datetime.fromtimestamp(at - 60 * m, timezone.utc))
           for i, m in enumerate(published_minutes_ago)]
    return _with(ALL_US, (new + records)[:20], total=1730)


t1 = WED + 3700
SITE["all_us"] = all_us_with_new([12, 40], t1, 800000000000000000)
gw.shadow_check(now=t1)
s = json.loads(STATE[gw.SHADOW_KEY])
check("two new jobs: both seen, with how long after Google published them", sorted(d for _, d in s["log"]) == [12, 40],
      str(s.get("log")))
check("nothing is pushed and nothing reaches the tracker", PUSHES == [] and len(STORE) == len(REAL))
check("the drill reports it", gw.shadow_summary(now=t1) ==
      "Shadow watch: 2 new U.S. Google jobs seen in the last 7 days, typically 26 min after Google published them.",
      gw.shadow_summary(now=t1))

# Quiet from Wednesday 11 AM on: 13 weekday hours that day, 24 each on Thursday and Friday, none over the
# weekend -- 61 by Monday, and 72 at Monday 11 AM.
SITE["all_us"] = all_us_with_new([], t1, 0)
quiet_from = t1
t = t1
fri_night = datetime(2026, 11, 6, 23, 30, tzinfo=ET).timestamp()
while t + 3700 < datetime(2026, 11, 9, 9, 0, tzinfo=ET).timestamp():
    t += 3700
    gw.shadow_check(now=t)
check("Wednesday to Monday morning with nothing new, the weekend included: no alarm",
      kinds(PUSHES, "canary") == [] and t > fri_night, str(kinds(PUSHES, "canary")))
NTFY["canary_status"] = 503
t_alarm = datetime(2026, 11, 9, 11, 5, tzinfo=ET).timestamp()
gw.shadow_check(now=t_alarm)
check("72 weekday hours, but ntfy refuses the alarm: not marked sent",
      kinds(PUSHES, "canary") == [] and not json.loads(STATE[gw.SHADOW_KEY]).get("alerted"))
NTFY["canary_status"] = 200
t_alarm += 3700
gw.shadow_check(now=t_alarm)
canary = kinds(PUSHES, "canary")
check("...the next hour: one alarm", len(canary) == 1 and "72 weekday hours" in canary[0]["body"], str(canary))
gw.shadow_check(now=t_alarm + 3700)
check("an hour later: no repeat", len(kinds(PUSHES, "canary")) == 1)
SITE["all_us"] = all_us_with_new([3], t_alarm + 2 * 3700, 800000000000000100)
gw.shadow_check(now=t_alarm + 2 * 3700)
canary = kinds(PUSHES, "canary")
check("a new job: one all-clear, and the alarm is re-armed", len(canary) == 2 and "showing new roles again"
      in canary[1]["body"] and not json.loads(STATE[gw.SHADOW_KEY]).get("alerted"), str(canary))

reset()
STATE.pop(gw.SHADOW_CHECKED_KEY, None)
SITE["status"]["all_us"] = 403
PUSHES.clear()
gw.shadow_check(now=WED)
check("the all-U.S. list refused: no crash, no alarm, tried again next hour",
      kinds(PUSHES, "canary") == [] and gw.SHADOW_CHECKED_KEY in STATE)
SITE["status"].clear()

reset()
STATE.pop(gw.SHADOW_CHECKED_KEY, None)
STATE_DOWN["keys"] = {gw.SHADOW_KEY}
FETCHED.clear()
gw.shadow_check(now=WED)
STATE_DOWN["keys"] = set()
check("its record unreadable: put off, nothing read, nothing overwritten", FETCHED == [] and gw.SHADOW_KEY not in STATE)

print("\n-- a drill that stops running is noticed --")
reset()
STATE.pop(gw.SHADOW_CHECKED_KEY, None)
PUSHES.clear()
gw.shadow_check(now=WED)
check("no drill on record: the clock starts, no alarm", gw.DRILL_KEY in STATE and kinds(PUSHES, "canary") == [])
STATE[gw.DRILL_KEY] = json.dumps({"passed_at": datetime.fromtimestamp(WED - 9 * 86400, timezone.utc).isoformat()})
gw.shadow_check(now=WED + 3700)
canary = kinds(PUSHES, "canary")
check("9 days since the last pass: one alarm, naming the workflow",
      len(canary) == 1 and "Google drill has not passed since" in canary[0]["body"]
      and "Apple and Google Alert Drill" in canary[0]["body"], str(canary))
gw.shadow_check(now=WED + 7400)
check("...not repeated", len(kinds(PUSHES, "canary")) == 1)

# ═════════════════════════════════════════════════════════════════════════════
print("\n-- the weekly drill --")


def drill_ready():
    """A world where the 5-minute check and the shadow watch have been running."""
    reset(applied=(FDE_TITLE,))
    run_pass()
    STATE[gw.SHADOW_CHECKED_KEY] = _now().isoformat()
    STATE[gw.SHADOW_KEY] = json.dumps({"seen": ["1"], "last_new_at": time.time(), "log": [[round(time.time()), 9]]})


drill_ready()
VERDICT.update(tier="APPLY_CAVEAT", reason="BS/MS program; check the graduation window")
store_before, state_before = copy.deepcopy(STORE), copy.deepcopy(STATE)
for lst in (PUSHES, CLASSIFIED, FETCHED):
    lst.clear()
rc = gw.drill()
google, job = kinds(PUSHES, "google"), kinds(PUSHES, "job")
check("passes", rc == 0)
check("reads the live internships list, and nothing else from Google", FETCHED == [gw.INTERNS_URL], str(FETCHED))
check("runs the classifier once, on the first undergraduate internship on the list", CLASSIFIED == [FDE_TITLE],
      str(CLASSIFIED))
check("sends the 🔎 push, marked DRILL", len(google) == 1 and google[0]["title"] == f"🔎 DRILL - GOOGLE - {FDE_TITLE}"
      and google[0]["tags"] == ["drill"] and google[0]["priority"] == 5, str(google))
d_body = google[0]["body"] if google else ""
check("...saying it is a drill, with the verdict, the shadow watch and the list's health",
      d_body.startswith("Weekly drill") and "Bot's verdict: APPLY with a caveat — BS/MS program" in d_body
      and "Shadow watch: 1 new U.S. Google jobs" in d_body and "Internships list: reading normally." in d_body, d_body)
check("...and what the tracker knows (he applied to LinkedIn's copy)", "You've applied to a role with this exact title"
      in d_body, d_body)
check("sends the usual push too, marked DRILL", job and all(p["title"].startswith("DRILL - ") and p["title"].endswith(
    f"Google - {FDE_TITLE}") for p in job), str([p["title"] for p in job]))
check("writes nothing to the tracker", STORE == store_before)
check("...and in bot_state only its pass stamp", {k: v for k, v in STATE.items() if k != gw.DRILL_KEY}
      == {k: v for k, v in state_before.items() if k != gw.DRILL_KEY}
      and "passed_at" in json.loads(STATE.get(gw.DRILL_KEY, "{}")))

drill_ready()
SITE["seq"] = [429]
PUSHES.clear()
check("one odd answer from Google: retried, and the drill passes", gw.drill() == 0
      and kinds(PUSHES, "canary") == [] and len(kinds(PUSHES, "google")) == 1, str(PUSHES))

drill_ready()
graduate_only = [r for r in REAL if not gw.wanted(r[1])]
serve(graduate_only)
PUSHES.clear()
CLASSIFIED.clear()
rc = gw.drill()
note = kinds(PUSHES, "google")[0]["body"] if kinds(PUSHES, "google") else ""
check("a list with no undergraduate internship on it: the drill still runs, on the first role, and says so",
      rc == 0 and CLASSIFIED == [graduate_only[0][1]] and "No undergraduate internship is on Google's list today" in note,
      str(CLASSIFIED) + note[:200])

for label, setup, step in (
        ("the 5-minute check last ran an hour ago",
         lambda: STATE.update({gw.STATE_KEY: (_now() - timedelta(minutes=61)).isoformat()}),
         "checking the 5-minute Google check (it last ran 61 minutes ago)"),
        ("the 5-minute check never ran", lambda: STATE.pop(gw.STATE_KEY), "it has never run"),
        ("the ledger was never written", lambda: STATE.pop(gw.LEDGER_KEY), "it has not recorded Google's internships yet"),
        ("passes stop before the shadow watch",
         lambda: STATE.update({gw.SHADOW_CHECKED_KEY: (_now() - timedelta(hours=4)).isoformat()}),
         "its passes stop before the shadow-watch check"),
        ("the list has been failing for an hour",
         lambda: STATE.update({gw.HEALTH_KEY: json.dumps({"failing_since": time.time() - 3700, "problem": "refused: HTTP 429"})}),
         "its reads of the internships list have failed since"),
        ("Google refuses the drill three times", lambda: SITE.update(seq=[429, 429, 429]),
         "reading Google's internships list (HTTP 429)"),
        ("ntfy refuses the 🔎 push", lambda: NTFY.update(google_status=500), "sending the 🔎 push"),
        ("ntfy's feed never shows it", lambda: NTFY.update(feed_drop=True), "confirming delivery"),
        ("bot_state will not take the pass stamp", lambda: STATE_DOWN["drop_writes"].add(gw.DRILL_KEY),
         "recording the pass")):
    drill_ready()
    setup()
    PUSHES.clear()
    rc = gw.drill()
    canary = kinds(PUSHES, "canary")
    check(f"{label}: fails, with an alarm naming the step", rc == 1 and len(canary) == 1
          and canary[0]["body"].startswith("Weekly Google drill FAILED at") and step in canary[0]["body"],
          str([c["body"] for c in canary]))

drill_ready()
out = Path(os.environ.get("TMPDIR", "/tmp")) / f"gh_output_{os.getpid()}"
out.write_text("")
os.environ["GITHUB_OUTPUT"] = str(out)
STATE.pop(gw.STATE_KEY)
gw.drill()
check("a failure it reported itself tells the workflow, so its alarm step stays quiet",
      "alerted=true" in out.read_text(), out.read_text())
os.environ.pop("GITHUB_OUTPUT")
out.unlink()

drill_ready()
STATE[gw.DRILL_KEY] = json.dumps({"passed_at": (_now() - timedelta(hours=3)).isoformat()})
os.environ["GITHUB_EVENT_NAME"] = "schedule"
PUSHES.clear()
FETCHED.clear()
check("GitHub's scheduled fallback after the 9 AM pinger's pass: stands down", gw.drill() == 0 and PUSHES == []
      and FETCHED == [])
STATE[gw.DRILL_KEY] = json.dumps({"passed_at": (_now() - timedelta(days=7)).isoformat()})
check("...but runs when the last pass is a week old", gw.drill() == 0 and len(kinds(PUSHES, "google")) == 1)
os.environ.pop("GITHUB_EVENT_NAME")

drill_ready()
STATE_DOWN["keys"] = {gw.STATE_KEY}
PUSHES.clear()
rc = gw.drill()
STATE_DOWN["keys"] = set()
check("bot_state unreadable through every retry: fails, saying so", rc == 1 and "reading the bot's state"
      in kinds(PUSHES, "canary")[0]["body"], str(PUSHES))

# ═════════════════════════════════════════════════════════════════════════════
print("\n-- robots.txt: only the first page of results, ever --")
check("no request in this whole run asked for a later page", ALL_FETCHED and not any("page=" in u for u in ALL_FETCHED),
      str([u for u in ALL_FETCHED if "page=" in u]))
try:
    gw.read_list(gw.INTERNS_URL + "&page=2")
    check("the reader refuses to build one", False)
except AssertionError:
    check("the reader refuses to build one", True)

print()
if _fails:
    print(f"{_fails} Google alert check(s) FAILED")
    sys.exit(1)
print("all Google alert checks passed")
