"""The Apple alert, end to end, on Apple's real pages.

fixtures/apple/ holds jobs.apple.com as it was served on 2026-10-30: pages 1 and 2
of the U.S. internships search, page 1 of every U.S. job, and two posting pages.
A scenario changes only the posting list inside a real page -- the data Apple
embeds for its own rendering -- and serves the result.

Faked, at the edges only: HTTP to Apple and to ntfy, the database (dicts), and the
classifier (a fixed verdict). Real, everything between: the reader,
ats_watch.run(), the title pre-filters, process_job and its push, the 🍎 push,
the ledger, the alarms, the shadow watch and the drill.

What it proves without Apple posting anything new:
  - Aug 27, replayed: Applied Data Solutions appears on a page that did not have
    it. One 🍎 push listing both cities, the usual push too, and nothing on the
    pass after.
  - the cases a real posting would hit: a reused title, PhD/Masters/MBA pools, a
    burst, a failed push, Apple refusing or changing its page, a first run that
    cannot read every page, the sitemap's internship titles.
  - the shadow watch and the drill do what the alarms depend on.

Run: cd scraper && python test_apple_watch.py
"""

import copy
import fnmatch
import gzip
import json
import logging
import re
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import requests

import apple_watch as aw
import ats_watch as w
import career_sitemaps as cs
import db
import main
import notifier
from db import job_norm_key

logging.getLogger().setLevel(logging.WARNING)
_dumps = json.dumps
_fails = 0


def check(label, cond, why=""):
    global _fails
    if cond:
        print(f"  PASS  {label}")
    else:
        _fails += 1
        print(f"  FAIL  {label}" + (f"\n        {why}" if why else ""))


# ── Apple's real pages ────────────────────────────────────────────────────────
FIX = Path(__file__).parent.parent / "fixtures" / "apple"


def _real(name: str) -> str:
    return gzip.open(FIX / f"{name}.html.gz", "rt", encoding="utf-8").read()


P1, P2, ALL_US = _real("students_p1"), _real("students_p2"), _real("all_us_p1")
D_ADS, D_SWE = _real("detail_applied_data_solutions_austin"), _real("detail_software_undergrad")


def _decode(page: str) -> dict:
    return json.loads(json.loads('"' + cs._APPLE_DATA.search(page).group(1) + '"'))


def _with(page: str, mutate) -> str:
    """The real page, with its embedded data changed by mutate and put back in Apple's own form: a JSON
    document inside a JS string literal. Every other byte of the page is Apple's."""
    data = _decode(page)
    mutate(data)
    m = cs._APPLE_DATA.search(page)
    return page[:m.start(1)] + _dumps(_dumps(data))[1:-1] + page[m.end(1):]


def _results(page: str) -> list[dict]:
    return _decode(page)["loaderData"]["search"]["searchResults"]


REAL = _results(P1) + _results(P2)
ADS = [r for r in REAL if r["id"].startswith("200673612-")]
SWE_TITLE = "Software Undergrad Engineering Internships"


def students_pages(records: list[dict]) -> dict[int, str]:
    """The internships search as Apple would serve these records: 20 to a page, inside its real pages."""
    def page(base, chunk):
        return _with(base, lambda d: d["loaderData"]["search"].update(searchResults=chunk,
                                                                     totalRecords=len(records)))
    return {n: page(P1 if n == 1 else P2, records[20 * (n - 1):20 * n]) for n in range(1, cs.APPLE_STUDENTS_MAX_PAGES + 1)}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def new_record(title: str, rid: str, posted: datetime | None = None, locs=("United States",)) -> dict:
    """A posting in Apple's own record shape: a real record with its identifying fields replaced."""
    r = copy.deepcopy(next(x for x in REAL if x["postingTitle"] == "MBA Internships"))
    loc = r["locations"][0]
    r.update(id=rid, positionId=rid, reqId=rid, postingTitle=title,
             transformedPostingTitle=re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"),
             postDateInGMT=_gmt(posted or _now() - timedelta(minutes=4, seconds=10)),
             locations=[dict(loc, name=n) for n in locs])
    return r


def url_of(r: dict) -> str:
    return f"https://jobs.apple.com/en-us/details/{r['id']}/{r['transformedPostingTitle']}"


def id_of(url: str) -> str:
    return cs._make_job("Apple", "-", "", url, None, None)["id"]


