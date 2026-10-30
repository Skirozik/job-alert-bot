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
import os
import re
import sys
import tempfile
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
# feed_lag_polls: how many read-backs miss a delivered message (ntfy.sh writes its cache in batches).
NTFY = {"apple_status": 200, "canary_status": 200, "feed_drop": False, "feed_lag_polls": 0, "polls": 0}


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
        # The topic's own feed, as ntfy serves it: only with poll=1, only messages since `since`.
        assert url == f"{notifier.NTFY_BASE}/{notifier.NTFY_TOPIC}/json", f"read-back from {url}"
        assert (params or {}).get("poll") == "1", f"read-back without poll=1: {params}"
        since = int((params or {}).get("since", 0))
        NTFY["polls"] += 1
        hidden = NTFY["feed_drop"] or NTFY["polls"] <= NTFY["feed_lag_polls"]
        lines = [] if hidden else [_dumps({"id": p["id"], "event": "message", "time": p["time"]})
                                   for p in PUSHES if p["time"] >= since]
        return _Resp(200, "\n".join(lines))
    FETCHED.append(url)
    base, _, page_no = url.partition("&page=")
    n = int(page_no or 1)
    if base == cs.APPLE_STUDENTS_URL:
        ORDER.append("apple-page")
        status = SITE["status"].get(("students", n), SITE["status"].get("students", 200))
        if n == 1 and SITE.get("students_seq"):
            status = SITE["students_seq"].pop(0)
        return _Resp(status, SITE["students"].get(n, "") if status == 200 else "Forbidden")
    if base == cs.APPLE_ALL_US_URL:
        status = SITE["status"].get("all_us", 200)
        return _Resp(status, SITE["all_us"] if status == 200 else "Forbidden")
    if "/en-us/details/" in url:
        return _Resp(200, SITE.get("detail") or (D_ADS if "/200673612-" in url else D_SWE))
    for source in cs.SOURCES:
        if url == source["sitemap"]:
            return _Resp(200, SITE.get("sitemaps", {}).get(source["company"], "<urlset></urlset>"))
    raise AssertionError(f"unexpected fetch: {url}")


def fake_post(url, data=None, headers=None, json=None, timeout=None):
    if json is not None:
        # ntfy parses a JSON body only at its root URL, and only with the topic inside it. Posted to the
        # topic URL instead, the JSON would arrive as plain text with no title, priority or link.
        assert url == notifier.NTFY_BASE, f"JSON published to {url}"
        assert json.get("topic") == notifier.NTFY_TOPIC, f"JSON publish without the topic: {json}"
        push = {"kind": "apple", "title": json["title"], "body": json["message"], "priority": json["priority"],
                "click": json["click"], "tags": json["tags"]}
        if NTFY["apple_status"] != 200:
            return _Resp(NTFY["apple_status"])
        ORDER.append("apple-push")
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
    if json is not None and NTFY.get("no_id"):
        return _Resp(200, "{}")
    return _Resp(200, _dumps({"id": push["id"], "event": "message"}))


requests.get, requests.post = fake_get, fake_post
notifier.NTFY_TOPIC = "test-topic"
cs.DETAIL_GAP_S = 0
aw.DRILL_RETRY_GAP_S = 0
aw.DELIVERY_POLL_WAITS_S = tuple(0 for _ in aw.DELIVERY_POLL_WAITS_S)   # as many read-backs, no waiting

STORE: dict[str, dict] = {}
STATE: dict[str, str] = {}
# on: every bot_state read fails. keys: reads of these keys fail. fail_reads: {key: n} fails the next n
# reads of key. drop_writes: writes to these keys are lost (db.set_state swallows the error).
STATE_DOWN = {"on": False, "keys": set(), "fail_reads": {}, "drop_writes": set()}
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


def _state_fails(key):
    if STATE_DOWN["on"] or key in STATE_DOWN["keys"]:
        return True
    if STATE_DOWN["fail_reads"].get(key):
        STATE_DOWN["fail_reads"][key] -= 1
        return True
    return False


def read_state(key):
    if _state_fails(key):
        raise db.StateUnavailable(key)
    return STATE.get(key)


def set_state(key, value):
    if key not in STATE_DOWN["drop_writes"]:
        STATE[key] = value


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


def get_state(key):
    """db.get_state reports a failed read as a missing key."""
    return None if (STATE_DOWN["on"] or key in STATE_DOWN["keys"]) else STATE.get(key)


cs.get_state, cs.set_state, cs.find_unknown_candidates = get_state, set_state, fake_unknown
aw.get_state, aw.set_state, aw.read_state = get_state, set_state, read_state
aw.clear_state = cs.clear_state = lambda key: STATE.pop(key, None)
aw.get_client = _Query
aw.classify = main.classify = fake_classify
main.get_job_row = lambda jid: ({k: STORE[jid][k] for k in ("id", "tier", "status")} if jid in STORE else None)
main.insert_job = w.insert_job = fake_insert
main.claim_notification = fake_claim
w.find_unknown_candidates = fake_unknown
w.start_run = lambda source="ats": 1
w.finish_run = lambda run_id, **kw: None
w.fetch_all_listings = lambda cfg: (ORDER.append("boards"), [])[1]
_real_collect_listings = cs.collect_listings


def fake_collect(force=False, apple_sitemap=None):
    """Like the real collect_listings, read as if the hourly sitemap were due on every pass: a row comes
    back only while its id is not stored, and apple_sitemap["internships"] gets every Apple internship
    title, stored or not (the same dicts as the returned rows, as in the real one) -- unless Apple's
    sitemap could not be fetched (SITE["apple_sitemap_down"]), when the key is not set at all."""
    if SITE.get("sitemap_not_due"):
        return []
    rows = [dict(r) for r in SITEMAP_ROWS if r["id"] not in STORE]
    if apple_sitemap is not None and not SITE.get("apple_sitemap_down"):
        by_id = {r["id"]: r for r in rows}
        apple_sitemap["internships"] = [by_id.get(r["id"], dict(r)) for r in SITEMAP_ROWS
                                        if r["company"] == "Apple" and cs.INTERN_TITLE.search(r["title"])]
    return rows


