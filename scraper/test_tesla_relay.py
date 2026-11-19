#!/usr/bin/env python3
"""The Tesla relay (tesla_relay.py) and the watcher's side of it. Offline: no
network, no database. The list below has the same shape as Tesla's real one
(www.tesla.com/cua-api/apps/careers/state, read 2026-11-16), cut down.

    python test_tesla_relay.py
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ats_watch as w
import tesla_relay as t

_fails = 0
_ran = 0


def check(label, cond, why=""):
    global _fails, _ran
    _ran += 1
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"   {why}" if not cond else ""))
    if not cond:
        _fails += 1


STATE = {
    "lookup": {"types": {"1": "fulltime", "2": "parttime", "3": "intern", "4": "seasonal"}},
    "geo": [
        {"id": "5", "sites": [{"id": "US", "states": [
            {"id": "CA", "name": "California", "cities": {"Palo Alto": ["401022"], "Fremont": ["15047"]}},
            {"id": "TX", "name": "Texas", "cities": {"Austin": ["412257"]}},
        ]}, {"id": "CA", "states": [{"id": "ON", "cities": {"Toronto": ["999"]}}]}]},
        {"id": "3", "sites": [{"id": "DE", "cities": {"Berlin": ["777"]}}]},
    ],
    "listings": [
        {"id": "281473", "t": "Internship, Software Engineer, Information Security (Winter/Spring 2027)",
         "dp": "8", "l": "401022", "y": 3},
        {"id": "288425", "t": "Internship, Service Technician Trainee (Fall 2026)", "dp": "1", "l": "412257", "y": 3},
        {"id": "290001", "t": "Software Engineer Intern, Vehicle Software", "dp": "6", "l": "15047", "y": 1},
        {"id": "224501", "t": "SI Engineer, Manipulation, Optimus", "dp": "3", "l": "401022", "y": 1},
        {"id": "290002", "t": "Internship, Software Engineer (Winter/Spring 2027)", "dp": "8", "l": "999", "y": 3},
        {"id": "290003", "t": "Internship, Data Engineer (Winter/Spring 2027)", "dp": "8", "l": "777", "y": 3},
        {"id": "290004", "t": "Internship, Data Engineer (Winter/Spring 2027)", "dp": "8", "l": "123456", "y": 3},
        {"id": "290005", "t": "Manufacturing Engineering Co-op (Fall 2026)", "dp": "4", "l": "412257", "y": 3},
    ],
}

print("-- reading the list --")
locs = t.us_locations(STATE)
check("U.S. locations map to 'City, ST'", locs == {"401022": "Palo Alto, CA", "15047": "Fremont, CA",
                                                  "412257": "Austin, TX"}, str(locs))
jobs = t.internships(STATE)
by_url = {j["url"]: j for j in jobs}
check("keeps U.S. internships: typed intern, or 'Intern' in a full-time-typed title",
      set(by_url) == {t.PUBLIC_URL.format(id=i) for i in ("281473", "288425", "290001", "290005")}, str(sorted(by_url)))
check("drops full-time, Canada, Germany and unmapped locations", len(jobs) == 4)
sec = by_url[t.PUBLIC_URL.format(id="281473")]
check("same 'ats:' id scheme as every board (so the watcher takes the ATS path)",
      sec["id"].startswith("ats:") and len(sec["id"]) == 20, sec["id"])
check("company, location, apply link and source are set",
      sec["company"] == "Tesla" and sec["location"] == "Palo Alto, CA"
      and sec["apply_url"] == sec["url"] and sec["search_term"] == "tesla-relay", str(sec))
check("Tesla's numeric id comes back off the URL", t._tesla_id(sec) == "281473")

check("description HTML becomes text",
      t._text("<p>Build&nbsp;tools</p><ul><li>Python</li><li>SQL &amp; Spark</li></ul>", None, "<div>Pay<br>$50/hr</div>")
      == "Build tools\nPython\nSQL & Spark\nPay\n$50/hr",
      repr(t._text("<p>Build&nbsp;tools</p><ul><li>Python</li><li>SQL &amp; Spark</li></ul>", None, "<div>Pay<br>$50/hr</div>")))

print("\n-- collect(): only unseen postings, descriptions only where a model will read them --")
reads = []
DETAIL = {"jobDescription": "<p>Secure pipelines.</p>", "jobRequirements": "<ul><li>Python</li></ul>",
          "location": "BELLEVUE, Washington", "state": "WA"}


def fake_get(url):
    reads.append(url)
    if url.endswith("/290001"):
        raise RuntimeError("Tesla refused the read (safari: HTTP 429)")
    return DETAIL


t._get = fake_get
seen_url = t.PUBLIC_URL.format(id="288425")
t.find_unknown_candidates = lambda js: {j["id"] for j in js if j["url"] != seen_url}
t.time.sleep = lambda s: None
out, listed, unseen = t.collect(STATE)
out_by_url = {j["url"]: j for j in out}
check("listed counts every U.S. internship", listed == 4, str(listed))
check("unseen counts the ones not stored yet", unseen == 3, str(unseen))
check("an already-stored posting is not relayed", seen_url not in out_by_url, str(sorted(out_by_url)))
sec = out_by_url.get(t.PUBLIC_URL.format(id="281473"), {})
check("a new internship carries its description", sec.get("description") == "Secure pipelines.\nPython",
      repr(sec.get("description")))
check("…and the detail's own city, out of capitals", sec.get("location") == "Bellevue, WA", sec.get("location"))
check("a refused detail read still relays the posting, title only",
      t.PUBLIC_URL.format(id="290001") in out_by_url
      and out_by_url[t.PUBLIC_URL.format(id="290001")]["description"] is None)
check("no norm_key is relayed (the watcher computes its own)", all("norm_key" not in j for j in out))

reads.clear()
t.find_unknown_candidates = lambda js: {j["id"] for j in js}
out, _, _ = t.collect(STATE)
coop = {j["url"]: j for j in out}[t.PUBLIC_URL.format(id="290005")]
check("a title the watcher pre-filters (a fall co-op) is relayed without a detail read",
      not any(u.endswith("/290005") for u in reads) and coop["description"] is None, str(reads))

reads.clear()
_cap = t.MAX_DESCRIPTIONS
t.MAX_DESCRIPTIONS = 1
out, _, _ = t.collect(STATE)
check("past the per-run cap, a posting waits for the next run rather than going title-only",
      len(reads) == 1 and len([j for j in out if t._would_be_classified(j["title"])]) == 1, str(reads))
t.MAX_DESCRIPTIONS = _cap

print("\n-- run(): what gets written --")
written = {}
t.set_state = lambda key, value: written.__setitem__(key, value)
t._get = lambda url: STATE if url == t.STATE_URL else DETAIL
payload = t.run()
stored = json.loads(written.get(t.RELAY_KEY, "{}"))
check("writes the payload under tesla_relay", stored.get("listed") == 4 and stored.get("unseen") == 4
      and len(stored.get("jobs", [])) == 4,
      str(stored)[:200])
check("…stamped with the time it read Tesla", datetime.fromisoformat(stored["at"]).tzinfo is not None)

written.clear()
t.run(dry_run=True)
check("a dry run writes nothing", not written, str(written))

t._get = lambda url: {"geo": STATE["geo"], "listings": STATE["listings"][3:4]}
try:
    t.run()
    raised = False
except RuntimeError:
    raised = True
check("a list with no U.S. internships at all is an error, and keeps the old payload",
      raised and not written, str(written))

print("\n-- the watcher's side --")
now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
relay = {}
w.read_state = lambda key: relay.get(key)
check("no relay yet: nothing to do", w._tesla_relay_rows(now) == [])

good = dict(payload["jobs"][0])
relay[w.TESLA_RELAY_KEY] = json.dumps({
    "at": (now - timedelta(minutes=3)).isoformat(), "listed": 3,
    "jobs": [good, {"id": "li:123", "title": "x", "url": "u", "company": "Tesla"},
             {"id": "ats:abc", "title": "Intern", "url": "u", "company": "Not Tesla"}, "junk"],
})
rows = w._tesla_relay_rows(now)
check("takes only well-formed Tesla 'ats:' rows", [r["id"] for r in rows] == [good["id"]], str(rows))
check("the relay key matches on both sides", w.TESLA_RELAY_KEY == t.RELAY_KEY)


class Grab(logging.Handler):
    def __init__(self):
        super().__init__()
        self.msgs = []

    def emit(self, record):
        self.msgs.append((record.levelno, record.getMessage()))


grab = Grab()
w.log.addHandler(grab)
relay[w.TESLA_RELAY_KEY] = json.dumps({"at": (now - timedelta(hours=3)).isoformat(), "listed": 3, "jobs": [good]})
rows = w._tesla_relay_rows(now)
check("a stale relay still hands over its rows", len(rows) == 1)
check("…and warns that the Mac has stopped reading",
      any(lvl == logging.WARNING and "180 minutes old" in m for lvl, m in grab.msgs), str(grab.msgs))
w.log.removeHandler(grab)

print("\n-- run(): relayed rows go through the same sweep as every board --")
swept = []
_orig = {k: getattr(w, k) for k in ("start_run", "finish_run", "fetch_all_listings", "_sweep",
                                    "_full_sweep_due", "_always_full_tokens")}
_orig_mods = (w.career_sitemaps.collect_listings, w.career_sitemaps.collect_apple_students,
              w.apple_watch.seen_reqs, w.apple_watch.shadow_check, w.google_watch.check,
              w.google_watch.shadow_check)
w.start_run = lambda source="ats": 1
w.finish_run = lambda run_id, **kw: None
w.fetch_all_listings = lambda cfg, **kw: []
w._full_sweep_due = lambda now: False
w._always_full_tokens = lambda: frozenset()
w._sweep = lambda rows, ids, nks, tks=None: (swept.append([r["id"] for r in rows]), (len(rows), 0))[1]
w.career_sitemaps.collect_listings = lambda force=False, apple_sitemap=None: []
w.career_sitemaps.collect_apple_students = lambda force=False, full=False, seen_reqs=None: w.career_sitemaps.AppleStudents()
w.apple_watch.seen_reqs = lambda: set()
w.apple_watch.shadow_check = lambda now=None: None
w.google_watch.check = lambda sweep: (0, 0, 0)
w.google_watch.shadow_check = lambda now=None: None

relay[w.TESLA_RELAY_KEY] = json.dumps({"at": datetime.now(timezone.utc).isoformat(), "listed": 3, "jobs": [good]})
w.run()
check("the relayed posting reaches the sweep", [good["id"]] in swept, str(swept))


def broken(key):
    raise w.StateUnavailable(key)


swept.clear()
w.read_state = broken
w.run()
check("an unreadable relay does not stop the board sweep", swept == [[]], str(swept))

for k, v in _orig.items():
    setattr(w, k, v)
(w.career_sitemaps.collect_listings, w.career_sitemaps.collect_apple_students, w.apple_watch.seen_reqs,
 w.apple_watch.shadow_check, w.google_watch.check, w.google_watch.shadow_check) = _orig_mods

print(f"\n{_ran - _fails} passed, {_fails} failed")
sys.exit(1 if _fails else 0)
