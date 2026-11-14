"""Workday early stop, the daily full sweep, and batch inserts for pre-filtered rows.

What these protect, in order of cost if they break:
  - a new posting on a Workday board must never be skipped by the early stop
    (a missed job is the failure this bot exists to prevent);
  - a pass must stay well inside the five-minute schedule (the 46 Workday boards
    cost ~1,800 page requests when read in full, Northrop Grumman alone ~5 minutes);
  - a newly added board must not cost one database round trip per posting.

Run: cd scraper && python test_workday_early_stop.py
"""

import json as jsonlib
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import ats_sources

_fails = 0
_ran = 0


def check(label, cond, why=""):
    global _fails, _ran
    _ran += 1
    if cond:
        print(f"  ok    {label}")
    else:
        _fails += 1
        print(f"  FAIL  {label}{f' — {why}' if why else ''}")


def _posted_on(age):
    if age is None:
        return None
    if age == 0:
        return "Posted Today"
    if age == 1:
        return "Posted Yesterday"
    return "Posted 30+ Days Ago" if age >= 30 else f"Posted {age} Days Ago"


def fake_board(pages):
    """A Workday CXS endpoint serving `pages` (lists of posting ages in days).
    Like the real one: total only on the first page, and out-of-range offsets wrap to page 0."""
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        offset = json["offset"]
        calls.append(offset)
        page = offset // 20
        if page >= len(pages):
            page = 0
        body = {
            "jobPostings": [{"title": f"Job {page}-{i}", "externalPath": f"/job/p{page}-{i}",
                             "locationsText": "Atlanta, GA", "postedOn": _posted_on(a)}
                            for i, a in enumerate(pages[page])],
            "total": sum(len(p) for p in pages) if offset == 0 else 0,
        }
        return types.SimpleNamespace(ok=True, status_code=200, json=lambda: body)

    return post, calls


_orig_post = ats_sources.requests.post


def read(pages, **kw):
    post, calls = fake_board(pages)
    ats_sources.requests.post = post
    try:
        jobs = ats_sources.fetch_workday_listings("Acme", "acme:wd1:Careers", **kw)
    finally:
        ats_sources.requests.post = _orig_post
    return jobs, calls


print("\n-- the recent-only read stops at the first page that is entirely old --")

SORTED = [[0] * 20, [1] * 20, [3] * 20, [5] * 20, [9] * 20]
jobs, calls = read(SORTED, full=False)
check("reads through the first all-old page, then stops", calls == [0, 20, 40], f"offsets={calls}")
check("every posting on the pages it read is returned", len(jobs) == 60, f"got {len(jobs)}")

jobs, calls = read(SORTED, full=True)
check("full=True still reads every page", calls == [0, 20, 40, 60, 80], f"offsets={calls}")
check("full=True returns the whole board", len(jobs) == 100, f"got {len(jobs)}")

jobs, calls = read(SORTED)
check("the default is a full read, so other callers (scraper_user_b) are unchanged",
      len(jobs) == 100, f"got {len(jobs)}")

print("\n-- never stop early on a page that might still hold something new --")

jobs, calls = read([[30] + [0] * 19, [3] * 20, [9] * 20], full=False)
check("one pinned old posting on top does not end the read", calls == [0, 20], f"offsets={calls}")

jobs, calls = read([[2] * 10 + [3] * 10, [2] * 20, [5] * 20], full=False)
check("a page with any posting inside the window keeps the read going", calls == [0, 20, 40],
      f"offsets={calls}")

jobs, calls = read([[None] * 20, [None] * 20, [None] * 20], full=False)
check("a tenant that omits postedOn is read in full (age unknown counts as recent)",
      calls == [0, 20, 40], f"offsets={calls}")

jobs, calls = read([[5] * 19 + [None], [0] * 20], full=False)
check("one unreadable age on an otherwise old page keeps the read going", calls == [0, 20],
      f"offsets={calls}")