# ── the outside world, faked ──────────────────────────────────────────────────
SITE: dict = {"students": students_pages(REAL), "all_us": ALL_US, "status": {}}
FETCHED: list[str] = []
ORDER: list[str] = []
PUSHES: list[dict] = []
NTFY = {"apple_status": 200, "feed_drop": False}


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
        lines = [] if NTFY["feed_drop"] else [_dumps({"id": p["id"], "event": "message"}) for p in PUSHES]
        return _Resp(200, "\n".join(lines))
    FETCHED.append(url)
    base, _, page_no = url.partition("&page=")
    n = int(page_no or 1)
    if base == cs.APPLE_STUDENTS_URL:
        ORDER.append("apple-page")
        status = SITE["status"].get(("students", n), SITE["status"].get("students", 200))
        return _Resp(status, SITE["students"].get(n, "") if status == 200 else "Forbidden")
    if base == cs.APPLE_ALL_US_URL:
        status = SITE["status"].get("all_us", 200)
        return _Resp(status, SITE["all_us"] if status == 200 else "Forbidden")
    if "/en-us/details/" in url:
        return _Resp(200, D_ADS if "/200673612-" in url else D_SWE)
    raise AssertionError(f"unexpected fetch: {url}")


def fake_post(url, data=None, headers=None, json=None, timeout=None):
    assert url.startswith(notifier.NTFY_BASE), url
    if json is not None:
        push = {"kind": "apple", "title": json["title"], "body": json["message"], "priority": json["priority"],
                "click": json["click"], "tags": json["tags"]}
        if NTFY["apple_status"] != 200:
            return _Resp(NTFY["apple_status"])
        ORDER.append("apple-push")
    else:
        title = headers.get("Title", "")
        push = {"kind": "canary" if title == "Job scraper alert" else "job", "title": title,
                "body": data.decode("utf-8"), "priority": headers.get("Priority"), "click": headers.get("Click")}
    push["id"] = f"msg{len(PUSHES) + 1}"
    PUSHES.append(push)
    return _Resp(200, _dumps({"id": push["id"], "event": "message"}))


requests.get, requests.post = fake_get, fake_post
notifier.NTFY_TOPIC = "test-topic"
cs.DETAIL_GAP_S = 0

STORE: dict[str, dict] = {}
STATE: dict[str, str] = {}
STATE_DOWN = {"on": False}
CLAIMED: set[str] = set()
CLASSIFIED: list[str] = []
VERDICT = {"tier": "APPLY", "reason": "Undergrad CS internship, Summer 2027"}
SITEMAP_ROWS: list[dict] = []
QUERIES: list[str] = []


def store_row(job: dict, tier="APPLY_CAVEAT", reason="stored earlier", status="new",
              found_at="2026-10-06T23:01:00+00:00") -> None:
    STORE[job["id"]] = {"id": job["id"], "title": job["title"], "company": job["company"], "url": job["url"],
                        "norm_key": job_norm_key(job), "tier": tier, "reason": reason, "status": status,
                        "found_at": found_at}


def fake_unknown(jobs, batch_size=5000):
    keys = {r["norm_key"] for r in STORE.values()}
    out = set()
    for j in jobs:
        nk = j.get("norm_key") or job_norm_key(j)
        if j["id"] not in STORE and (nk == "|" or nk not in keys):
            out.add(j["id"])
    return out


def fake_insert(job):
    if job["id"] not in STORE:
        store_row(job, tier=job.get("tier", "APPLY_CAVEAT"), reason=job.get("reason", ""),
                  found_at=_now().isoformat())
    return True


def fake_classify(job):
    CLASSIFIED.append(job["title"])
    if VERDICT.get("failed"):
        return {"failed": True, "failed_kind": "billing"}
    return dict(VERDICT, suggested_resume="General")


def fake_claim(job_id):
    if job_id in CLAIMED:
        return False, "already-notified"
    CLAIMED.add(job_id)
    return True, "claimed"


def read_state(key):
    if STATE_DOWN["on"]:
        raise db.StateUnavailable(key)
    return STATE.get(key)


class _Query:
    """Just enough of the Supabase query builder for apple_watch._stored_rows, evaluating its filter."""

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
        terms = re.findall(r'(\w+)\.(eq|like)\."([^"]*)"', self.expr)
        assert terms and len(terms) == self.expr.count('."'), f"malformed filter: {self.expr}"

        def hit(row):
            return any(row.get(col) == val if op == "eq" else fnmatch.fnmatchcase(row.get(col) or "", val)
                       for col, op, val in terms)
        rows = [r for r in STORE.values() if all(r.get(k) == v for k, v in self.filters.items()) and hit(r)]
        return types.SimpleNamespace(data=[{c: r.get(c) for c in self.cols} for r in rows])


for mod in (cs,):
    mod.get_state, mod.set_state, mod.find_unknown_candidates = STATE.get, STATE.__setitem__, fake_unknown
