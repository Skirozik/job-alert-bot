#!/usr/bin/env python3
"""The amazon.jobs and Eightfold readers in ats_sources.py. Offline: requests.get is
stubbed with cut-down copies of what the two sites answered on 2026-11-18.

    python test_feed_readers.py
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import ats_sources as a

a.time.sleep = lambda s: None  # the pause between Eightfold pages

_fails = 0
_ran = 0


def check(label, cond, why=""):
    global _fails, _ran
    _ran += 1
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"\n        {why}" if not cond and why else ""))
    if not cond:
        _fails += 1


calls = []


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.ok = status, body, status == 200

    def json(self):
        return self._body


def serve(pages):
    """pages: {offset_or_start: (status, body)}; unknown offsets answer an empty page."""
    def get(url, params=None, headers=None, timeout=None):
        calls.append((url, dict(params or {})))
        key = params.get("offset", params.get("start"))
        return Resp(*pages.get(key, (200, {"jobs": [], "hits": 0, "data": {"positions": [], "count": 0}})))
    return types.SimpleNamespace(get=get)


AMZ = {
    "title": "Software Development Engineer Intern, Summer 2027", "city": "Seattle", "state": "WA",
    "location": "US, WA, Seattle", "normalized_location": "Seattle, Washington, USA",
    "posted_date": "November  2, 2026", "job_path": "/en/jobs/10594729/software-development-engineer-intern",
    "description": "Shape what comes next &amp; build<br/>at scale", "basic_qualifications": "- Python",
    "preferred_qualifications": "- Go",
}
AMZ2 = {**AMZ, "title": "Data Center Technician Intern", "city": "", "state": "", "job_path": "/en/jobs/1/x",
        "posted_date": "bad date", "description": None, "basic_qualifications": None, "preferred_qualifications": None}
NO_PATH = {**AMZ, "job_path": ""}

print("-- amazon.jobs --")
calls.clear()
a.requests = serve({0: (200, {"hits": 2, "jobs": [AMZ, AMZ2, NO_PATH]})})
rows = a.fetch_amazon_listings("Amazon", "USA")
check("one request when the first page holds every hit", len(calls) == 1, str(calls))
check("…asking for interns in the one country, newest first",
      calls[0][1]["base_query"] == "intern" and calls[0][1]["normalized_country_code[]"] == "USA"
      and calls[0][1]["sort"] == "recent", str(calls[0][1]))
check("a posting without a path is dropped, the rest kept", len(rows) == 2, str([r["title"] for r in rows]))
r = rows[0]
check("same 'ats:' id scheme as every board", r["id"].startswith("ats:") and r["search_term"] == "ats-watch", r["id"])
check("link is the posting on amazon.jobs", r["url"] == "https://www.amazon.jobs" + AMZ["job_path"], r["url"])
check("location is City, ST", r["location"] == "Seattle, WA", r["location"])
check("description joins the three text fields, unescaped",
      r["description"] == "Shape what comes next & build<br/>at scale\n\n- Python\n\n- Go", repr(r["description"]))
check("'November  2, 2026' (two spaces) becomes an ISO date", r["posted_at"] == "2026-11-02T00:00:00+00:00", r["posted_at"])
check("no city: falls back to the normalized location", rows[1]["location"] == "Seattle, Washington, USA", rows[1]["location"])
check("an unreadable date and empty text fields give None, not a crash",
      rows[1]["posted_at"] is None and rows[1]["description"] is None)

calls.clear()
many = [{**AMZ, "job_path": f"/en/jobs/{i}/x"} for i in range(100)]
a.requests = serve({0: (200, {"hits": 150, "jobs": many}), 100: (200, {"hits": 150, "jobs": many[:50]})})
rows = a.fetch_amazon_listings("Amazon", "USA")
check("pages until the hit count is reached", len(calls) == 2 and [c[1]["offset"] for c in calls] == [0, 100], str([c[1]["offset"] for c in calls]))
check("…and returns every row", len(rows) == 150, str(len(rows)))

calls.clear()
a.requests = serve({k: (200, {"hits": 10_000, "jobs": many}) for k in (0, 100, 200, 300, 400)})
rows = a.fetch_amazon_listings("Amazon", "USA")
check("a runaway hit count stops at the page cap", len(calls) == a._AMAZON_MAX_PAGES, str(len(calls)))

calls.clear()
a.requests = serve({0: (503, {})})
check("a refused read answers [] (the sweep goes on)", a.fetch_amazon_listings("Amazon", "USA") == [])


def boom(url, **kw):
    raise ConnectionError("no route")


a.requests = types.SimpleNamespace(get=boom)
check("a network error answers [] too", a.fetch_amazon_listings("Amazon", "USA") == [])

print("\n-- Eightfold (Microsoft, Qualcomm, PayPal, Boston Scientific) --")
POS = [
    {"id": 1970393557075140, "name": "Research Intern - Firmware Security", "postedTs": 1794957750,
     "standardizedLocations": ["Redmond, WA, US"], "positionUrl": "/careers/job/1970393557075140"},
    {"id": 2, "name": "Security Research: PhD Internship Opportunities", "postedTs": 1794943714,
     "standardizedLocations": ["Redmond, WA, US", "Mountain View, CA, US", "Redmond, WA, US"], "positionUrl": "/careers/job/2"},
    {"id": 3, "name": "Construction Project Engineer", "postedTs": 1794928042,
     "standardizedLocations": ["US"], "positionUrl": "/careers/job/3"},
    {"id": 4, "name": "Software Engineering Intern", "postedTs": 1794928000,
     "standardizedLocations": ["US"], "positionUrl": "/careers/job/4"},
    {"id": 5, "name": "Software Engineering Intern (no link)", "postedTs": 1794928000,
     "standardizedLocations": ["Redmond, WA, US"], "positionUrl": ""},
]
calls.clear()
a.requests = serve({0: (200, {"data": {"positions": POS, "count": 5}})})
rows = a.fetch_eightfold_listings("Microsoft", "apply.careers.microsoft.com:microsoft.com")
check("asks the site's search for interns in the U.S., newest first",
      calls[0][0] == "https://apply.careers.microsoft.com/api/pcsx/search" and calls[0][1]["domain"] == "microsoft.com"
      and calls[0][1]["query"] == "intern" and calls[0][1]["sort_by"] == "timestamp", str(calls[0]))
check("keeps only intern-titled postings with a link (the query matches descriptions too)",
      [r["title"] for r in rows] == ["Research Intern - Firmware Security",
                                     "Security Research: PhD Internship Opportunities", "Software Engineering Intern"],
      str([r["title"] for r in rows]))
check("link is the posting on the company's site", rows[0]["url"] == "https://apply.careers.microsoft.com/careers/job/1970393557075140", rows[0]["url"])
check("one location reads City, ST", rows[0]["location"] == "Redmond, WA", rows[0]["location"])
check("several are joined, without repeats", rows[1]["location"] == "Redmond, WA; Mountain View, CA", rows[1]["location"])
check("a bare 'US' is kept as it came", rows[2]["location"] == "US", rows[2]["location"])
check("postedTs (epoch seconds) becomes an ISO date", rows[0]["posted_at"] == "2026-11-17T23:22:30+00:00", rows[0]["posted_at"])
check("no description in the list (read per posting later, like SmartRecruiters)", all(r["description"] is None for r in rows))

calls.clear()
page = lambda i: [{**POS[0], "id": i * 10 + k, "positionUrl": f"/careers/job/{i * 10 + k}"} for k in range(10)]
three = serve({0: (200, {"data": {"positions": page(0), "count": 23}}), 10: (200, {"data": {"positions": page(1), "count": 23}}),
               20: (200, {"data": {"positions": page(2)[:3], "count": 23}})})
a.requests = three
rows = a.fetch_eightfold_listings("Qualcomm", "qualcomm.eightfold.ai:qualcomm.com")
check("a watcher pass reads only the two newest pages (Eightfold's rate limit is tight; the list is newest first)",
      a._EIGHTFOLD_MAX_PAGES == 2 and [c[1]["start"] for c in calls] == [0, 10] and len(rows) == 20,
      f"{[c[1]['start'] for c in calls]} {len(rows)}")
calls.clear()
cap, a._EIGHTFOLD_MAX_PAGES = a._EIGHTFOLD_MAX_PAGES, 10
rows = a.fetch_eightfold_listings("Qualcomm", "qualcomm.eightfold.ai:qualcomm.com")
check("with the cap raised (a seed), it pages by 10 until the count is reached",
      [c[1]["start"] for c in calls] == [0, 10, 20] and len(rows) == 23, f"{[c[1]['start'] for c in calls]} {len(rows)}")
a._EIGHTFOLD_MAX_PAGES = cap

calls.clear()
a.requests = serve({k: (200, {"data": {"positions": page(k // 10), "count": 100_000}}) for k in range(0, 200, 10)})
a.fetch_eightfold_listings("Qualcomm", "qualcomm.eightfold.ai:qualcomm.com")
check("a runaway count stops at the page cap", len(calls) == a._EIGHTFOLD_MAX_PAGES, str(len(calls)))
calls.clear()
slept = []
a.time.sleep = slept.append
a.fetch_eightfold_listings("Qualcomm", "qualcomm.eightfold.ai:qualcomm.com")
check("…pausing between pages", slept == [a._EIGHTFOLD_PAGE_PAUSE_S] * (a._EIGHTFOLD_MAX_PAGES - 1) and a._EIGHTFOLD_PAGE_PAUSE_S >= 2, str(slept))
a.time.sleep = lambda s: None

a.requests = serve({0: (403, {"message": "Not authorized for PCSX"})})
check("a refused read answers []", a.fetch_eightfold_listings("PayPal", "paypal.eightfold.ai:paypal.com") == [])

# Eightfold rate-limits by address across its sites: on 2026-11-18 the sweep read Microsoft
# and Qualcomm in the same second and both came back 429. One retry after a pause, and the
# sites are read one at a time.
calls.clear()
slept = []
a.time.sleep = slept.append
answers = iter([(429, {}), (200, {"data": {"positions": POS[:1], "count": 1}})])
a.requests = types.SimpleNamespace(get=lambda url, params=None, **kw: (calls.append((url, dict(params))), Resp(*next(answers)))[1])
rows = a.fetch_eightfold_listings("Microsoft", "apply.careers.microsoft.com:microsoft.com")
check("a 429 is tried once more after a pause, and the retry's rows are kept",
      len(calls) == 2 and slept == [a._EIGHTFOLD_RETRY_PAUSE_S] and len(rows) == 1, f"{len(calls)} {slept} {len(rows)}")
answers = iter([(429, {}), (429, {})])
check("a second 429 gives up ([])", a.fetch_eightfold_listings("Microsoft", "apply.careers.microsoft.com:microsoft.com") == []
      and len(calls) == 4)
a.time.sleep = lambda s: None
held = []
a.requests = types.SimpleNamespace(get=lambda url, **kw: (held.append(a._EIGHTFOLD_LOCK.locked()),
                                                        Resp(200, {"data": {"positions": [], "count": 0}}))[1])
a.fetch_eightfold_listings("Qualcomm", "qualcomm.eightfold.ai:qualcomm.com")
check("every request is made holding the Eightfold lock (sites read one at a time)", held == [True], str(held))
check("…and the lock is released afterwards", not a._EIGHTFOLD_LOCK.locked())
check("a token without host:domain answers [] and never calls out",
      a.fetch_eightfold_listings("PayPal", "paypal") == [])

print("\n-- wired into the sweep --")
check("both platforms are in the fetcher table", a._FETCHERS["amazon"] is a.fetch_amazon_listings
      and a._FETCHERS["eightfold"] is a.fetch_eightfold_listings)
import ats_config
plats = {v["platform"] for v in ats_config.ATS_COMPANIES.values()}
check("every configured platform has a fetcher", plats <= set(a._FETCHERS), str(plats - set(a._FETCHERS)))
check("every Eightfold entry's token is host:domain",
      all(":" in v["token"] and "." in v["token"].split(":")[1] for v in ats_config.ATS_COMPANIES.values() if v["platform"] == "eightfold"))

print(f"\n{_ran - _fails} passed, {_fails} failed")
sys.exit(1 if _fails else 0)