_real_apple_sitemap_internships = cs.apple_sitemap_internships
cs.collect_listings = fake_collect
cs.apple_sitemap_internships = lambda: (None if SITE.get("apple_sitemap_down") else
                                        [dict(r) for r in SITEMAP_ROWS if cs.INTERN_TITLE.search(r["title"])])


def reset(records=REAL, stored=REAL, applied=(SWE_TITLE,)):
    """A world where Apple lists `records` and the tracker already holds `stored` (found Oct 6)."""
    for d in (STORE, STATE, SITE["status"]):
        d.clear()
    SITE["students_seq"], SITE["detail"] = [], None
    CLAIMED.clear()
    SITEMAP_ROWS.clear()
    QUERIES.clear()
    STATE_DOWN.update(on=False, keys=set(), fail_reads={}, drop_writes=set())
    SITE["apple_sitemap_down"] = SITE["sitemap_not_due"] = False
    os.environ.pop("GITHUB_EVENT_NAME", None)
    os.environ.pop("GITHUB_OUTPUT", None)
    NTFY.update(apple_status=200, canary_status=200, feed_drop=False, feed_lag_polls=0, polls=0, no_id=False)
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


def sitemap_row(title, rid):
    """A sitemap stub as collect_listings makes one: title from the URL slug, location the country."""
    url = f"https://jobs.apple.com/en-us/details/{rid}/{re.sub(r'[^a-z0-9]+', '-', title.lower())}"
    job = cs._make_job("Apple", title, "United States", url, None, None)
    job["search_term"] = "career-sitemap"
    return job


# Apple's sitemap on 2026-10-30, the internship part of it: Feb 2026's Finance Development Program in two
# cities (not on the internships page), and last year's pools.
SITEMAP_TODAY = ([sitemap_row("Finance Development Program 2027 Internship Opportunities", "200646124-0157"),
                  sitemap_row("Finance Development Program 2027 Internship Opportunities", "200646124-0836")]
                 + [sitemap_row(t, f"20060{6140 + i}-3810") for i, t in enumerate(
                     ["Software Engineering Internships", "Machine Learning AI Internships",
                      "Hardware Engineering Internships", "MBA Internships Summer"])]
                 + [sitemap_row("Senior Software Engineer", "200712399-3810")])


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
# Today's sitemap, with one city of the Finance Development Program stored and the other not -- the
# state the bot is in now (the second city shares its title key, so it is never stored).
SITEMAP_ROWS[:] = SITEMAP_TODAY
store_row(SITEMAP_TODAY[0])
for r in SITEMAP_TODAY[2:]:
    store_row(r)
pushes = run_pass()
check("the first pass after deploy pushes nothing, not even the never-stored second city",
      kinds(pushes, "apple") == [], str([p["title"] for p in kinds(pushes, "apple")]))
check("...because the sitemap's internship titles are recorded the moment the ledger is",
      {"200646124", "200606140", "200606143"} <= ledger() and json.loads(STATE[aw.LEDGER_KEY])["sitemap_seeded"] is True)
check("...but never its full-time roles (the sitemap adds dozens a day; the ledger is read every pass)",
      "200712399" not in ledger())
check("the pass after is quiet too", kinds(run_pass(), "apple") == [])

SITEMAP_ROWS[:] = SITEMAP_TODAY + [sitemap_row("Software Engineering Internships Summer", "200712351-3810"),
                                   sitemap_row("Internal Communications Strategist", "200712352-3810"),
                                   sitemap_row("Hardware Phd Internships", "200712353-3810")]
pushes = run_pass()
apple = kinds(pushes, "apple")
check("a new internship title there pushes 🍎", [p["title"] for p in apple] == ["🍎 APPLE - Software Engineering Internships Summer"],
      str([p["title"] for p in apple]))
check("...with the verdict it was given this pass, not a stored one",
      apple and "Bot's verdict: APPLY — Undergrad CS internship, Summer 2027" in apple[0]["body"]
      and "(rated " not in apple[0]["body"], apple[0]["body"] if apple else "")
check("a PhD pool there is recorded without a push", "200712353" in ledger())
check("'Internal' is not 'intern': never recorded", "200712352" not in ledger())

reset()
SITE["sitemap_not_due"] = True
SITEMAP_ROWS[:] = SITEMAP_TODAY
run_pass()
check("seeded from the sitemap even on a pass when the hourly sitemap read is not due (no window between the seeds)",
      "200646124" in ledger() and json.loads(STATE[aw.LEDGER_KEY])["sitemap_seeded"] is True)
SITE["sitemap_not_due"] = False
check("...so its first hourly read is quiet", kinds(run_pass(), "apple") == [])

print("\n-- Apple's sitemap unavailable when the ledger is created --")
reset()
SITE["apple_sitemap_down"] = True
SITEMAP_ROWS[:] = SITEMAP_TODAY
for r in SITEMAP_TODAY:
    store_row(r)
pushes = run_pass()
check("the internships page seeds alone, and says the sitemap still has to be",
      len(ledger()) == 24 and json.loads(STATE[aw.LEDGER_KEY])["sitemap_seeded"] is False and kinds(pushes, "apple") == [])
SITE["apple_sitemap_down"] = False
pushes = run_pass()
check("the first read that works seeds the sitemap, silently", kinds(pushes, "apple") == []
      and "200646124" in ledger() and json.loads(STATE[aw.LEDGER_KEY])["sitemap_seeded"] is True, str(pushes))

print("\n-- a sitemap with no internship titles at all is still a read --")
reset()
pushes = run_pass()
check("seeded with nothing from the sitemap, and marked seeded", json.loads(STATE[aw.LEDGER_KEY])["sitemap_seeded"] is True)
reset()
SITE["apple_sitemap_down"] = True
run_pass()
SITE["apple_sitemap_down"] = False
run_pass()
check("...also when that empty read comes after a failed one", json.loads(STATE[aw.LEDGER_KEY])["sitemap_seeded"] is True)
SITEMAP_ROWS[:] = [sitemap_row("Treasury Internships Summer", "200712361-3810")]
apple = kinds(run_pass(), "apple")
check("so the first internship it later lists pushes, rather than being recorded silently",
      [p["title"] for p in apple] == ["🍎 APPLE - Treasury Internships Summer"], str([p["title"] for p in apple]))