print("\n-- the full read reports whether the board is sorted newest first --")

report = {}
read(SORTED, full=True, sort_report=report)
check("a newest-first board is reported sorted", report.get("acme:wd1:Careers") is True, str(report))

report = {}
read([[4] * 20, [18] * 20, [1] * 20], full=True, sort_report=report)
check("a board whose later page is newer is reported unsorted (Cisco's shape)",
      report.get("acme:wd1:Careers") is False, str(report))

report = {}
read([[0] * 7], full=True, sort_report=report)
check("one page is too little to judge", report.get("acme:wd1:Careers") is None, str(report))

report = {}
read(SORTED, full=False, sort_report=report)
check("a recent-only read reports nothing (it cannot see the whole board)", report == {}, str(report))

check("ties and small reorderings are not 'unsorted'",
      ats_sources.workday_sorted_by_date([1, 2, 0, 3]) is True)
check("Adobe's measured shape is unsorted", ats_sources.workday_sorted_by_date([27.5, 12, 30]) is False)

print("\n-- fetch_all_listings routes the mode to Workday boards only --")

seen = {}
_orig_fcl = ats_sources.fetch_company_listings


def spy(company, platform, token, **kw):
    seen[company] = kw
    return []


ats_sources.fetch_company_listings = spy
COMPANIES = {
    "Sorted": {"platform": "workday", "token": "s:wd1:x"},
    "Unsorted": {"platform": "workday", "token": "u:wd1:x"},
    "Green": {"platform": "greenhouse", "token": "green"},
}
rep = {}
ats_sources.fetch_all_listings(COMPANIES, full=False, always_full=frozenset({"u:wd1:x"}))
check("a sorted Workday board gets the recent-only read", seen["Sorted"].get("full") is False, str(seen))
check("a board in always_full is read in full even on a recent-only pass",
      seen["Unsorted"].get("full") is True, str(seen))
check("non-Workday boards get no paging options", seen["Green"] == {}, str(seen))
seen.clear()
ats_sources.fetch_all_listings(COMPANIES, full=True, sort_report=rep)
check("a full pass reads every Workday board in full",
      seen["Sorted"].get("full") is True and seen["Unsorted"].get("full") is True, str(seen))
check("the sort report is handed to the Workday reads", seen["Sorted"].get("sort_report") is rep)
ats_sources.fetch_company_listings = _orig_fcl

print("\n-- insert_jobs: one request per batch, row by row only when a batch fails --")

import db

requests_sent = []


class _Table:
    def __init__(self, fail_sizes):
        self.fail_sizes = fail_sizes

    def upsert(self, payload, on_conflict=None, ignore_duplicates=False):
        rows = payload if isinstance(payload, list) else [payload]
        requests_sent.append((len(rows), on_conflict, ignore_duplicates))
        fail = isinstance(payload, list) and len(rows) in self.fail_sizes
        return types.SimpleNamespace(execute=lambda: (_ for _ in ()).throw(RuntimeError("bad row"))
                                     if fail else types.SimpleNamespace(data=[]))


_orig_client = db.get_client


def use_client(fail_sizes=()):
    table = _Table(set(fail_sizes))
    db.get_client = lambda: types.SimpleNamespace(table=lambda name: table)


ROWS = [{"id": f"ats:{i}", "title": "Senior Engineer", "company": "Acme", "tier": "INELIGIBLE"}
        for i in range(250)]

use_client()
stored = db.insert_jobs(ROWS)
check("250 rows go up in 3 requests", [n for n, _, _ in requests_sent] == [100, 100, 50],
      str(requests_sent))
check("same conflict rule as insert_job (ON CONFLICT (id) DO NOTHING)",
      all(oc == "id" and ign is True for _, oc, ign in requests_sent), str(requests_sent))
check("all 250 reported stored", stored == 250, f"stored={stored}")