aw.get_state, aw.set_state, aw.read_state = STATE.get, STATE.__setitem__, read_state
aw.clear_state = lambda key: STATE.pop(key, None)
aw.get_client = _Query
aw.classify = main.classify = fake_classify
main.get_job_row = lambda jid: ({k: STORE[jid][k] for k in ("id", "tier", "status")} if jid in STORE else None)
main.insert_job = w.insert_job = fake_insert
main.claim_notification = fake_claim
w.find_unknown_candidates = fake_unknown
w.start_run = lambda source="ats": 1
w.finish_run = lambda run_id, **kw: None
w.fetch_all_listings = lambda cfg: (ORDER.append("boards"), [])[1]
cs.collect_listings = lambda force=False: [dict(r) for r in SITEMAP_ROWS]


def reset(records=REAL, stored=REAL, applied=(SWE_TITLE,)):
    """A world where Apple lists `records` and the tracker already holds `stored` (found Oct 6)."""
    for d in (STORE, STATE, SITE["status"]):
        d.clear()
    CLAIMED.clear()
    SITEMAP_ROWS.clear()
    QUERIES.clear()
    STATE_DOWN["on"] = False
    NTFY.update(apple_status=200, feed_drop=False)
    VERDICT.clear()
    VERDICT.update(tier="APPLY", reason="Undergrad CS internship, Summer 2027")
    SITE["students"] = students_pages(records)
    for r in stored:
        job = cs._make_job("Apple", r["postingTitle"].strip(), "", url_of(r), None, None)
        store_row(job, status="applied" if r["postingTitle"] in applied else "new")


def run_pass(full_read_age_min: float | None = 10) -> list[dict]:
    """One 5-minute ATS pass: the last pass 5 minutes ago, the last full read full_read_age_min ago
    (None: as recorded). The shadow watch is held back; it is tested on its own below."""
    STATE[cs.APPLE_STUDENTS_STATE_KEY] = (_now() - timedelta(minutes=5)).isoformat()
    if full_read_age_min is not None and cs.APPLE_STUDENTS_FULL_STATE_KEY in STATE:
        STATE[cs.APPLE_STUDENTS_FULL_STATE_KEY] = (_now() - timedelta(minutes=full_read_age_min)).isoformat()
    STATE[aw.SHADOW_CHECKED_KEY] = _now().isoformat()
    for lst in (FETCHED, ORDER, PUSHES, CLASSIFIED):
        lst.clear()
    w.run()
    return list(PUSHES)


def kinds(pushes: list[dict], kind: str) -> list[dict]:
    return [p for p in pushes if p["kind"] == kind]


def searches() -> list[str]:
    return [u for u in FETCHED if "/search?" in u]


def ledger() -> set[str]:
    return set(json.loads(STATE[aw.LEDGER_KEY])["reqs"]) if aw.LEDGER_KEY in STATE else set()


# ═════════════════════════════════════════════════════════════════════════════
print("-- the reader, on Apple's real pages --")
posts1, total1 = cs.parse_apple_search(P1)
posts2, total2 = cs.parse_apple_search(P2)
check("page 1: the 20 newest of 25", len(posts1) == 20 and total1 == 25, f"{len(posts1)} of {total1}")
check("page 2: the other 5", len(posts2) == 5 and total2 == 25, f"{len(posts2)} of {total2}")
everything = posts1 + posts2
check("25 distinct postings, 24 requisitions (Applied Data Solutions is one req in two cities)",
      len({p["apple_id"] for p in everything}) == 25 and len({p["req"] for p in everything}) == 24)
check("page 1 is newest first, which is what reading only page 1 relies on", cs._newest_first(posts1))
check("both Applied Data Solutions postings are read, Austin and Cupertino",
      sorted(p["location"] for p in everything if p["req"] == "200673612") == ["Austin", "Cupertino"])
check("every posting links to its own page", all(re.fullmatch(r"https://jobs\.apple\.com/en-us/details/\d+-\d+/[a-z0-9-]+",
                                                            p["url"]) for p in everything))
cupertino = next(p for p in everything if p["apple_id"] == "200673612-0836")
check("the reader's id for a posting is the id the live tracker already stores for it (no duplicates on deploy)",
      id_of(cupertino["url"]) == "ats:93c855b68ba08a7b", id_of(cupertino["url"]))
for name, page in (("Applied Data Solutions", D_ADS), ("Software Undergrad", D_SWE)):
    d = cs.parse_apple_detail(page) or ""
    check(f"{name}: the posting page gives the description and qualifications",
          len(d) > 1000 and "Minimum qualifications:" in d and "<" not in d, d[:120])