print("\n-- the real sitemap reader hands over every Apple internship title, stored or not --")
reset()
SITE["sitemaps"] = {"Apple": "<urlset>" + "".join(f"<url><loc>{r['url']}</loc></url>" for r in SITEMAP_TODAY)
                    + "<url><loc>https://jobs.apple.com/en-us/details/200712352-3810/internal-communications-strategist"
                      "</loc></url></urlset>"}
store_row(SITEMAP_TODAY[0])
handed: dict = {}
_saved_due = cs._due
cs._due = lambda now: True
rows = _real_collect_listings(apple_sitemap=handed)
names = [j["title"] for j in handed.get("internships", [])]
check("both cities of the Finance Development Program, though one is stored",
      {SITEMAP_TODAY[0]["id"], SITEMAP_TODAY[1]["id"]} <= {j["id"] for j in handed.get("internships", [])}, str(names))
check("no full-time role and no 'Internal' title", not any(t in n for n in names
      for t in ("Senior Software Engineer", "Internal Communications")), str(names))
check("...while the rows for the tracker are still only the unstored ones", SITEMAP_TODAY[0]["id"] not in
      {j["id"] for j in rows} and SITEMAP_TODAY[1]["id"] in {j["id"] for j in rows})
check("the one-off reader for the ledger's creation gives the same list",
      sorted(j["id"] for j in _real_apple_sitemap_internships()) == sorted(j["id"] for j in handed["internships"]))
SITE["sitemaps"] = {"Apple": None}
_orig_get = requests.get
requests.get = lambda url, **kw: (_ for _ in ()).throw(requests.ConnectionError("down")) if "sitemap" in url else _orig_get(url, **kw)
handed = {}
_real_collect_listings(apple_sitemap=handed)
check("a sitemap that cannot be fetched is not 'no internships': nothing is handed over", "internships" not in handed)
check("...and the one-off reader says so with None", _real_apple_sitemap_internships() is None)
requests.get = _orig_get
SITE["sitemaps"] = {"Apple": "<html><body>Please verify you are a human</body></html>"}
handed = {}
_real_collect_listings(apple_sitemap=handed)
check("a 200 listing no job pages at all (a challenge page) is unread too, not 'no internships'",
      "internships" not in handed and _real_apple_sitemap_internships() is None)
cs._due = _saved_due
SITE.pop("sitemaps")

print("\n-- a sitemap push that fails is sent at the next hourly read --")
reset()
SITEMAP_ROWS[:] = list(SITEMAP_TODAY)
run_pass()
SITEMAP_ROWS.append(sitemap_row("Treasury Internships Summer", "200712357-3810"))
NTFY["apple_status"] = 500
pushes = run_pass()
check("ntfy refuses: nothing recorded, though the row is now stored", kinds(pushes, "apple") == []
      and "200712357" not in ledger() and id_of(SITEMAP_ROWS[-1]["url"]) in STORE)
NTFY["apple_status"] = 200
pushes = run_pass()
apple = kinds(pushes, "apple")
check("the next read pushes it (the stored row still reaches the alert)", len(apple) == 1
      and "Treasury" in apple[0]["title"], str([p["title"] for p in apple]))
check("...once", kinds(run_pass(), "apple") == [])
SITEMAP_ROWS.clear()

print("\n-- a board sweep that crashes still lets the sitemap push and the shadow watch run --")
reset()
SITEMAP_ROWS[:] = list(SITEMAP_TODAY)
run_pass()
SITEMAP_ROWS.append(sitemap_row("Treasury Internships Summer", "200712362-3810"))
STATE.pop(aw.SHADOW_CHECKED_KEY, None)
_orig_process, _orig_fetch = w.process_job, w.fetch_all_listings
w.fetch_all_listings = lambda cfg: [{"id": "ats:board1", "company": "Acme", "title": "Software Engineer Intern",
                                     "location": "Atlanta", "url": "https://acme.example/1"}]
w.process_job = lambda job: (_ for _ in ()).throw(RuntimeError("star rules missing"))
STATE[cs.APPLE_STUDENTS_STATE_KEY] = (_now() - timedelta(minutes=5)).isoformat()
for lst in (FETCHED, ORDER, PUSHES, CLASSIFIED):
    lst.clear()
try:
    w.run()
    raised = False
except RuntimeError:
    raised = True
w.process_job, w.fetch_all_listings = _orig_process, _orig_fetch
check("the 🍎 push for the sitemap internship still goes", [p["title"] for p in kinds(PUSHES, "apple")]
      == ["🍎 APPLE - Treasury Internships Summer"], str(PUSHES))
check("...the shadow watch still runs", aw.SHADOW_KEY in STATE)
check("...and the run still fails, as a crash always has", raised)
SITEMAP_ROWS.clear()

print("\n-- a first pass where only the sitemap can be read does not seed --")
reset()
SITE["status"]["students"] = 403
SITEMAP_ROWS[:] = SITEMAP_TODAY
pushes = run_pass()
check("internships page refused, sitemap read: nothing recorded, nothing pushed",
      aw.LEDGER_KEY not in STATE and kinds(pushes, "apple") == [], str(pushes))
SITE["status"].clear()
pushes = run_pass(full_read_age_min=None)
check("the next good pass seeds from the internships page and the sitemap, still pushing nothing",
      len(ledger()) > 24 and kinds(pushes, "apple") == [], f"{len(ledger())} {pushes}")
SITEMAP_ROWS.clear()