requests_sent.clear()
use_client(fail_sizes={50})
stored = db.insert_jobs(ROWS)
singles = [n for n, _, _ in requests_sent[3:]]
check("a failed batch is retried row by row", len(singles) == 50 and set(singles) == {1},
      f"{len(singles)} follow-up requests")
check("rows from the retried batch still count as stored", stored == 250, f"stored={stored}")

requests_sent.clear()
check("nothing to store, nothing sent", db.insert_jobs([]) == 0 and requests_sent == [])

payload = db._job_payload({"id": "ats:x", "title": "Software Engineer Intern", "company": "Acme",
                           "url": "https://acme.example/1"})
check("the batch payload is insert_job's payload (one builder, no drift)",
      {"id", "norm_key", "target_key", "tier", "reason"} <= set(payload), str(sorted(payload)))
db.get_client = _orig_client

print("\n-- the daily full sweep: when it runs and what it remembers --")

import ats_watch as w

STATE = {}
STATE_DOWN = {"on": False}


def fake_read_state(key):
    if STATE_DOWN["on"]:
        raise db.StateUnavailable(key)
    return STATE.get(key)


_w_orig = {k: getattr(w, k) for k in ("read_state", "set_state", "start_run", "finish_run", "insert_jobs",
                                     "insert_job", "process_job", "fetch_all_listings",
                                     "find_unknown_candidates")}
w.read_state = fake_read_state
w.set_state = lambda key, value: STATE.__setitem__(key, value)

at = lambda h: datetime(2026, 11, 15, h, 30, tzinfo=timezone.utc)
check("before 08:00 UTC: no full sweep, and no state read", w._full_sweep_due(at(7)) is False)
check("first pass at/after 08:00 with no sweep today: full", w._full_sweep_due(at(8)) is True)
STATE[w._FULL_SWEEP_DAY_KEY] = "2026-11-15"
check("already swept today: recent pages only", w._full_sweep_due(at(14)) is False)
STATE[w._FULL_SWEEP_DAY_KEY] = "2026-11-14"
check("yesterday's sweep does not count for today", w._full_sweep_due(at(9)) is True)
STATE_DOWN["on"] = True
check("unreadable state never starts a full sweep", w._full_sweep_due(at(9)) is False)
STATE_DOWN["on"] = False

STATE.clear()
STATE[w._UNSORTED_KEY] = jsonlib.dumps(["kept:wd1:x", "fixed:wd1:x"])
w._record_full_sweep(at(8), {"new:wd1:x": False, "ok:wd1:x": True, "kept:wd1:x": None, "fixed:wd1:x": True})
check("today is recorded as swept", STATE[w._FULL_SWEEP_DAY_KEY] == "2026-11-15", str(STATE))
check("unsorted list: newly found added, unknown keeps its old verdict, re-sorted board dropped",
      jsonlib.loads(STATE[w._UNSORTED_KEY]) == ["kept:wd1:x", "new:wd1:x"], STATE[w._UNSORTED_KEY])

always = w._always_full_tokens()
built_in = {w.ATS_COMPANIES[n]["token"] for n in ats_sources.WORKDAY_UNSORTED_COMPANIES}
check("always-full = the built-in four plus the stored list",
      built_in <= always and {"kept:wd1:x", "new:wd1:x"} <= always, str(sorted(always)))
check("the built-in four are all real Workday boards in the config",
      len(built_in) == 4 and all(w.ATS_COMPANIES[n]["platform"] == "workday"
                                 for n in ats_sources.WORKDAY_UNSORTED_COMPANIES))
STATE[w._UNSORTED_KEY] = "not json"
check("a garbled stored list falls back to the built-in four", w._always_full_tokens() == frozenset(built_in))

print("\n-- _sweep: pre-filtered rows in one batch, before any classifying; newest internships first --")

events = []
w.insert_jobs = lambda jobs: (events.append(("batch", [j["id"] for j in jobs])), len(jobs))[1]
w.insert_job = lambda job: (events.append(("single", job["id"])), True)[1]
w.process_job = lambda job: (events.append(("classify", job["id"])), False)[1]
w.find_unknown_candidates = lambda jobs: {j["id"] for j in jobs}