all_posts, all_total = cs.parse_apple_search(ALL_US)
check("every U.S. job: page 1 of thousands, newest first", len(all_posts) == 20 and all_total > 1000
      and cs._newest_first(all_posts), f"{len(all_posts)} of {all_total}")

print("\n-- a page the reader cannot read is never 'no postings' --")
for label, page in (("the embedded data is gone", P1.replace("__staticRouterHydrationData", "__appState")),
                    ("Apple renamed the title field", _with(P1, lambda d: [r.update(postingTitle=None, jobTitle="x")
                                                                        for r in d["loaderData"]["search"]["searchResults"]])),
                    ("the page is a challenge page", "<html><body>Please verify you are a human</body></html>")):
    try:
        cs.parse_apple_search(page)
        check(f"{label}: unreadable", False, "parsed as if it were fine")
    except cs.ApplePageUnreadable:
        check(f"{label}: unreadable", True)
empty = _with(P1, lambda d: d["loaderData"]["search"].update(searchResults=[], totalRecords=0))
check("a genuinely empty search is no postings, not an error", cs.parse_apple_search(empty) == ([], 0))

print("\n-- which postings push --")
for title, on_page, want in [(SWE_TITLE, True, True),
                             ("Applied Data Solutions Program, Internships – Summer 2027", True, True),
                             ("Legal Internships", True, True),
                             ("Software Engineering Masters Internships", True, False),
                             ("Machine Learning and Artificial Intelligence PhD Internships", True, False),
                             ("Hardware Technologies Phd Internships", False, False),
                             ("MBA Internships", True, False),
                             ("Software Engineering Internships Summer", False, True),
                             ("Internal Communications Strategist", False, False),
                             ("iOS Software Engineer", False, False)]:
    check(f"{'internships page' if on_page else 'sitemap'}: {title!r} -> {'push' if want else 'no push'}",
          aw.wanted(title, on_page) is want)

# ═════════════════════════════════════════════════════════════════════════════
print("\n-- the first pass after deploy: record what is there, push nothing --")
reset()
pushes = run_pass()
check("reads every page (a ledger seeded from page 1 alone would push page 2 an hour later)",
      searches() == [cs.APPLE_STUDENTS_URL, cs.APPLE_STUDENTS_URL + "&page=2"], str(searches()))
check("records all 24 requisitions", ledger() == {p["req"] for p in everything}, str(len(ledger())))
check("pushes nothing and classifies nothing", pushes == [] and CLASSIFIED == [], str(pushes))
pushes = run_pass()
check("the pass after reads page 1 only, and is quiet", searches() == [cs.APPLE_STUDENTS_URL] and pushes == [],
      str(searches()))

print("\n-- Aug 27, replayed: Applied Data Solutions appears --")
before = [r for r in REAL if r not in ADS]
reset(records=before, stored=before)
run_pass()
check("before: seeded without it, nothing pushed", "200673612" not in ledger() and PUSHES == [])
appeared = [dict(r, postDateInGMT=_gmt(_now() - timedelta(minutes=4, seconds=10))) for r in ADS]
SITE["students"] = students_pages(appeared + before)
pushes = run_pass()
apple = kinds(pushes, "apple")
check("exactly one 🍎 push for the requisition, not one per city", len(apple) == 1, str([p["title"] for p in apple]))
a = apple[0] if apple else {"title": "", "body": "", "priority": None, "click": None, "tags": None}
check("titled with the 🍎 and Apple's own title",
      a["title"] == "🍎 APPLE - Applied Data Solutions Program, Internships – Summer 2027", a["title"])
check("both cities are in it", a["body"].splitlines()[0] == "Austin, Cupertino", a["body"])
check("with the bot's verdict and reason", "Bot's verdict: APPLY — Undergrad CS internship, Summer 2027" in a["body"],
      a["body"])
check("with how long ago Apple posted it", "Posted 4 min ago" in a["body"], a["body"])
check("at ntfy's top priority, opening the posting", a["priority"] == 5 and a["click"] == url_of(ADS[0]),
      f"{a['priority']} {a['click']}")
check("the usual push goes out too (two notifications, as asked; Apple is a gold-star company)",
      [p["title"] for p in kinds(pushes, "job")] == ["GOLD - Apple - Applied Data Solutions Program, Internships ? Summer 2027"],
      str([p["title"] for p in kinds(pushes, "job")]))