reset()
SITEMAP_ROWS[:] = list(SITEMAP_TODAY)
run_pass()
both = new_record("Software Engineering Internships – Summer 2028", "200712355-3810")
SITE["students"] = students_pages([both] + REAL)
SITEMAP_ROWS.append(dict(sitemap_row("Software Engineering Internships Summer", "200712355-3810"), url=url_of(both),
                         apply_url=url_of(both), id=id_of(url_of(both))))
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
run_pass()
_orig_unknown = cs.find_unknown_candidates
cs.find_unknown_candidates = lambda jobs, batch_size=5000: (_ for _ in ()).throw(RuntimeError("dedup down"))
pushes = run_pass()
check("the boards are still swept", "boards" in ORDER, str(ORDER))
check("...and a reader that crashes counts as one that cannot read: no alarm on the first", kinds(pushes, "canary") == [])
canary = kinds(run_pass(), "canary")
cs.find_unknown_candidates = _orig_unknown
check("...one on the second, naming only the error type", len(canary) == 1
      and "reader error (RuntimeError)" in canary[0]["body"] and "dedup down" not in canary[0]["body"], str(canary))

print("\n-- the reader survives surprises in Apple's data --")
odd = _with(P1, lambda d: d["loaderData"]["search"]["searchResults"].__setitem__(
    slice(0, 2), [None, dict(d["loaderData"]["search"]["searchResults"][1], locations=["Austin"])]))
try:
    got, _ = cs.parse_apple_search(odd)
except Exception as exc:
    got = [repr(exc)]
check("a null result is skipped, and a location given as plain text is read",
      len(got) == 19 and isinstance(got[0], dict) and got[0]["location"] == "Austin", f"{len(got)} {got[:1]}")
reset()
run_pass()
SITE["students"] = students_pages([dict(r, postDateInGMT={"value": r["postDateInGMT"]}) for r in REAL])
try:
    pushes = run_pass()
    health = STATE.get(aw.HEALTH_KEY)
except Exception as exc:
    pushes, health = [repr(exc)], "raised"
check("a posting date sent as an object is no date, not a crash (it is compared to order page 1)",
      health is None and kinds([p for p in pushes if isinstance(p, dict)], "canary") == [], f"{health} {pushes}")
SITE["students"] = {1: _with(P1, lambda d: d["loaderData"]["search"].update(totalRecords="25+"))}
r = cs.read_apple_pages(cs.APPLE_STUDENTS_URL, 1, 1, expect_team="INTRN")
check("a posting count that is not a number is unreadable, not a crash",
      r.outcome == "unreadable" and "not a number" in r.problem, str(r))
SITE["students"] = students_pages(REAL)
_orig_parse = cs.parse_apple_search
cs.parse_apple_search = lambda page, expect_team=None: (_ for _ in ()).throw(TypeError("surprise"))
try:
    r = cs.read_apple_pages(cs.APPLE_STUDENTS_URL, 1, 1)
except Exception as exc:
    r = cs.AppleRead(outcome="escaped", problem=repr(exc))
check("any other parser error is 'unreadable' too, naming only its type (the logs are public)",
      r.outcome == "unreadable" and r.problem == "parser error (TypeError)", str(r))
try:
    run_pass()
    canary = kinds(run_pass(), "canary")
except Exception as exc:
    canary = [repr(exc)]
check("...so the two-in-a-row alarm still fires", len(canary) == 1 and isinstance(canary[0], dict)
      and "can't read" in canary[0]["body"], str(canary))
try:
    aw.shadow_check(now=time.time() + 7200)
    shadow_ok = aw.SHADOW_KEY in STATE and aw.DRILL_KEY in STATE    # written only at its end
except Exception as exc:
    shadow_ok = False
check("...and the shadow watch still reaches its own checks", shadow_ok)
cs.parse_apple_search = _orig_parse

reset()
run_pass()
SITE["students"] = students_pages([new_record("Software Engineering Internships – Summer 2028", "200712358-3810")] + REAL)
SITE["detail"] = _with(D_SWE, lambda d: d["loaderData"]["jobDetails"]["jobsData"].update(
    minimumQualifications=["Pursuing a BS", "Python"]))
pushes = run_pass()
check("a posting page Apple changed: the posting is still classified (on its title) and pushed",
      len(kinds(pushes, "apple")) == 1 and CLASSIFIED and CLASSIFIED[-1].endswith("Summer 2028"), str(pushes))

print("\n-- Apple dropping the internships filter (it answers an unknown team with every U.S. job) --")
reset()
run_pass()
SITE["students"] = {1: ALL_US}
ledger_before = ledger()
pushes = run_pass()
check("the page reads as unreadable, so none of its 20 new full-time roles is pushed as an internship",
      kinds(pushes, "apple") == [] and ledger() == ledger_before, str([p["title"] for p in kinds(pushes, "apple")]))
canary = kinds(run_pass(), "canary")
check("...and the second such read alarms", len(canary) == 1 and "INTRN team filter was not applied" in canary[0]["body"],
      str(canary))
SITE["students"] = {1: _with(P1, lambda d: d["loaderData"]["search"].update(totalRecords=4483))}
r2 = cs.read_apple_pages(cs.APPLE_STUDENTS_URL, 1, 1, expect_team="INTRN")
check("a filtered page claiming thousands of postings is unreadable too", r2.outcome == "unreadable"
      and "too many" in r2.problem, str(r2))
check("every U.S. job, which has no team filter, still reads (the shadow watch's page)",
      cs.parse_apple_search(ALL_US)[1] > 1000)

print("\n-- page 2 refused on the hourly read --")
reset()
run_pass()
SITE["status"][("students", 2)] = 403
pushes = run_pass(full_read_age_min=61)
check("is not an outage: page 1, which finds new postings, was read", aw.HEALTH_KEY not in STATE
      and kinds(pushes, "canary") == [], STATE.get(aw.HEALTH_KEY, ""))
check("...and is tried again at the next hourly slot, not on every pass",
      (_now() - datetime.fromisoformat(STATE[cs.APPLE_STUDENTS_FULL_STATE_KEY])).total_seconds() < 60)
run_pass(full_read_age_min=None)
check("the pass after reads page 1 only", searches() == [cs.APPLE_STUDENTS_URL], str(searches()))

print("\n-- page 2 refused while the ledger is being seeded is an outage --")
reset()
SITE["status"][("students", 2)] = 403
pushes = run_pass()
check("recorded as a failure, naming the page", aw.LEDGER_KEY not in STATE
      and "page 2 or later: HTTP 403" in STATE.get(aw.HEALTH_KEY, ""), STATE.get(aw.HEALTH_KEY, ""))