LISTINGS = [
    {"id": "ats:ft1", "company": "Acme", "title": "Senior Software Engineer", "location": "Atlanta"},
    {"id": "ats:old", "company": "Acme", "title": "Software Engineer Intern", "location": "Atlanta",
     "posted_at": "2026-10-01T00:00:00+00:00"},
    {"id": "ats:ft2", "company": "Acme", "title": "Accountant", "location": "Atlanta"},
    {"id": "ats:new", "company": "Acme", "title": "Data Science Intern", "location": "Atlanta",
     "posted_at": "2026-11-15T00:00:00+00:00"},
]
new, notified = w._sweep([dict(j) for j in LISTINGS], set(), set())
batches = [e for e in events if e[0] == "batch"]
check("pre-filtered rows go up in ONE batch call", len(batches) == 1 and set(batches[0][1]) == {"ats:ft1", "ats:ft2"},
      str(events))
check("no pre-filtered row is inserted one at a time", not [e for e in events if e[0] == "single"], str(events))
check("the batch is stored before anything is classified", events[0][0] == "batch", str(events))
check("internships are classified newest first",
      [e[1] for e in events if e[0] == "classify"] == ["ats:new", "ats:old"], str(events))
check("only the internships count as new", new == 2, f"new={new}")

print("\n-- run(): the mode reaches the board read, and only a full read is recorded --")

calls = []


def fake_fetch(cfg, **kw):
    calls.append(kw)
    if kw.get("sort_report") is not None:
        kw["sort_report"]["acme:wd1:Careers"] = True
    return []


w.fetch_all_listings = fake_fetch
w.start_run = lambda source="ats": 1
w.finish_run = lambda run_id, **kw: None
_orig_sitemaps = w.career_sitemaps.collect_listings
w.career_sitemaps.collect_listings = lambda force=False, apple_sitemap=None: []
_orig_apple = (w.career_sitemaps.collect_apple_students, w.apple_watch.seen_reqs, w.apple_watch.shadow_check)
w.career_sitemaps.collect_apple_students = lambda force=False, full=False, seen_reqs=None: w.career_sitemaps.AppleStudents()
w.apple_watch.seen_reqs = lambda: set()
w.apple_watch.shadow_check = lambda now=None: None
_orig_google = (w.google_watch.check, w.google_watch.shadow_check)
w.google_watch.check = lambda sweep: (0, 0, 0)
w.google_watch.shadow_check = lambda now=None: None
_orig_due = w._full_sweep_due

STATE.clear()
w._full_sweep_due = lambda now: True
w.run()
check("a due pass reads every board in full", calls[-1].get("full") is True, str(calls[-1]))
check("…with the sort report attached", isinstance(calls[-1].get("sort_report"), dict), str(calls[-1]))
check("…and is recorded, so the next pass is a recent-only one", w._FULL_SWEEP_DAY_KEY in STATE, str(STATE))

STATE.clear()
w._full_sweep_due = lambda now: False
w.run()
check("a normal pass reads recent pages", calls[-1].get("full") is False, str(calls[-1]))
check("…reads the unsorted boards in full", built_in <= set(calls[-1].get("always_full", ())), str(calls[-1]))
check("…and records nothing", w._FULL_SWEEP_DAY_KEY not in STATE, str(STATE))

w._full_sweep_due = _orig_due
for k, v in _w_orig.items():
    setattr(w, k, v)
w.career_sitemaps.collect_listings = _orig_sitemaps
w.career_sitemaps.collect_apple_students, w.apple_watch.seen_reqs, w.apple_watch.shadow_check = _orig_apple
w.google_watch.check, w.google_watch.shadow_check = _orig_google

print(f"\n{_ran - _fails} passed, {_fails} failed")
sys.exit(1 if _fails else 0)