check("found on page 1: no other search page was read", searches() == [cs.APPLE_STUDENTS_URL], str(searches()))
check("classified once, with the description from the posting's page",
      CLASSIFIED == ["Applied Data Solutions Program, Internships – Summer 2027"], str(CLASSIFIED))
check("stored in the tracker", any(r["title"].startswith("Applied Data Solutions") for r in STORE.values()))
check("the 🍎 push lands before the 104-board sweep starts",
      "apple-push" in ORDER and ORDER.index("apple-push") < ORDER.index("boards"), str(ORDER))
pushes = run_pass()
check("the pass after: no push, no second classification", pushes == [] and CLASSIFIED == [], str(pushes))
pushes = run_pass(full_read_age_min=61)
check("the hourly full read after: still nothing", pushes == [] and len(searches()) == 2, str(searches()))

print("\n-- a new requisition with an old title (how Summer 2028 will likely arrive) --")
reset()
run_pass()
reused = new_record(SWE_TITLE, "200712345-3810")
SITE["students"] = students_pages([reused] + REAL)
pushes = run_pass()
apple = kinds(pushes, "apple")
check("still pushes 🍎, though the tracker takes it for the stored job", len(apple) == 1, str(pushes))
body = apple[0]["body"] if apple else ""
check("says the bot did not rate it, and why", "a role with this title is already in your tracker" in body, body)
check("says he applied to the earlier one, and when that was first seen",
      "You applied to an earlier posting of this title (first seen Oct 6)." in body, body)
check("no usual push and no classification: the tracker path is unchanged",
      kinds(pushes, "job") == [] and CLASSIFIED == [], str(CLASSIFIED))
check("the tracker lookup asked for this title and this requisition",
      QUERIES and 'url.like."*/details/200712345-*"' in QUERIES[-1] and "norm_key.eq." in QUERIES[-1],
      str(QUERIES[-1:]))
check("and the pass after is quiet", run_pass() == [])

print("\n-- PhD, Masters and MBA pools --")
reset()
run_pass()
pools = [new_record("Software Engineering PhD Internships – Summer 2028", "200712346-3810"),
         new_record("Hardware Masters Engineering Internships – Summer 2028", "200712347-3810"),
         new_record("MBA Internships – Summer 2028", "200712348-3810")]
SITE["students"] = students_pages(pools + REAL)
pushes = run_pass()
check("no 🍎 push for any of them", kinds(pushes, "apple") == [], str([p["title"] for p in kinds(pushes, "apple")]))
check("...but they are recorded, so they never push later", {"200712346", "200712347", "200712348"} <= ledger())
check("...and the tracker still takes them in as before", len(CLASSIFIED) + sum(
      1 for r in STORE.values() if r["title"].endswith("2028") and r["tier"] == "INELIGIBLE") >= 3, str(CLASSIFIED))

print("\n-- a burst: Apple's May launch --")
reset(records=REAL[:1], stored=REAL[:1])
run_pass()
launch = [new_record(r["postingTitle"].replace("Internships", "Internships – Summer 2028"), f"3{i:08d}-3810")
          for i, r in enumerate(REAL) if not r["id"].startswith("200673612-")]
SITE["students"] = students_pages(launch + REAL[:1])
pushes = run_pass()
expected = sum(aw.wanted(r["postingTitle"], True) for r in launch)
apple = kinds(pushes, "apple")
check("every posting on page 1 being new makes the pass read every page", len(searches()) == 2, str(searches()))
check(f"one 🍎 push listing all {expected}, not {expected} pushes", len(apple) == 1 and apple[0]["title"]
      == f"🍎 APPLE - {expected} new internship postings", str([p["title"] for p in apple]))
check("...that names every one, and opens the internships page",
      apple and apple[0]["body"].count("• ") == expected and apple[0]["click"] == cs.APPLE_STUDENTS_URL)

print("\n-- a burst: Apple renumbering every posting --")
reset()
run_pass()
renumbered = [dict(r, id="9" + r["id"][1:]) for r in REAL]
SITE["students"] = students_pages(renumbered)
pushes = run_pass()
apple = kinds(pushes, "apple")
check("page 1's postings come as one push, not a flood", len(apple) == 1 and apple[0]["title"].endswith(
      "new internship postings"), str([p["title"] for p in apple]))
check("...and the tracker takes them for the stored jobs, as before", CLASSIFIED == [], str(CLASSIFIED))
pushes = run_pass(full_read_age_min=61)
page2_wanted = sum(aw.wanted(r["postingTitle"], True) for r in renumbered[20:])
check(f"the hourly full read brings page 2's {page2_wanted} wanted postings (one push each, as a burst of "
      f"{aw.GROUPED_PUSH_OVER} or fewer)", len(kinds(pushes, "apple")) == page2_wanted, str(kinds(pushes, "apple")))