h = json.loads(STATE[aw.HEALTH_KEY])
h["failing_since"] -= 3660
STATE[aw.HEALTH_KEY] = json.dumps(h)
canary = kinds(run_pass(full_read_age_min=None), "canary")
check("an hour of it: alarm (until it is seeded, no 🍎 push can go out)", len(canary) == 1, str(canary))

reset(records=REAL[:1], stored=REAL[:1])
run_pass()
launch = [new_record(f"Software Engineering Internships – Summer 2028 Team {i}", f"5{i:08d}-3810") for i in range(22)]
SITE["students"] = students_pages(launch + REAL[:1])
SITE["status"][("students", 2)] = 403
run_pass()
check("a burst that runs onto a refused page 2 is recorded as a failure too",
      "page 2 or later" in STATE.get(aw.HEALTH_KEY, ""), STATE.get(aw.HEALTH_KEY, ""))

print("\n-- an empty page --")
reset()
run_pass()
SITE["students"] = students_pages([])
check("one empty read: no alarm", kinds(run_pass(), "canary") == [])
canary = kinds(run_pass(), "canary")
check("two: one alarm (Apple has listed 25 for months; an empty page is the 2026-10-29 symptom)",
      len(canary) == 1 and "lists no postings" in canary[0]["body"], str(canary))
reset()
run_pass()
SITE["status"]["students"] = 403
run_pass()
h = json.loads(STATE[aw.HEALTH_KEY])
h["failing_since"] -= 3660
STATE[aw.HEALTH_KEY] = json.dumps(h)
check("refused for an hour: alarm", len(kinds(run_pass(), "canary")) == 1)
SITE["status"].clear()
SITE["students"] = students_pages([])
check("then an empty page: no false 'readable again'", kinds(run_pass(), "canary") == [] and aw.HEALTH_KEY in STATE)
SITE["students"] = students_pages(REAL)
canary = kinds(run_pass(), "canary")
check("then the real page: the all-clear", len(canary) == 1 and "readable again" in canary[0]["body"], str(canary))

print("\n-- an alarm ntfy did not take is sent again --")
reset()
run_pass()
SITE["status"]["students"] = 403
run_pass()
h = json.loads(STATE[aw.HEALTH_KEY])
h["failing_since"] -= 3660
STATE[aw.HEALTH_KEY] = json.dumps(h)
NTFY["canary_status"] = 503
run_pass()
check("the alarm push fails: not marked sent", not json.loads(STATE[aw.HEALTH_KEY]).get("alerted"))
NTFY["canary_status"] = 200
canary = kinds(run_pass(), "canary")
check("next pass: sent", len(canary) == 1 and "turned away" in canary[0]["body"], str(canary))

print("\n-- a bot_state blip never restarts an alarm clock --")
reset()
run_pass()
SITE["status"]["students"] = 403
run_pass()
before = STATE[aw.HEALTH_KEY]
STATE_DOWN["on"] = True
run_pass()
STATE_DOWN["on"] = False
check("the failure record is left exactly as it was", STATE[aw.HEALTH_KEY] == before, f"{before} -> {STATE[aw.HEALTH_KEY]}")
SITE["status"].clear()
t_s = time.time() + 10 * 3600
STATE.pop(aw.SHADOW_CHECKED_KEY, None)
aw.shadow_check(now=t_s)
shadow_before = STATE[aw.SHADOW_KEY]
STATE_DOWN["on"] = True
FETCHED.clear()
aw.shadow_check(now=t_s + 3700)
STATE_DOWN["on"] = False
check("a shadow check that cannot read its record skips the hour, untouched, and retries next pass",
      STATE[aw.SHADOW_KEY] == shadow_before and FETCHED == [] and
      datetime.fromisoformat(STATE[aw.SHADOW_CHECKED_KEY]).timestamp() < t_s + 3700)

print("\n-- 'applied' means any status at or past applied --")
reset(applied=())
STORE[id_of(url_of(next(r for r in REAL if r["postingTitle"] == SWE_TITLE)))]["status"] = "rejected"
run_pass()
SITE["students"] = students_pages([new_record(SWE_TITLE, "200712359-3810")] + REAL)
apple = kinds(run_pass(), "apple")
check("an earlier posting marked rejected still counts as applied to", apple and
      "You applied to an earlier posting of this title" in apple[0]["body"], apple[0]["body"] if apple else "")

print("\n-- a grouped push that fails is sent again --")
reset(records=REAL[:1], stored=REAL[:1])
run_pass()
launch = [new_record(f"Software Engineering Internships – Summer 2028 Team {i}", f"4{i:08d}-3810")
          for i in range(aw.GROUPED_PUSH_OVER + 2)]
SITE["students"] = students_pages(launch + REAL[:1])
NTFY["apple_status"] = 500
run_pass()
check("ntfy refuses the burst's push: none of it recorded", not {r["id"].split("-")[0] for r in launch} & ledger())
NTFY["apple_status"] = 200
apple = kinds(run_pass(), "apple")
check("next pass: one grouped push", len(apple) == 1 and apple[0]["title"].endswith(f"{len(launch)} new internship postings"),
      str([p["title"] for p in apple]))
check("...and all of it recorded", {r["id"].split("-")[0] for r in launch} <= ledger())

print("\n-- the ledger lost while the hourly read is fresh --")
reset()
run_pass()
STATE[aw.LEDGER_KEY] = "{not json"
run_pass(full_read_age_min=10)
check("asks for a full read anyway, and seeds from every page", len(searches()) == 2 and len(ledger()) == 24,
      f"{searches()} {len(ledger())}")
SITE["students"] = students_pages([new_record("Software Engineering Internships – Summer 2028", "200712360-3810")] + REAL)
check("...and the next new posting pushes", len(kinds(run_pass(), "apple")) == 1)

