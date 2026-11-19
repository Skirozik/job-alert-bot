#!/usr/bin/env python3
"""The Workday relay (workday_relay.py) and the watcher's side of it. Offline: no
network, no database. The boards are faked page by page.

    python test_workday_relay.py
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ats_sources
import ats_watch as w
import workday_relay as r

_fails = 0
_ran = 0


def check(label, cond, why=""):
    global _fails, _ran
    _ran += 1
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"   {why}" if not cond else ""))
    if not cond:
        _fails += 1


def posting(company, n, title="Software Engineer Intern", posted="2026-11-19T00:00:00+00:00"):
    job = ats_sources._make_job(company, title, "Atlanta, GA",
                                f"https://{company.lower()}.wd1.myworkdayjobs.com/Careers/job/Atlanta/{title.replace(' ', '-')}_JR{n}",
                                None, posted)
    return job


print("-- the boards --")
B = r.boards()
check("every Workday board in the config, and nothing else",
      B and all(w.ATS_COMPANIES[c]["platform"] == "workday" for c in B)
      and len(B) == sum(1 for c in w.ATS_COMPANIES.values() if c["platform"] == "workday"), str(len(B)))
check("Nvidia is one of them", "Nvidia" in B)

print("\n-- read_board: a short read stops at the first page the database already knows --")
PAGES = {"Acme": [[posting("Acme", 1), posting("Acme", 2, "Staff Engineer")],
                  [posting("Acme", 3, "Data Intern"), posting("Acme", 4, "Analyst")],
                  [posting("Acme", 5), posting("Acme", 6)]]}
STORED = {posting("Acme", 3, "Data Intern")["id"], posting("Acme", 4, "Analyst")["id"],
          posting("Acme", 5)["id"], posting("Acme", 6)["id"]}
asked = []
pages_read = []


def fake_pages(company, token):
    for page in PAGES.get(company, []):
        pages_read.append(company)
        yield [dict(j) for j in page], [0] * len(page)


def fake_unknown(jobs):
    asked.append([j["id"] for j in jobs])
    return {j["id"] for j in jobs if j["id"] not in STORED}


r.iter_workday_pages = fake_pages
r.find_unknown_candidates = fake_unknown
rows, read, pages = r.read_board("Acme", "acme:wd1:Careers", full=False)
check("reads page 1 (something new) and page 2 (nothing new), then stops", pages == 2 and pages_read == ["Acme", "Acme"],
      f"pages={pages} {pages_read}")
check("asks the dedup question once per page, with that page's ids", len(asked) == 2 and len(asked[0]) == 2, str(asked))
check("returns only the postings not yet stored", sorted(j["title"] for j in rows) == ["Software Engineer Intern", "Staff Engineer"],
      str([j["title"] for j in rows]))
check("counts the postings it read", read == 4, str(read))
check("every returned row carries a norm_key for the dedup call", all(j.get("norm_key") for j in rows))

asked.clear()
pages_read.clear()
r.SHORT_MAX_PAGES, _cap = 1, r.SHORT_MAX_PAGES
rows, read, pages = r.read_board("Acme", "acme:wd1:Careers", full=False)
check("a short read gives up at SHORT_MAX_PAGES even with new postings on every page", pages == 1, str(pages))
r.SHORT_MAX_PAGES = _cap

print("\n-- read_board: a full read takes every page and asks once --")
asked.clear()
full_calls = []


def fake_full(company, token, full=True, sort_report=None):
    full_calls.append((company, full))
    if sort_report is not None:
        sort_report[token] = False
    return [dict(j) for page in PAGES[company] for j in page]


r.fetch_workday_listings = fake_full
report = {}
rows, read, pages = r.read_board("Acme", "acme:wd1:Careers", full=True, sort_report=report)
check("the whole board is read through fetch_workday_listings(full=True)", full_calls == [("Acme", True)], str(full_calls))
check("one dedup question for the whole board", len(asked) == 1 and len(asked[0]) == 6, str(asked))
check("the same two unknown rows come back (pages estimated at 20 a page)", len(rows) == 2 and read == 6 and pages == 1,
      f"{len(rows)} {read} {pages}")
check("the sort report reaches the reader", report == {"acme:wd1:Careers": False}, str(report))

print("\n-- collect: every board, duplicates within the batch dropped, failures counted --")
_boards = r.boards
r.boards = lambda: {"Acme": "acme:wd1:Careers", "Beta": "beta:wd1:Careers", "Down": "down:wd1:Careers"}
twin = posting("Beta", 9, "Software Engineering Intern")       # same norm_key as Acme's? no: other company
same_target = dict(posting("Beta", 9, "Software Engineering Intern (Summer 2027)"))  # same req JR9, other title
PAGES["Beta"] = [[twin, same_target, posting("Beta", 10, "Data Intern")], [posting("Beta", 11, "Data Intern")]]
STORED.add(posting("Beta", 11, "Data Intern")["id"])
asked.clear()
got = r.collect(full=False)
titles = sorted(j["title"] for j in got["jobs"])
check("Acme's two and Beta's two come back; Beta's same-requisition twin is dropped",
      titles == ["Data Intern", "Software Engineer Intern", "Software Engineering Intern", "Staff Engineer"], str(titles))
check("a board that answered nothing is counted as failed, not as empty", got["failed"] == 1 and got["answered"] == 2,
      str({k: got[k] for k in ("failed", "answered")}))
check("listed counts every posting read", got["listed"] == 4 + 4, str(got["listed"]))

full_calls.clear()
got = r.collect(full=False, full_tokens=frozenset({"beta:wd1:Careers"}))
check("a token in full_tokens is read in full on a short pass", full_calls == [("Beta", True)], str(full_calls))
check("…and its sort verdict is collected", got["sort_report"] == {"beta:wd1:Careers": False}, str(got["sort_report"]))

print("\n-- split: the watcher's own title gate, same reasons --")
ineligible, to_classify = r.split([posting("Acme", 1), posting("Acme", 2, "Staff Engineer"),
                                   posting("Acme", 7, "Software Engineer, New Grad"), posting("Acme", 8, "Software Engineer")])
check("the internship goes to the model", [j["title"] for j in to_classify] == ["Software Engineer Intern"])
check("the other three are ruled out with the watcher's reasons",
      [j["reason"] for j in ineligible] == [w.title_gate("Staff Engineer"), w.title_gate("Software Engineer, New Grad"),
                                            w.title_gate("Software Engineer")]
      and all(j["tier"] == "INELIGIBLE" and j["suggested_resume"] == "General" for j in ineligible),
      str([(j["title"], j["reason"]) for j in ineligible]))
check("the gate itself names the three reasons",
      w.title_gate("Staff Engineer") == "Pre-filtered: seniority keyword in title"
      and w.title_gate("Software Engineer, New Grad") == "Pre-filtered: new grad / full-time role, not an internship"
      and w.title_gate("Software Engineer") == "Pre-filtered: no internship marker in title"
      and w.title_gate("Software Engineer Intern") is None)

print("\n-- describe: descriptions for the model's rows, newest first, capped --")
r.DETAIL_PAUSE_S = 0
fetched = []


def fake_fetch(url):
    fetched.append(url)
    if "JR13" in url:
        raise RuntimeError("HTTP 429")
    return "Build things.\nPython"


jobs = [posting("Acme", 12, posted="2026-11-18T00:00:00+00:00"), posting("Acme", 13, posted="2026-11-19T00:00:00+00:00"),
        posting("Acme", 14, "Engineering Co-op (Spring 2027)", posted="2026-11-17T00:00:00+00:00")]
out = r.describe(jobs, fetch=fake_fetch)
check("newest first", [j["url"][-4:] for j in out] == ["JR13", "JR12", "JR14"], str([j["url"][-4:] for j in out]))
check("a description is read for each internship", out[1].get("description") == "Build things.\nPython")
check("a refused detail read still relays the posting, title only", out[0].get("description") is None and "JR13" in fetched[0])
check("a pure co-op is relayed without a detail read", out[2].get("description") is None and not any("JR14" in u for u in fetched))

r.MAX_DESCRIPTIONS, _max = 1, r.MAX_DESCRIPTIONS
out = r.describe(jobs, fetch=fake_fetch)
check("past the per-run cap, a posting waits for the next run", len(out) == 1 and "JR13" in out[0]["url"], str(len(out)))
r.MAX_DESCRIPTIONS = _max

print("\n-- run(): what gets read and what gets written --")
state = {}
written = []
inserted = []
r.read_state = lambda key: state.get(key)
r.set_state = lambda key, value: written.append((key, value)) or state.__setitem__(key, value)
r.insert_jobs = lambda jobs: inserted.extend(jobs) or len(jobs)
r._always_full_tokens = lambda: frozenset({"beta:wd1:Careers"})
r._record_unsorted = lambda report: written.append(("unsorted", json.dumps(report)))
r._fetch_workday = fake_fetch
r.describe.__defaults__ = (fake_fetch,)
# Today's full read is already done, so this run is the ordinary short pass.
state["workday_relay_full_day"] = datetime.now(timezone.utc).date().isoformat()

full_calls.clear()
payload = r.run()
kinds = [k for k, _ in written]
check("first run: short read, and the unsorted boards in full (never read before)",
      full_calls == [("Beta", True)] and "workday_relay_unsorted_at" in kinds, f"{full_calls} {kinds}")
check("the ineligible titles are stored directly, as INELIGIBLE",
      sorted(j["title"] for j in inserted) == ["Staff Engineer"] and inserted[0]["tier"] == "INELIGIBLE", str(inserted))
check("…and are not in the payload", all(j["title"] != "Staff Engineer" for j in payload["jobs"]))
check("the payload holds the model's rows, with descriptions", len(payload["jobs"]) == 3
      and all(j.get("description") for j in payload["jobs"]), str([(j["title"], bool(j.get("description"))) for j in payload["jobs"]]))
check("no norm_key is relayed or inserted (both sides compute their own)",
      all("norm_key" not in j for j in payload["jobs"] + inserted))
check("the payload is written under workday_relay with the counts",
      "workday_relay" in kinds and payload["listed"] == 8 and payload["boards"] == 2 and payload["failed"] == 1
      and payload["unseen"] == 4 and datetime.fromisoformat(payload["at"]).tzinfo is not None, str(payload))
check("no full-day stamp on a short pass", "workday_relay_full_day" not in kinds, str(kinds))
check("the unsorted verdicts are recorded", ("unsorted", json.dumps({"beta:wd1:Careers": False})) in written)

written.clear()
full_calls.clear()
payload = r.run()
check("within the half hour the unsorted boards are left alone", full_calls == [], str(full_calls))

written.clear()
inserted.clear()
payload = r.run(force_full=True)
check("--full reads every board in full", sorted(c for c, _ in full_calls) == ["Acme", "Beta", "Down"], str(full_calls))
check("…and stamps the day", any(k == "workday_relay_full_day" for k in [k for k, _ in written]))

state["workday_relay_full_day"] = datetime.now(timezone.utc).date().isoformat()
state["workday_relay_unsorted_at"] = datetime.now(timezone.utc).isoformat()
check("full_due: not again today", r.full_due(datetime.now(timezone.utc).replace(hour=9)) is False)
state["workday_relay_full_day"] = "2000-01-01"
check("full_due: yes after FULL_HOUR_UTC on a new day", r.full_due(datetime.now(timezone.utc).replace(hour=9)) is True)
check("full_due: never before FULL_HOUR_UTC", r.full_due(datetime.now(timezone.utc).replace(hour=7)) is False)
check("unsorted_due after the interval", r.unsorted_due(datetime.now(timezone.utc) + timedelta(minutes=31)) is True)

written.clear()
inserted.clear()
payload = r.run(dry_run=True)
check("a dry run writes nothing", written == [] and inserted == [], str(written))

PAGES_ALL, PAGES = PAGES, {}
written.clear()
try:
    r.run()
    check("every board answering nothing is an error", False)
except RuntimeError as exc:
    check("every board answering nothing is an error, and the old payload stays", written == [] and "no Workday board" in str(exc))
PAGES = PAGES_ALL


print("\n-- iter_workday_pages: one retry on HTTP 429, then give up --")
import types as _types
_posts = []
_slept = []
_orig_post, _orig_sleep = ats_sources.requests.post, ats_sources.time.sleep
STATUS = [429, 200, 429, 429]


def _fake_post(url, json=None, headers=None, timeout=None):
    _posts.append(json["offset"])
    code = STATUS[len(_posts) - 1] if len(_posts) <= len(STATUS) else 200
    page = [{"title": f"Job {json['offset'] + i}", "externalPath": f"/job/p{json['offset'] + i}",
             "locationsText": "Atlanta, GA", "postedOn": "Posted Today"} for i in range(20)]
    return _types.SimpleNamespace(ok=code == 200, status_code=code,
                                  json=lambda: {"jobPostings": page if code == 200 else [], "total": 100})


ats_sources.requests.post = _fake_post
ats_sources.time.sleep = lambda s: _slept.append(s)
got = list(ats_sources.iter_workday_pages("Acme", "acme:wd1:Careers"))
ats_sources.requests.post, ats_sources.time.sleep = _orig_post, _orig_sleep
check("a 429 is retried once after a pause, and the page comes back", _posts[:2] == [0, 0] and len(got) >= 1
      and _slept[:1] == [ats_sources._WORKDAY_RETRY_PAUSE_S], f"posts={_posts} slept={_slept} pages={len(got)}")
check("two 429s in a row end the read", _posts == [0, 0, 20, 20] and len(got) == 1, f"posts={_posts} pages={len(got)}")

print("\n-- the watcher's side --")
relay = {}
w.read_state = lambda key: relay.get(key)
now = datetime.now(timezone.utc)
rows, age = w._workday_relay_rows(now)
check("no relay yet: nothing to do, and no age", rows == [] and age is None)
good = posting("Nvidia", 77)
not_workday = posting("Airbnb", 78)
relay[w.WORKDAY_RELAY_KEY] = json.dumps({"at": (now - timedelta(minutes=4)).isoformat(), "listed": 9, "unseen": 2,
                                          "jobs": [good, not_workday, {"id": "x"}, "junk"]})
rows, age = w._workday_relay_rows(now)
check("takes only well-formed rows for Workday companies", [x["id"] for x in rows] == [good["id"]], str(rows))
check("reports how old the relay is", 3.9 < age < 4.1, str(age))
check("the relay key matches on both sides", w.WORKDAY_RELAY_KEY == r.RELAY_KEY)

print("\n-- run(): a fresh relay takes the Workday boards off the runner --")
swept = []
boards_seen = []
_orig = {k: getattr(w, k) for k in ("start_run", "finish_run", "fetch_all_listings", "_sweep",
                                    "_full_sweep_due", "_always_full_tokens")}
_orig_mods = (w.career_sitemaps.collect_listings, w.career_sitemaps.collect_apple_students,
              w.apple_watch.seen_reqs, w.apple_watch.shadow_check, w.google_watch.check,
              w.google_watch.shadow_check)
w.start_run = lambda source="ats": 1
w.finish_run = lambda run_id, **kw: None
w.fetch_all_listings = lambda cfg, **kw: boards_seen.append(sorted(cfg)) or []
w._full_sweep_due = lambda now: False
w._always_full_tokens = lambda: frozenset()
w._sweep = lambda rows, ids, nks, tks=None: (swept.append([x["id"] for x in rows]), (len(rows), 0))[1]
w.career_sitemaps.collect_listings = lambda force=False, apple_sitemap=None: []
w.career_sitemaps.collect_apple_students = lambda force=False, full=False, seen_reqs=None: w.career_sitemaps.AppleStudents()
w.apple_watch.seen_reqs = lambda: set()
w.apple_watch.shadow_check = lambda now=None: None
w.google_watch.check = lambda sweep: (0, 0, 0)
w.google_watch.shadow_check = lambda now=None: None

w.run()
check("the relayed posting reaches the sweep", [good["id"]] in swept, str(swept))
check("the runner reads no Workday board while the relay is fresh",
      boards_seen and not any(w.ATS_COMPANIES[c]["platform"] == "workday" for c in boards_seen[-1])
      and len(boards_seen[-1]) == len(w.ATS_COMPANIES) - len(B), str(len(boards_seen[-1]) if boards_seen else None))

relay[w.WORKDAY_RELAY_KEY] = json.dumps({"at": (now - timedelta(hours=2)).isoformat(), "listed": 9, "jobs": [good]})
swept.clear()
w.run()
check("a stale relay still hands over its rows", [good["id"]] in swept, str(swept))
check("…but the runner reads the Workday boards itself again", len(boards_seen[-1]) == len(w.ATS_COMPANIES),
      str(len(boards_seen[-1])))

relay.clear()
w.run()
check("no relay at all: the runner reads every board", len(boards_seen[-1]) == len(w.ATS_COMPANIES))

for k, v in _orig.items():
    setattr(w, k, v)
(w.career_sitemaps.collect_listings, w.career_sitemaps.collect_apple_students, w.apple_watch.seen_reqs,
 w.apple_watch.shadow_check, w.google_watch.check, w.google_watch.shadow_check) = _orig_mods

print(f"\n{_ran - _fails} passed, {_fails} failed")
sys.exit(1 if _fails else 0)