check("after that: quiet", run_pass(full_read_age_min=61) == [])

print("\n-- a push that fails is sent again, once --")
reset()
run_pass()
SITE["students"] = students_pages([new_record("Software Engineering Internships – Summer 2028", "200712349-3810")] + REAL)
NTFY["apple_status"] = 500
pushes = run_pass()
check("ntfy refuses: nothing recorded", kinds(pushes, "apple") == [] and "200712349" not in ledger())
NTFY["apple_status"] = 200
pushes = run_pass()
apple = kinds(pushes, "apple")
check("next pass: pushed", len(apple) == 1, str(pushes))
check("...with the verdict stored on the pass before", apple and "Bot's verdict: APPLY — Undergrad CS internship"
      in apple[0]["body"] and "(rated " in apple[0]["body"], apple[0]["body"] if apple else "")
check("...and not again", run_pass() == [])

print("\n-- the tracker's state store is down: no pushes, no re-seeding, then a normal push --")
reset()
run_pass()
before_ledger = STATE[aw.LEDGER_KEY]
SITE["students"] = students_pages([new_record("Software Engineering Internships – Summer 2028", "200712350-3810")] + REAL)
STATE_DOWN["on"] = True
pushes = run_pass()
check("no 🍎 push while the ledger cannot be read", kinds(pushes, "apple") == [], str(pushes))
check("...and the ledger is not started again from scratch", STATE[aw.LEDGER_KEY] == before_ledger)
STATE_DOWN["on"] = False
check("back up: pushed on the next pass", len(kinds(run_pass(), "apple")) == 1)

print("\n-- Apple turns the bot away --")
reset()
run_pass()
SITE["status"]["students"] = 403
pushes = run_pass()
check("a refused pass alarms nobody yet", kinds(pushes, "canary") == [], str(pushes))
h = json.loads(STATE[aw.HEALTH_KEY])
check("...but is recorded, with the status", h.get("problem") == "refused: HTTP 403" and "failing_since" in h, str(h))
h["failing_since"] -= 3660
STATE[aw.HEALTH_KEY] = json.dumps(h)
pushes = run_pass()
canary = kinds(pushes, "canary")
check("an hour of it: one alarm", len(canary) == 1 and "turned away" in canary[0]["body"]
      and "HTTP 403" in canary[0]["body"], str(canary))
check("the next refused pass does not alarm again", kinds(run_pass(), "canary") == [])
check("the board sweep still ran on every refused pass", "boards" in ORDER)
SITE["status"].clear()
pushes = run_pass()
canary = kinds(pushes, "canary")
check("readable again: one all-clear", len(canary) == 1 and "readable again" in canary[0]["body"], str(canary))
check("...and the record is cleared", aw.HEALTH_KEY not in STATE)

print("\n-- Apple changes its page --")
reset()
run_pass()
SITE["students"] = {1: P1.replace("__staticRouterHydrationData", "__appState")}
pushes = run_pass()
check("one unreadable pass: no alarm (one odd response is not a redesign)", kinds(pushes, "canary") == [])
pushes = run_pass()
canary = kinds(pushes, "canary")
check("two in a row: one alarm saying the page changed", len(canary) == 1 and "can't read" in canary[0]["body"],
      str(canary))
check("a third: no repeat", kinds(run_pass(), "canary") == [])
check("nothing was pushed or recorded from the unreadable page", kinds(PUSHES, "apple") == []
      and len(ledger()) == 24)

print("\n-- a first pass that cannot read every page does not seed --")
reset()
SITE["status"][("students", 2)] = 403
pushes = run_pass()
check("page 2 refused on the first pass: nothing recorded, nothing pushed",
      aw.LEDGER_KEY not in STATE and kinds(pushes, "apple") == [], str(pushes))
SITE["status"].clear()
pushes = run_pass(full_read_age_min=None)
check("the next pass reads every page and seeds, still pushing nothing",
      len(ledger()) == 24 and kinds(pushes, "apple") == [] and len(searches()) == 2, str(searches()))

print("\n-- page 1 out of date order: read every page --")
reset()
run_pass()
shuffled = [dict(REAL[5], postDateInGMT="2026-01-01T00:00:00.000Z")] + REAL[:5] + REAL[6:]
SITE["students"] = students_pages(shuffled)
run_pass()
check("a page 1 that is not newest-first is not trusted alone", len(searches()) == 2, str(searches()))

print("\n-- Apple's full job list (the sitemap) --")
reset()
run_pass()