print("\n-- the sitemap seed, when Apple's sitemap is down at the ledger's creation --")
reset()
SITE["apple_sitemap_down"] = True
SITEMAP_ROWS[:] = list(SITEMAP_TODAY)
run_pass()
check("the page seeds alone", json.loads(STATE[aw.LEDGER_KEY])["sitemap_seeded"] is False)
fresh_one = new_record("Software Engineering Internships – Summer 2028", "200712363-3810")
SITE["students"] = students_pages([fresh_one] + REAL)
SITEMAP_ROWS.append(dict(sitemap_row("Software Engineering Internships Summer", "200712363-3810"), url=url_of(fresh_one),
                         apply_url=url_of(fresh_one), id=id_of(url_of(fresh_one))))
SITE["apple_sitemap_down"] = False
NTFY["apple_status"] = 500
pushes = run_pass()
check("the next pass seeds the sitemap late, but not over a posting the page has yet to push (its push refused)",
      json.loads(STATE[aw.LEDGER_KEY])["sitemap_seeded"] is True and "200712363" not in ledger()
      and "200646124" in ledger())
NTFY["apple_status"] = 200
apple = kinds(run_pass(), "apple")
check("...so it is pushed once ntfy takes it", [p["title"] for p in apple] == [f"🍎 APPLE - {fresh_one['postingTitle']}"],
      str([p["title"] for p in apple]))
SITEMAP_ROWS.clear()

reset()
SITE["apple_sitemap_down"] = True
SITEMAP_ROWS[:] = list(SITEMAP_TODAY)
run_pass()
SITE["status"]["students"] = 403
SITE["apple_sitemap_down"] = False
pushes = run_pass()
check("while the page is refused, the hourly sitemap read records nothing on its own",
      json.loads(STATE[aw.LEDGER_KEY])["sitemap_seeded"] is False and "200646124" not in ledger()
      and kinds(pushes, "apple") == [], str(pushes))
SITEMAP_ROWS.clear()

print("\n-- posting times without a time zone --")
reset()
run_pass()
naive = new_record("Software Engineering Internships – Summer 2028", "200712364-3810")
naive["postDateInGMT"] = (_now() - timedelta(minutes=4, seconds=10)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
SITE["students"] = students_pages([naive] + REAL)
apple = kinds(run_pass(), "apple")
check("are taken as UTC: the push goes out, with its posted time", apple and "Posted 4 min ago" in apple[0]["body"],
      apple[0]["body"] if apple else str(STATE.get(aw.LEDGER_KEY, ""))[:80])
reset()
run_pass()
SITE["students"] = students_pages([new_record("Software Engineering Internships – Summer 2028", "200712365-3810")] + REAL)
_orig_describe = aw.describe
aw.describe = lambda *a, **k: (_ for _ in ()).throw(TypeError("surprise"))
apple = kinds(run_pass(), "apple")
aw.describe = _orig_describe
check("a push whose details cannot be built still goes out, plain", len(apple) == 1
      and "Details unavailable" in apple[0]["body"] and "200712365" in ledger(), str(apple))

print("\n-- a burst whose page 2 stays refused --")
reset(records=REAL[:1], stored=REAL[:1])
run_pass()
launch = [new_record(f"Software Engineering Internships – Summer 2028 Team {i}", f"6{i:08d}-3810") for i in range(23)]
SITE["students"] = students_pages(launch + REAL[:1])
SITE["status"][("students", 2)] = 403
run_pass()
check("the burst's tail is remembered as unread", STATE.get(cs.APPLE_STUDENTS_PAGE2_KEY) == "1")
run_pass(full_read_age_min=None)
check("...so the next pass, though no longer a burst, reads page 2 again and still records the failure",
      len(searches()) == 2 and "page 2 or later" in STATE.get(aw.HEALTH_KEY, ""), f"{searches()} {STATE.get(aw.HEALTH_KEY)}")
h = json.loads(STATE[aw.HEALTH_KEY])
h["failing_since"] -= 3660
STATE[aw.HEALTH_KEY] = json.dumps(h)
check("an hour of it: alarm", len(kinds(run_pass(full_read_age_min=None), "canary")) == 1)
SITE["status"].clear()
pushes = run_pass(full_read_age_min=None)
tail = {r["id"].split("-")[0] for r in launch[20:]}
check("page 2 readable again: the tail is pushed, and the flag cleared", tail <= ledger()
      and cs.APPLE_STUDENTS_PAGE2_KEY not in STATE and len(kinds(pushes, "apple")) == len(tail), str(kinds(pushes, "apple")))

print("\n-- page 2 that loads but lists nothing, while seeding --")
reset()
SITE["students"] = {1: students_pages(REAL)[1], 2: _with(P2, lambda d: d["loaderData"]["search"].update(searchResults=[]))}
run_pass()
check("is a failure, named with what was read", "page 2 or later: 20 of 25 postings read" in STATE.get(aw.HEALTH_KEY, ""),
      STATE.get(aw.HEALTH_KEY, ""))
canary = kinds(run_pass(full_read_age_min=None), "canary")
check("...and the second such pass alarms (until it is seeded, no 🍎 push can go out)", len(canary) == 1, str(canary))

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
NTFY["canary_status"] = 503
aw.shadow_check(now=t_alarm)
check("48 hours with nothing new, but ntfy refuses the alarm: not marked sent",
      kinds(PUSHES, "canary") == [] and not json.loads(STATE[aw.SHADOW_KEY]).get("alerted"))
NTFY["canary_status"] = 200
t_alarm += hour
aw.shadow_check(now=t_alarm)
SITE["status"].clear()
canary = kinds(PUSHES, "canary")
check("...the next hour: one alarm", len(canary) == 1 and "48 hours" in canary[0]["body"], str(canary))
aw.shadow_check(now=t_alarm + hour)
check("an hour later: no repeat", len(kinds(PUSHES, "canary")) == 1)
SITE["all_us"] = with_new_on_all_us([5], t_alarm + 2 * hour, 300000100)
NTFY["canary_status"] = 503
aw.shadow_check(now=t_alarm + 2 * hour)
check("a new posting, but the all-clear is refused: still alerted, so it is sent later",
      json.loads(STATE[aw.SHADOW_KEY]).get("alerted") is True)
NTFY["canary_status"] = 200
SITE["all_us"] = with_new_on_all_us([3], t_alarm + 3 * hour, 300000200)
aw.shadow_check(now=t_alarm + 3 * hour)
canary = kinds(PUSHES, "canary")
check("...the next new posting: one all-clear", len(canary) == 2 and "showing new postings again" in canary[1]["body"],
      str(canary))
check("...and the alarm is re-armed", not json.loads(STATE[aw.SHADOW_KEY]).get("alerted"))
SITE["all_us"] = ALL_US

# ═════════════════════════════════════════════════════════════════════════════
print("\n-- the weekly drill --")
reset()
run_pass()
VERDICT.update(tier="APPLY_CAVEAT", reason="Undergrad pool; no term listed")
store_before, state_before = copy.deepcopy(STORE), copy.deepcopy(STATE)
for lst in (PUSHES, CLASSIFIED, FETCHED):
    lst.clear()
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
check("writes nothing to the tracker", STORE == store_before)
check("...and in bot_state only its pass stamp", {k: v for k, v in STATE.items() if k != aw.DRILL_KEY}
      == {k: v for k, v in state_before.items() if k != aw.DRILL_KEY}
      and "passed_at" in json.loads(STATE.get(aw.DRILL_KEY, "{}")))

reset()
run_pass()
SITE["students_seq"] = [403]
PUSHES.clear()
check("one odd answer from Apple: retried, and the drill passes", aw.drill() == 0
      and kinds(PUSHES, "canary") == [] and len(kinds(PUSHES, "apple")) == 1, str(PUSHES))

reset()
run_pass()
SITE["status"]["students"] = 403
PUSHES.clear()
rc = aw.drill()
canary = kinds(PUSHES, "canary")
check("Apple refusing every try: it fails, and says where", rc == 1 and len(canary) == 1
      and "drill FAILED at reading Apple's internships page (HTTP 403)" in canary[0]["body"]
      and not kinds(PUSHES, "apple"), str(PUSHES))

reset()
run_pass()
NTFY["feed_lag_polls"] = 1
PUSHES.clear()
check("ntfy's feed a moment behind (its cache is written in batches): polled again, passes", aw.drill() == 0
      and kinds(PUSHES, "canary") == [], str(kinds(PUSHES, "canary")))

reset()
run_pass()
NTFY["feed_drop"] = True
PUSHES.clear()
rc = aw.drill()
canary = kinds(PUSHES, "canary")
check("ntfy taking the push but never holding it: it fails at delivery", rc == 1 and canary
      and "confirming delivery" in canary[-1]["body"], str(canary))

reset()
run_pass()
STATE[cs.APPLE_STUDENTS_STATE_KEY] = (_now() - timedelta(hours=2)).isoformat()
PUSHES.clear()
rc = aw.drill()
canary = kinds(PUSHES, "canary")
check("the 5-minute check not running: it fails, and says so, instead of 'the Apple alert works'",
      rc == 1 and canary and "checking the 5-minute Apple check (it last ran 120 minutes ago)" in canary[0]["body"]
      and not kinds(PUSHES, "apple"), str(PUSHES))

reset()
run_pass()
del STATE[aw.LEDGER_KEY]
PUSHES.clear()
check("the 5-minute check running but never having recorded Apple's postings: it fails",
      aw.drill() == 1 and "has not recorded Apple's postings" in kinds(PUSHES, "canary")[0]["body"])

reset()
run_pass()
STATE_DOWN["on"] = True
PUSHES.clear()
rc = aw.drill()
STATE_DOWN["on"] = False
check("the bot's state unreadable: it fails, and says so", rc == 1
      and "reading the bot's state" in kinds(PUSHES, "canary")[0]["body"])

def drill_fails(label, expect):
    """The drill fails with a canary containing expect, and records no pass."""
    PUSHES.clear()
    try:
        rc = aw.drill()
    except Exception as exc:
        rc = f"raised {type(exc).__name__}"
    canary = kinds(PUSHES, "canary")
    check(label, rc == 1 and canary and expect in canary[-1]["body"] and aw.DRILL_KEY not in STATE,
          f"rc={rc} {[c['body'][:120] for c in canary]} stamp={STATE.get(aw.DRILL_KEY)}")


reset()
run_pass()
SITE["detail"] = _with(D_SWE, lambda d: d["loaderData"]["jobDetails"]["jobsData"].update(minimumQualifications=["a", "b"]))
drill_fails("the posting's page unreadable on every try: fails, and says so", "its page is unreadable (TypeError)")

reset()
run_pass()
_orig_get = requests.get
_detail_tries = {"n": 0}


def _flaky_detail(url, **kw):
    if "/en-us/details/" in url:
        _detail_tries["n"] += 1
        if _detail_tries["n"] == 1:
            return _Resp(200, "<html><body>Loading</body></html>")
    return _orig_get(url, **kw)


requests.get = _flaky_detail
PUSHES.clear()
rc = aw.drill()
requests.get = _orig_get
check("the posting's page empty once (a 200 with nothing in it): retried, passes", rc == 0 and _detail_tries["n"] == 2,
      f"rc={rc} tries={_detail_tries['n']}")

reset()
run_pass()
NTFY["no_id"] = True
drill_fails("ntfy answering without a message id: fails, since delivery cannot be checked", "ntfy gave no message id")

reset()
run_pass()
SITE["students"] = students_pages([new_record("Software Engineer, Apple Pay", "200799001-3810")]
                                  + [r for r in REAL if r["postingTitle"] != SWE_TITLE])
PUSHES.clear()
rc = aw.drill()
apple = kinds(PUSHES, "apple")
check("without the usual posting, the drill picks an internship, never a full-time role",
      rc == 0 and apple and "Internship" in apple[0]["title"] and "Apple Pay" not in apple[0]["title"],
      str([p["title"] for p in apple]))

reset()
run_pass()
STATE[aw.SHADOW_CHECKED_KEY] = (_now() - timedelta(hours=4)).isoformat()
drill_fails("the passes dying before their end (no shadow check in 4 hours): fails", "passes stop before")
reset()
run_pass()
STATE[aw.HEALTH_KEY] = json.dumps({"failing_since": time.time() - 7200, "problem": "refused: HTTP 403", "alerted": True})
drill_fails("production in a declared outage: fails, though the drill's own read got through",
            "reads of the internships page have failed since")

reset()
run_pass()
STATE_DOWN["fail_reads"] = {cs.APPLE_STUDENTS_STATE_KEY: 1}
PUSHES.clear()
check("one bot_state blip: retried, passes", aw.drill() == 0 and kinds(PUSHES, "canary") == [], str(kinds(PUSHES, "canary")))

reset()
run_pass()
STATE_DOWN["drop_writes"] = {aw.DRILL_KEY}
drill_fails("its pass stamp not taken by bot_state: fails (an unrecorded pass becomes a false alarm in a week)",
            "recording the pass")

reset()
run_pass()
STATE[aw.DRILL_KEY] = json.dumps({"passed_at": (_now() - timedelta(hours=3)).isoformat()})
os.environ["GITHUB_EVENT_NAME"] = "schedule"
PUSHES.clear()
rc = aw.drill()
check("GitHub's late scheduled run after the 9 AM pinger's pass stands down: no pushes", rc == 0 and PUSHES == [],
      str(PUSHES))
STATE[aw.DRILL_KEY] = json.dumps({"passed_at": (_now() - timedelta(days=3)).isoformat()})
PUSHES.clear()
check("...but runs when the last pass is older (the pinger missed)", aw.drill() == 0 and len(kinds(PUSHES, "apple")) == 1)
os.environ.pop("GITHUB_EVENT_NAME")

reset()
run_pass()
out_file = tempfile.NamedTemporaryFile("w+", delete=False)
os.environ["GITHUB_OUTPUT"] = out_file.name
SITE["status"]["students"] = 403
aw.drill()
os.environ.pop("GITHUB_OUTPUT")
check("a failure the drill reported tells the workflow, so its last-resort alarm stays quiet",
      "alerted=true" in open(out_file.name).read())
os.unlink(out_file.name)

reset(applied=())
STORE[id_of(url_of(next(r for r in REAL if r["postingTitle"] == SWE_TITLE)))]["status"] = "interview"
run_pass()
PUSHES.clear()
aw.drill()
apple = kinds(PUSHES, "apple")
check("a posting he has moved on to 'interview' still says he applied", apple
      and "You've already applied to this posting." in apple[0]["body"], apple[0]["body"] if apple else "")

reset()
run_pass()
STATE[aw.SHADOW_KEY] = json.dumps({"last_new_at": time.time() - 3600, "log": [[round(time.time()) - 3600, 30]]})
STATE[aw.SHADOW_CHECKED_KEY] = (_now() - timedelta(hours=4)).isoformat()
check("the drill's shadow line says when the watch has stopped", aw.shadow_summary() ==
      "Shadow watch: has not run in over 3 hours.", aw.shadow_summary())

reset()
run_pass()
VERDICT.clear()
VERDICT.update(failed=True)
PUSHES.clear()
rc = aw.drill()
apple = kinds(PUSHES, "apple")
check("the classifier down: the 🍎 push still goes, saying the verdict is pending (the classifier has its own alarm)",
      rc == 0 and apple and "pending" in apple[0]["body"] and not kinds(PUSHES, "job"), str(PUSHES))

print("\n-- a drill that stops running is noticed --")
reset()
t_d = time.time()
STATE.pop(aw.SHADOW_CHECKED_KEY, None)
PUSHES.clear()
aw.shadow_check(now=t_d)
check("no drill on record: the clock starts, no alarm", aw.DRILL_KEY in STATE and kinds(PUSHES, "canary") == [])
STATE[aw.DRILL_KEY] = json.dumps({"passed_at": datetime.fromtimestamp(t_d - 9 * 86400, timezone.utc).isoformat()})
aw.shadow_check(now=t_d + 3700)
canary = kinds(PUSHES, "canary")
check("9 days since the last pass: one alarm", len(canary) == 1 and "drill has not passed since" in canary[0]["body"],
      str(canary))
aw.shadow_check(now=t_d + 7400)
check("...not repeated", len(kinds(PUSHES, "canary")) == 1)

reset()
STATE.pop(aw.SHADOW_CHECKED_KEY, None)
PUSHES.clear()
STATE[aw.DRILL_KEY] = json.dumps({"passed_at": datetime.fromtimestamp(t_d - 9 * 86400, timezone.utc).isoformat()})
NTFY["canary_status"] = 503
aw.shadow_check(now=t_d)
check("the stale-drill alarm refused by ntfy: not marked sent", not json.loads(STATE[aw.DRILL_KEY]).get("alerted"))
NTFY["canary_status"] = 200
aw.shadow_check(now=t_d + 3700)
check("...sent the next hour", len(kinds(PUSHES, "canary")) == 1)

reset()
STATE.pop(aw.SHADOW_CHECKED_KEY, None)
PUSHES.clear()
stale = json.dumps({"passed_at": datetime.fromtimestamp(t_d - 9 * 86400, timezone.utc).isoformat()})
STATE[aw.DRILL_KEY] = stale
STATE_DOWN["keys"] = {aw.DRILL_KEY}
aw.shadow_check(now=t_d)
STATE_DOWN["keys"] = set()
check("the drill stamp unreadable this hour: left exactly as it was, no alarm, no fresh clock",
      STATE[aw.DRILL_KEY] == stale and kinds(PUSHES, "canary") == [])

reset()
STATE.pop(aw.SHADOW_CHECKED_KEY, None)
PUSHES.clear()
STATE[aw.DRILL_KEY] = json.dumps({"passed_at": datetime.fromtimestamp(t_d - 9 * 86400, timezone.utc).isoformat(),
                                  "clock_started": True})
aw.shadow_check(now=t_d)
canary = kinds(PUSHES, "canary")
check("a drill that never passed at all says so, rather than 'not since <date>'", canary
      and "has never passed (watching since" in canary[0]["body"], str(canary))

print()
if _fails:
    print(f"{_fails} FAILED")
    sys.exit(1)
print("all Apple alert checks passed")