def sitemap_row(title, rid):
    url = f"https://jobs.apple.com/en-us/details/{rid}/{re.sub(r'[^a-z0-9]+', '-', title.lower())}"
    job = cs._make_job("Apple", title, "United States", url, None, None)
    job["search_term"] = "career-sitemap"
    return job


SITEMAP_ROWS[:] = [sitemap_row("Software Engineering Internships Summer", "200712351-3810"),
                   sitemap_row("Internal Communications Strategist", "200712352-3810"),
                   sitemap_row("Hardware Phd Internships", "200712353-3810"),
                   sitemap_row("Senior Software Engineer", "200712354-3810")]
pushes = run_pass()
apple = kinds(pushes, "apple")
check("an internship title there pushes 🍎", [p["title"] for p in apple] == ["🍎 APPLE - Software Engineering Internships Summer"],
      str([p["title"] for p in apple]))
check("a PhD pool there is recorded without a push", "200712353" in ledger())
check("full-time roles are never recorded (the sitemap adds dozens a day; the ledger is read every pass)",
      not {"200712352", "200712354"} & ledger())
SITEMAP_ROWS.clear()

reset()
run_pass()
both = new_record("Software Engineering Internships – Summer 2028", "200712355-3810")
SITE["students"] = students_pages([both] + REAL)
SITEMAP_ROWS[:] = [dict(sitemap_row("Software Engineering Internships Summer", "200712355-3810"), url=url_of(both),
                        apply_url=url_of(both), id=id_of(url_of(both)))]
pushes = run_pass()
check("one posting on both the internships page and the sitemap: one 🍎 push", len(kinds(pushes, "apple")) == 1,
      str([p["title"] for p in kinds(pushes, "apple")]))
check("...classified once, from the internships page (the version with a description)", len(CLASSIFIED) == 1
      and CLASSIFIED[0].endswith("Summer 2028"), str(CLASSIFIED))
SITEMAP_ROWS.clear()

print("\n-- the bot failing to rate a new posting does not hold back its 🍎 push --")
reset()
run_pass()
SITE["students"] = students_pages([new_record("Software Engineering Internships – Summer 2028", "200712356-3810")] + REAL)
_orig_process = w.process_job
w.process_job = lambda job: (_ for _ in ()).throw(RuntimeError("star rules missing"))
pushes = run_pass()
w.process_job = _orig_process
apple = kinds(pushes, "apple")
check("the 🍎 push still goes, without a verdict", len(apple) == 1 and "not rated yet" in apple[0]["body"],
      str(pushes))
check("...and the board sweep still runs", "boards" in ORDER, str(ORDER))

print("\n-- one Apple failure never costs the board sweep --")
reset()
_orig_unknown = cs.find_unknown_candidates
cs.find_unknown_candidates = lambda jobs, batch_size=5000: (_ for _ in ()).throw(RuntimeError("dedup down"))
run_pass()
cs.find_unknown_candidates = _orig_unknown
check("the boards are still swept", "boards" in ORDER, str(ORDER))

# ═════════════════════════════════════════════════════════════════════════════
print("\n-- the shadow watch: the same reader, where Apple posts every day --")
reset()
t0 = time.time()
STATE.pop(aw.SHADOW_CHECKED_KEY, None)
FETCHED.clear()
PUSHES.clear()
aw.shadow_check(now=t0)
s = json.loads(STATE[aw.SHADOW_KEY])
check("first check: page 1 of every U.S. job is read and recorded", FETCHED == [cs.APPLE_ALL_US_URL]
      and len(s["seen"]) == 20 and not s.get("log"), str(FETCHED))
FETCHED.clear()
aw.shadow_check(now=t0 + 1800)
check("half an hour later: not read again (hourly)", FETCHED == [])

t1 = t0 + 3700


def with_new_on_all_us(posted_minutes_ago: list[int], at: float, rid_base: int):
    def mutate(d):
        res = d["loaderData"]["search"]["searchResults"]
        res[:0] = [new_record(f"Software Engineer {i}", f"{rid_base + i}-3810",
                              posted=datetime.fromtimestamp(at - 60 * m, timezone.utc))
                   for i, m in enumerate(posted_minutes_ago)]
        del res[20:]
    return _with(ALL_US, mutate)


SITE["all_us"] = with_new_on_all_us([12, 40], t1, 300000000)
aw.shadow_check(now=t1)
s = json.loads(STATE[aw.SHADOW_KEY])
check("two new postings: both seen, with how long after Apple's posting time",
      sorted(d for _, d in s["log"]) == [12, 40], str(s.get("log")))
check("nothing is pushed and nothing reaches the tracker", PUSHES == [] and len(STORE) == len(REAL))
check("the drill reports it", aw.shadow_summary(now=t1) ==
      "Shadow watch: 2 new U.S. Apple jobs seen in the last 7 days, typically 26 min after Apple posted them.",
      aw.shadow_summary(now=t1))

hour = 3700
t = t1
while t + hour < t1 + 48 * 3600:
    t += hour
    aw.shadow_check(now=t)
check("47 quiet hours: no alarm (Sundays are slow)", kinds(PUSHES, "canary") == [], str(PUSHES))
SITE["status"]["all_us"] = 403
t_alarm = max(t1 + 48 * 3600 + 1, t + hour)
aw.shadow_check(now=t_alarm)
SITE["status"].clear()
canary = kinds(PUSHES, "canary")
check("48 hours with nothing new (the last hour refused): one alarm", len(canary) == 1
      and "48 hours" in canary[0]["body"], str(canary))
aw.shadow_check(now=t_alarm + hour)
check("an hour later: no repeat", len(kinds(PUSHES, "canary")) == 1)
SITE["all_us"] = with_new_on_all_us([5], t_alarm + 2 * hour, 300000100)
aw.shadow_check(now=t_alarm + 2 * hour)
canary = kinds(PUSHES, "canary")
check("a new posting again: one all-clear", len(canary) == 2 and "showing new postings again" in canary[1]["body"],
      str(canary))
check("...and the alarm is re-armed", not json.loads(STATE[aw.SHADOW_KEY]).get("alerted"))
SITE["all_us"] = ALL_US

# ═════════════════════════════════════════════════════════════════════════════
print("\n-- the weekly drill --")
reset()
VERDICT.update(tier="APPLY_CAVEAT", reason="Undergrad pool; no term listed")
store_before, state_before = copy.deepcopy(STORE), copy.deepcopy(STATE)
PUSHES.clear()
CLASSIFIED.clear()
FETCHED.clear()
rc = aw.drill()
apple, job = kinds(PUSHES, "apple"), kinds(PUSHES, "job")
check("passes", rc == 0)
check("reads the live internships page and the posting's own page", FETCHED[0] == cs.APPLE_STUDENTS_URL
      and any("/details/200664785-3810/" in u for u in FETCHED), str(FETCHED))
check("runs the real classifier path once, on the posting", CLASSIFIED == [SWE_TITLE], str(CLASSIFIED))
check("sends the 🍎 push, marked DRILL", len(apple) == 1 and apple[0]["title"] == f"🍎 DRILL - APPLE - {SWE_TITLE}"
      and apple[0]["tags"] == ["drill"] and apple[0]["priority"] == 5, str(apple))
d_body = apple[0]["body"] if apple else ""
check("...saying it is a drill, with the verdict, the shadow watch and the page's health",
      d_body.startswith("Weekly drill") and "Bot's verdict: APPLY with a caveat — Undergrad pool" in d_body
      and "Shadow watch:" in d_body and "Internships page: reading normally." in d_body, d_body)
check("...and what the tracker knows (he applied to it)", "You've already applied to this posting." in d_body, d_body)
check("sends the usual push too, marked DRILL", [p["title"] for p in job] == [f"DRILL - Apple - {SWE_TITLE}"],
      str([p["title"] for p in job]))
check("writes nothing: tracker and state exactly as before", STORE == store_before and STATE == state_before)

reset()
SITE["status"]["students"] = 403
PUSHES.clear()
rc = aw.drill()
canary = kinds(PUSHES, "canary")
check("Apple refusing the drill: it fails, and says where", rc == 1 and len(canary) == 1
      and "drill FAILED at reading Apple's internships page (HTTP 403)" in canary[0]["body"]
      and not kinds(PUSHES, "apple"), str(PUSHES))

reset()
NTFY["feed_drop"] = True
PUSHES.clear()
rc = aw.drill()
canary = kinds(PUSHES, "canary")
check("ntfy taking the push but not holding it: it fails at delivery", rc == 1 and canary
      and "confirming delivery" in canary[-1]["body"], str(canary))

reset()
VERDICT.clear()
VERDICT.update(failed=True)
PUSHES.clear()
rc = aw.drill()
apple = kinds(PUSHES, "apple")
check("the classifier down: the 🍎 push still goes, saying the verdict is pending (the classifier has its own alarm)",
      rc == 0 and apple and "pending" in apple[0]["body"] and not kinds(PUSHES, "job"), str(PUSHES))

print()
if _fails:
    print(f"{_fails} FAILED")
    sys.exit(1)
print("all Apple alert checks passed")
