"""The ATS pass: ats_boards.py, the vendored ats_sources.py, ats_pass.py, and main.py's use of them.

Offline. The platform responses are RECORDED: fixtures/ats/*.json hold live responses of 2026-09-30 --
Greenhouse (Tailscale's board), Ashby (Drata's), Workday (all three pages of Kyndryl's early-careers site,
unmodified) and one Workday CXS job detail (a Palo Alto Networks posting) -- trimmed only in row count and
description length (each file's "note" says how). A stand-in for `requests` answers from them and returns
404 for anything else.

Run:  cd scraper_brice && python -X utf8 test_ats_pass.py
"""

import testkit

testkit.block_network()

import ast  # noqa: E402
import contextlib  # noqa: E402
import hashlib  # noqa: E402
import html  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import requests  # noqa: E402

import ats_boards  # noqa: E402
import ats_pass  # noqa: E402
import ats_sources  # noqa: E402
import db as real_db  # noqa: E402
import families  # noqa: E402
import main  # noqa: E402
from db import make_norm_key  # noqa: E402
from testkit import (Forbidden, Pipeline, captured_logs, captured_stdout, check, env_cleared, patched,  # noqa: E402
                     raiser, section)

FIXTURES = testkit.HERE / "fixtures" / "ats"
REF = testkit.REPO / "scraper"

GH_URL = "https://boards-api.greenhouse.io/v1/boards/tailscale/jobs"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/drata"
WD_LIST_URL = "https://kyndryl.wd5.myworkdayjobs.com/wday/cxs/kyndryl/KyndrylEarlyCareers/jobs"
BROKEN_URL = "https://boards-api.greenhouse.io/v1/boards/no-such-board/jobs"
PANW_POSTING = ("https://paloaltonetworks.wd5.myworkdayjobs.com/panwexternalcareers/job/Office---USA---TX/"
                "Academy-Systems-Engineer_JR-011449")
PANW_CXS = ("https://paloaltonetworks.wd5.myworkdayjobs.com/wday/cxs/paloaltonetworks/panwexternalcareers/job/"
            "Office---USA---TX/Academy-Systems-Engineer_JR-011449")
RECORDED = ("greenhouse_tailscale.json", "ashby_drata.json", "workday_kyndryl.json", "workday_panw_detail.json")
BOARDS = {"Tailscale": {"platform": "greenhouse", "token": "tailscale"},
          "Drata": {"platform": "ashby", "token": "drata"},
          "Kyndryl": {"platform": "workday", "token": "kyndryl:wd5:KyndrylEarlyCareers"},
          "Broken Board": {"platform": "greenhouse", "token": "no-such-board"}}
LISTING_CALLS = [("GET", GH_URL, None), ("GET", ASHBY_URL, None), ("POST", WD_LIST_URL, 0),
                 ("POST", WD_LIST_URL, 20), ("POST", WD_LIST_URL, 40), ("GET", BROKEN_URL, None)]


def fixture(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["exchanges"]


class FakeResponse:
    def __init__(self, status: int, body):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body

    def json(self):
        if isinstance(self._body, BaseException):
            raise self._body
        return self._body


class Recorded:
    """Stands in for `requests` inside ats_sources and ats_pass, answering from the recorded exchanges.

    A request is keyed (method, url, Workday offset); an unknown key gets a 404. `extra` adds or replaces
    answers: a (status, body) pair, or an exception instance to raise. Every call lands in `calls` (with its
    keyword arguments in `kwargs`), and in `log` when one is given, to order it against a Pipeline's calls.
    """

    def __init__(self, *files, extra=None, log=None):
        self.routes = {}
        for name in files:
            for ex in fixture(name):
                key = (ex["method"], ex["url"], (ex.get("json") or {}).get("offset"))
                self.routes[key] = (ex["status"], ex["body"])
        self.routes.update(extra or {})
        self.calls: list = []
        self.kwargs: list = []
        self.log = log

    def _answer(self, key, kwargs):
        self.calls.append(key)
        self.kwargs.append(kwargs)
        if self.log is not None:
            self.log.append(("http." + key[0], key[1]))
        hit = self.routes.get(key)
        if isinstance(hit, BaseException):
            raise hit
        return FakeResponse(*hit) if hit else FakeResponse(404, {"error": "not found"})

    def get(self, url, params=None, **kwargs):
        return self._answer(("GET", url, None), dict(kwargs, params=params))

    def post(self, url, json=None, **kwargs):  # noqa: A002 -- requests' own keyword
        return self._answer(("POST", url, (json or {}).get("offset")), dict(kwargs, json=json))

    @contextlib.contextmanager
    def installed(self):
        with patched(ats_sources, requests=self), patched(ats_pass, requests=self):
            yield self


def listing(company: str, title: str, location: str, url: str, description=None) -> dict:
    """A listing exactly as the vendored fetchers build it."""
    return ats_sources._make_job(company, title, location, url, description, None)


@contextlib.contextmanager
def only_listings(rows):
    """fetch_all_listings returns `rows`; any request, description fetch or DB client use fails the test."""
    with patched(ats_sources, fetch_all_listings=lambda boards: [dict(r) for r in rows]), \
            patched(ats_pass, requests=Forbidden("requests"),
                    fetch_workday_description=raiser("ats_pass.fetch_workday_description")), \
            patched(real_db, get_client=raiser("db.get_client")), captured_logs() as logs:
        yield logs


def sweep(rows, boards=None):
    boards = boards or {"Acme": {"platform": "workday", "token": "acme:wd5:Site"},
                        "Beta": {"platform": "greenhouse", "token": "beta"}}
    with only_listings(rows):
        return ats_pass.collect_ats_candidates(boards)


# ─────────────────────────────────────────────────────────────────────────────
section("ats_boards: the 26 verified boards")

SPEC_BOARDS = [   # SPEC section 8.1, in order: (company key, platform, token)
    ("Salesforce", "workday", "salesforce:wd12:External_Career_Site"),
    ("NCR Voyix", "workday", "ncr:wd1:ext_us"),
    ("OpenGov", "ashby", "opengov"),
    ("Okta", "greenhouse", "okta"),
    ("Pure Storage", "greenhouse", "purestorage"),
    ("CrowdStrike", "workday", "crowdstrike:wd5:crowdstrikecareers"),
    ("Anduril", "greenhouse", "andurilindustries"),
    ("Capital One", "workday", "capitalone:wd12:Capital_One"),
    ("FIS", "workday", "fis:wd5:SearchJobs"),
    ("Replit", "ashby", "replit"),
    ("Jump Trading", "greenhouse", "jumptrading"),
    ("Micron Technology", "workday", "micron:wd1:External"),
    ("Warner Bros. Discovery", "workday", "warnerbros:wd5:global"),
    ("HD Supply", "workday", "hdsupply:wd1:External"),
    ("Tempus", "workday", "tempus:wd5:Tempus_Careers"),
    ("Palo Alto Networks", "workday", "paloaltonetworks:wd5:panwexternalcareers"),
    ("Verkada", "greenhouse", "verkada"),
    ("Samsara", "greenhouse", "samsara"),
    ("Kyndryl", "workday", "kyndryl:wd5:KyndrylEarlyCareers"),
    ("AT&T", "workday", "att:wd1:ATTCollege"),
    ("Hewlett Packard Enterprise", "workday", "hpe:wd5:Jobsathpe"),
    ("Drata", "ashby", "drata"),
    ("Cisco", "workday", "cisco:wd5:Cisco_Careers"),
    ("Verizon", "workday", "verizon:wd12:verizon-careers"),
    ("Expel", "greenhouse", "expel"),
    ("Tailscale", "greenhouse", "tailscale"),
]
B = ats_boards.ATS_BOARDS
got = [(c, v.get("platform"), v.get("token")) for c, v in B.items()]
check("exactly 26 boards", len(B) == 26, str(len(B)))
check("company keys, platforms and tokens equal SPEC section 8.1, in order", got == SPEC_BOARDS,
      "; ".join(f"{g} != {w}" for g, w in zip(got, SPEC_BOARDS) if g != w) or f"{len(got)} vs {len(SPEC_BOARDS)}")
check("each entry is exactly {platform, token}", all(set(v) == {"platform", "token"} for v in B.values()))
check("every platform has a fetcher in the vendored ats_sources",
      all(v["platform"] in ats_sources._FETCHERS for v in B.values()))
check("the boards use three platforms: greenhouse, ashby and workday",
      {v["platform"] for v in B.values()} == {"greenhouse", "ashby", "workday"})
check("every Workday token is tenant:wdN:site",
      all(re.fullmatch(r"[a-z0-9-]+:wd\d+:[A-Za-z0-9_-]+", v["token"]) for v in B.values() if v["platform"] == "workday"))
check("Verizon's board is on wd12 (wd5 answers HTTP 422)", B["Verizon"]["token"].split(":")[1] == "wd12")

src = (REF / "ats_config.py").read_text(encoding="utf-8")
ast.parse(src)                                   # main's config, read as text: nothing of main's is imported
namespace: dict = {}
exec(compile(src, "ats_config.py", "exec"), namespace)
main_boards = namespace["ATS_COMPANIES"]
shared = [c for c, _p, _t in SPEC_BOARDS[:15]]
added = [c for c, _p, _t in SPEC_BOARDS[15:]]
bad = [c for c in shared if main_boards.get(c) != B[c]]
check("the 15 boards shared with the main pipeline carry exactly main's platform and token", not bad, str(bad))
check("the 11 added boards are not in the main pipeline's list", not set(added) & set(main_boards),
      str(set(added) & set(main_boards)))
check("keys the family filter special-cases keep their spelling (Pure Storage, Anduril, Micron Technology)",
      {"Pure Storage", "Anduril", "Micron Technology"} <= set(B))
check("...because Pure Storage's presales 'Systems Engineer' is a sales/solutions title only under that name",
      families.classify_family("Systems Engineer", "Pure Storage")[0] == "SALES_SOLUTIONS"
      and families.classify_family("Systems Engineer", "Pure Storage Inc")[0] != "SALES_SOLUTIONS")

# ─────────────────────────────────────────────────────────────────────────────
section("ats_sources.py: vendored from the main pipeline")

vendored = (testkit.HERE / "ats_sources.py").read_bytes()
reference = (REF / "ats_sources.py").read_bytes()
if vendored == reference:
    check("byte-identical to ../scraper/ats_sources.py (no drift)", True)
else:
    print("  NOTE: drift -- scraper_brice/ats_sources.py differs from ../scraper/ats_sources.py; "
          "review main's change and re-vendor it if it applies here (not a failure)")
check("LF line endings", b"\r\n" not in vendored)
check("imports no config module (it cannot pick up another pipeline's settings)",
      not re.search(rb"^\s*(?:from|import)\s+config\b", vendored, re.M))

# ─────────────────────────────────────────────────────────────────────────────
section("the vendored fetchers on the recorded responses")

rec = Recorded(*RECORDED)
with rec.installed(), captured_logs():
    gh = ats_sources.fetch_greenhouse_listings("Tailscale", "tailscale")
gh_jobs = fixture("greenhouse_tailscale.json")[0]["body"]["jobs"]
j0, s0 = (gh or [{}])[0], gh_jobs[0]
check("Greenhouse: every recorded job is a listing", len(gh) == len(gh_jobs) == 8, str(len(gh)))
check("...one GET per board, content=true, with a timeout",
      rec.calls == [("GET", GH_URL, None)] and rec.kwargs[0]["params"] == {"content": "true"}
      and rec.kwargs[0].get("timeout"), str(rec.calls))
check("...id = 'ats:' + sha1(absolute_url)[:16]",
      j0.get("id") == "ats:" + hashlib.sha1(s0["absolute_url"].encode("utf-8")).hexdigest()[:16])
check("...title, location name, url = apply_url = absolute_url, posted_at = updated_at",
      (j0.get("title"), j0.get("location"), j0.get("url"), j0.get("apply_url"), j0.get("posted_at"))
      == (s0["title"], s0["location"]["name"], s0["absolute_url"], s0["absolute_url"], s0["updated_at"]))
check("...the description is the content, un-escaped (Greenhouse serves it entity-escaped)",
      j0.get("description") == html.unescape(s0["content"]) and j0["description"].startswith("<div")
      and s0["content"].startswith("&lt;div"))

rec = Recorded(*RECORDED)
with rec.installed(), captured_logs():
    ab = ats_sources.fetch_ashby_listings("Drata", "drata")
ab_jobs = fixture("ashby_drata.json")[0]["body"]["jobs"]
check("Ashby: every recorded job is a listing, url = jobUrl, description = descriptionPlain",
      len(ab) == len(ab_jobs) == 5 and all(j["url"] == s["jobUrl"] and j["description"] == s["descriptionPlain"]
                                           and j["location"] == s["location"] for j, s in zip(ab, ab_jobs)))

rec = Recorded(*RECORDED)
with rec.installed(), captured_logs():
    wd = ats_sources.fetch_workday_listings("Kyndryl", "kyndryl:wd5:KyndrylEarlyCareers")
pages = fixture("workday_kyndryl.json")
first_posting = pages[0]["body"]["jobPostings"][0]
check("Workday: the recording is three pages; total is on page 0 only (later pages say 0)",
      [p["body"]["total"] for p in pages] == [50, 0, 0], str([p["body"]["total"] for p in pages]))
check("...all 50 postings, from three POSTs at offsets 0, 20 and 40 (the fetcher latches page 0's total)",
      len(wd) == 50 and rec.calls == [("POST", WD_LIST_URL, 0), ("POST", WD_LIST_URL, 20), ("POST", WD_LIST_URL, 40)],
      f"{len(wd)} {rec.calls}")
check("...20 per page, no search text", all(kw["json"]["limit"] == 20 and kw["json"]["searchText"] == ""
                                            for kw in rec.kwargs))
check("...url = board base + site + externalPath, and no description on any row",
      wd[0]["url"] == "https://kyndryl.wd5.myworkdayjobs.com/KyndrylEarlyCareers" + first_posting["externalPath"]
      and all(j["description"] is None for j in wd))
posted = datetime.fromisoformat(wd[0]["posted_at"]) if wd and wd[0]["posted_at"] else None
now = datetime.now(timezone.utc)
check("...'Posted 2 Days Ago' becomes a timestamp two days back", first_posting["postedOn"] == "Posted 2 Days Ago"
      and posted is not None and now - timedelta(days=2, minutes=5) < posted < now - timedelta(days=2) + timedelta(minutes=5))

rec = Recorded(*RECORDED, extra={("POST", WD_LIST_URL, 20): (500, {})})
with rec.installed(), captured_logs():
    wd_partial = ats_sources.fetch_workday_listings("Kyndryl", "kyndryl:wd5:KyndrylEarlyCareers")
check("...a failed later page keeps the page already read", len(wd_partial) == 20 and len(rec.calls) == 2,
      f"{len(wd_partial)} {rec.calls}")

# ─────────────────────────────────────────────────────────────────────────────
section("collect_ats_candidates on the recorded boards (plus one that 404s)")

rec = Recorded(*RECORDED)
with rec.installed(), captured_logs() as logs:
    cands, stats = ats_pass.collect_ats_candidates(BOARDS)
EXPECTED_KEPT = [
    ("Tailscale", "Customer Support Engineer (Tier 1)", "Remote (United States)", "SUPPORT_ENG"),
    ("Tailscale", "Solutions Engineer - Commercial (Expansion Sales)", "Remote (United States)", "SALES_SOLUTIONS"),
    ("Tailscale", "Windows Engineer", "Remote (United States)", "SYSTEMS_IT"),
    ("Drata", "Associate Solutions Architect", "Remote - US ", "SALES_SOLUTIONS"),
    ("Kyndryl", "Early Career Consult Program – Cybersecurity Engineer", "Dallas (USDALFRI) Frisco AI HUB", "SECURITY"),
    ("Kyndryl", "Early Career Consult Program – Cybersecurity Defense Associate", "Dallas (USDALFRI) Frisco AI HUB",
     "SECURITY"),
    ("Kyndryl", "Early Career Consult Program – Cybersecurity Infrastructure Associate",
     "Dallas (USDALFRI) Frisco AI HUB", "SECURITY"),
    ("Kyndryl", "Early Career Consult Program – Network Support Associate", "Dallas (USDALFRI) Frisco AI HUB",
     "PROGRAM"),
    ("Kyndryl", "Product Support Engineer", "CIO KPop-Dallas (US152527)", "SUPPORT_ENG"),
]
kept = [(j["company"], j["title"], j["location"], j["family"]) for j in cands]
check("exactly the rows that pass the gate, in board order, labelled with their family (or PROGRAM)",
      kept == EXPECTED_KEPT, "\n        ".join(map(str, kept)))
check("...the U.S. copy of a title posted in Canada, the U.S. and the U.K. is the one kept",
      [j["location"] for j in cands if j["title"] == "Customer Support Engineer (Tier 1)"] == ["Remote (United States)"])
check("...each tagged for the queue: source 'ats', search_term 'ats:<company>', id 'ats:<16 hex>'",
      all(j["source"] == "ats" and j["search_term"] == f"ats:{j['company']}" and re.fullmatch(r"ats:[0-9a-f]{16}", j["id"])
          for j in cands))
check("...norm_key = make_norm_key(company, title): the full-time key ('|ft')",
      all(j["norm_key"] == make_norm_key(j["company"], j["title"]) and j["norm_key"].endswith("|ft") for j in cands))
check("...Greenhouse and Ashby rows carry a description; Workday rows none (main fetches it per job, later)",
      all(bool(j["description"]) == (j["company"] != "Kyndryl") for j in cands))
check("stats: 4 boards, 3 with listings, 63 listings, 9 kept",
      (stats["boards"], stats["boards_with_listings"], stats["listings"], stats["kept"]) == (4, 3, 63, 9),
      str({k: stats[k] for k in ("boards", "boards_with_listings", "listings", "kept")}))
check("...dropped by rule", dict(stats["dropped_by"]) == {"non-US location": 40, "off-family": 13,
                                                          "seniority/leadership title": 1}, str(stats["dropped_by"]))
check("...every listing is kept or counted under exactly one rule",
      stats["listings"] == stats["kept"] + sum(stats["dropped_by"].values()))
check("...per board: platform, listings, kept", stats["by_board"] == {
    "Tailscale": {"platform": "greenhouse", "listings": 8, "kept": 3},
    "Drata": {"platform": "ashby", "listings": 5, "kept": 1},
    "Kyndryl": {"platform": "workday", "listings": 50, "kept": 5},
    "Broken Board": {"platform": "greenhouse", "listings": 0, "kept": 0}}, str(stats["by_board"]))
check("...the board that failed is named", stats["empty_boards"] == ["Broken Board"], str(stats["empty_boards"]))
check("...kept by family", dict(stats["kept_by_family"]) == {"SUPPORT_ENG": 2, "SALES_SOLUTIONS": 2, "SYSTEMS_IT": 1,
                                                              "SECURITY": 3, "PROGRAM": 1}, str(stats["kept_by_family"]))
check("...dropped samples read 'company | title | location'",
      "Tailscale | Customer Support Engineer (Tier 1) | Remote (Canada)" in stats["dropped_samples"].get("non-US location", [])
      and "Drata | Senior IT Engineer | Hybrid - San Francisco" in stats["dropped_samples"].get("seniority/leadership title", []))
check("only the listing endpoints were requested: nothing per job, no description, no other host",
      sorted(rec.calls, key=str) == sorted(LISTING_CALLS, key=str), str(rec.calls))
warnings = [m for m in logs.messages() if "no listings this run" in m]
check("the log names the board that returned nothing", warnings and warnings[0].endswith(": Broken Board"),
      str(warnings))
check("a sweep inside its budget is not cut short", stats["cut_short"] is False)
check("...and gives ats_sources its own `requests` back", ats_sources.requests is requests)

# ─────────────────────────────────────────────────────────────────────────────
section("collect_ats_candidates: the sweep's own budget (config.ATS_SWEEP_BUDGET_S)")
clock = testkit.FakeClock()


def slow_sweep(boards):
    """Tailscale answers; then a board pages past the budget, and Kyndryl's first POST is refused."""
    out = ats_sources.fetch_greenhouse_listings("Tailscale", "tailscale")
    clock.advance(ats_pass.config.ATS_SWEEP_BUDGET_S + 1)
    return out + ats_sources.fetch_workday_listings("Kyndryl", "kyndryl:wd5:KyndrylEarlyCareers")


rec = Recorded(*RECORDED)
with rec.installed(), patched(ats_pass, time=clock.as_time_module()), \
        patched(ats_sources, fetch_all_listings=slow_sweep), captured_logs() as logs:
    before = ats_sources.requests
    cands, stats = ats_pass.collect_ats_candidates(BOARDS)
    restored = ats_sources.requests is before
check("a request after the budget never leaves: Tailscale's GET went out, Kyndryl's POST did not",
      rec.calls == [("GET", GH_URL, None)], str(rec.calls))
check("...the board read in time keeps its rows", {j["company"] for j in cands} == {"Tailscale"}
      and stats["listings"] == 8, str(stats["listings"]))
check("...the sweep reports cut_short, and the log says so",
      stats["cut_short"] is True and any("ATS sweep hit its 10-minute budget" in m for m in logs.messages()))
check("...and ats_sources gets its own `requests` back", restored)
check("the budget is 10 minutes, well inside the 48-minute run", ats_pass.config.ATS_SWEEP_BUDGET_S == 600)

# ─────────────────────────────────────────────────────────────────────────────
section("collect_ats_candidates: one copy per posting; the gate before anything else")

WD = "https://acme.wd5.myworkdayjobs.com/Site/job/"
ANE = "Associate Network Engineer"
remote = listing("Acme", ANE, "Remote", WD + "Remote/Associate-Network-Engineer_R1")
austin = listing("Acme", ANE, "Austin, TX", WD + "Austin-TX/Associate-Network-Engineer_R2")
seattle = listing("Acme", ANE, "Seattle, WA", WD + "Seattle-WA/Associate-Network-Engineer_R3")
check("(the premise: is_us is unknown for 'Remote' and True for Austin and Seattle)",
      [families.is_us(j["location"], j["url"], ANE) for j in (remote, austin, seattle)] == [None, True, True])

c, s = sweep([remote, austin])
check("an unknown-location copy first, a U.S. copy second -> the U.S. copy is kept",
      [j["id"] for j in c] == [austin["id"]] and dict(s["dropped_by"]) == {ats_pass.DUPLICATE_RULE: 1},
      str([(j["location"]) for j in c]))
check("...and the other copy is sampled as the duplicate",
      s["dropped_samples"].get(ats_pass.DUPLICATE_RULE) == ["Acme | Associate Network Engineer | Remote"])

pr = listing("Hewlett Packard Enterprise", "Cloud Engineer Graduate", "Aguadilla, Puerto Rico, Puerto Rico",
             "https://hpe.wd5.myworkdayjobs.com/Jobsathpe/job/Aguadilla-Puerto-Rico-Puerto-Rico/"
             "Cloud-Engineer-Graduate_1215862-3")
multi = listing("Hewlett Packard Enterprise", "Cloud Engineer Graduate", "10 Locations",
                "https://hpe.wd5.myworkdayjobs.com/Jobsathpe/job/Spring-Texas-United-States-of-America/"
                "Cloud-Engineer-Graduate_1213628-1")
c, s = sweep([pr, multi], {"Hewlett Packard Enterprise": {"platform": "workday", "token": "hpe:wd5:Jobsathpe"}})
check("HPE's shape (seen live 2026-09-30): the Puerto Rico copy first, the ten-city U.S. copy second -> U.S. kept",
      [j["id"] for j in c] == [multi["id"]] and c[0]["family"] == "NETWORK_INFRA", str([j["location"] for j in c]))

c, s = sweep([remote, listing("Acme", ANE, "Remote", WD + "Remote/Associate-Network-Engineer_R4")])
check("no copy with a confirmed U.S. location -> the first copy is kept", [j["id"] for j in c] == [remote["id"]])
c, s = sweep([austin, seattle])
check("two confirmed U.S. copies -> the first is kept", [j["id"] for j in c] == [austin["id"]])
c, s = sweep([austin, dict(austin)])
check("the same URL twice -> one candidate", [j["id"] for j in c] == [austin["id"]]
      and s["dropped_by"].get(ats_pass.DUPLICATE_RULE) == 1)
shouty = listing("Acme", "ASSOCIATE  NETWORK ENGINEER.", "Denver, CO", WD + "Denver-CO/ANE_R5")
check("(the premise: case, spacing and punctuation do not change the norm_key)",
      make_norm_key("Acme", ANE) == make_norm_key("Acme", shouty["title"]))
c, s = sweep([austin, shouty])
check("...so the same title written differently is one posting", [j["id"] for j in c] == [austin["id"]])
toronto = listing("Acme", ANE, "2 Locations", WD + "Toronto-Canada/Associate-Network-Engineer_R6")
c, s = sweep([toronto, austin])
check("a non-U.S. copy is dropped by the gate (it is not a duplicate); the U.S. copy is kept",
      [j["id"] for j in c] == [austin["id"]] and dict(s["dropped_by"]) == {"non-US location": 1}, str(s["dropped_by"]))
check("...a Workday '2 Locations' row is placed by its URL (the pass hands source_gate the url)",
      s["dropped_samples"].get("non-US location") == ["Acme | Associate Network Engineer | 2 Locations"])

tdp = listing("AT&T", "AT&T Technology Development Program", "4 Locations",
              "https://att.wd1.myworkdayjobs.com/ATTCollege/job/Dallas-Texas/AT-T-Technology-Development-Program_R-121905-1")
rows = [tdp,
        listing("AT&T", "AT&T Technology Development Program Internship", "4 Locations",
                "https://att.wd1.myworkdayjobs.com/ATTCollege/job/Dallas-Texas/AT-T-TDP-Internship_R-122670-1"),
        listing("Acme", "Help Desk Technician - New Grad", "Austin, TX", WD + "Austin-TX/HDT_R7"),
        listing("Acme", "Senior Network Engineer", "Austin, TX", WD + "Austin-TX/SNE_R8"),
        listing("Acme", "Account Executive", "Austin, TX", WD + "Austin-TX/AE_R9")]
c, s = sweep(rows, {"AT&T": {"platform": "workday", "token": "att:wd1:ATTCollege"},
                    "Acme": {"platform": "workday", "token": "acme:wd5:Site"}})
check("the early-career program pass-through is on for ATS rows: AT&T's TDP is kept as PROGRAM",
      [(j["title"], j["family"]) for j in c] == [("AT&T Technology Development Program", "PROGRAM")], str(c))
check("...while the gate's rules drop the rest, each under its own rule",
      dict(s["dropped_by"]) == {"internship/co-op title": 1, "off-family": 2, "seniority/leadership title": 1},
      str(s["dropped_by"]))

beta = listing("Beta", "Associate Sales Engineer", "Chicago, IL", "https://job-boards.greenhouse.io/beta/jobs/1",
               description="<p>Greenhouse description</p>")
c, s = sweep([beta, austin])         # Beta's board answered first; Acme comes first in the board list
check("candidates follow board order, not the order the hosts answered", [j["company"] for j in c] == ["Acme", "Beta"])

many = [listing("Acme", "Senior Network Engineer", f"City {i}, TX", WD + f"City-{i}-TX/SNE_{i}") for i in range(45)]
c, s = sweep(many)
check("dropped samples stop at SAMPLES_PER_RULE; the count does not",
      len(s["dropped_samples"].get("seniority/leadership title", [])) == ats_pass.SAMPLES_PER_RULE
      and s["dropped_by"].get("seniority/leadership title") == 45)

seen_boards = {}


def capture(boards):
    seen_boards["arg"] = boards
    return []


with patched(ats_sources, fetch_all_listings=capture), captured_logs():
    c, s = ats_pass.collect_ats_candidates()
check("called with no argument, the sweep covers ats_boards.ATS_BOARDS", seen_boards.get("arg") is ats_boards.ATS_BOARDS)
check("...and an all-empty sweep says so: 26 boards, 0 with listings, all 26 named (main alerts the owner)",
      (s["boards"], s["boards_with_listings"], len(s["empty_boards"]), c) == (26, 0, 26, []))


def pool_breaks(boards):
    raise RuntimeError("can't start new thread")


with patched(ats_sources, fetch_all_listings=pool_breaks), captured_logs() as logs:
    try:
        c, s = ats_pass.collect_ats_candidates()
        outcome = (c, s["boards_with_listings"], len(s["empty_boards"]))
    except Exception as exc:  # noqa: BLE001 -- the check reports it
        outcome = f"raised {type(exc).__name__}"
check("a sweep that raises (not one board: the whole pool) is an empty sweep, not a crashed run",
      outcome == ([], 0, 26), str(outcome))
check("...logged by exception type only", any(m == "ATS sweep failed (RuntimeError) — no board read this run"
                                              for m in logs.messages()) and "new thread" not in logs.text())

# ─────────────────────────────────────────────────────────────────────────────
section("fetch_workday_description: Workday hosts only, one GET, text out")

rec = Recorded(*RECORDED)
with rec.installed(), captured_logs():
    text = ats_pass.fetch_workday_description(PANW_POSTING)
detail_html = fixture("workday_panw_detail.json")[0]["body"]["jobPostingInfo"]["jobDescription"]
check("the recorded CXS detail -> plain text", bool(text) and text.startswith("Our Mission")
      and "<p>" not in text and "</" not in text, repr((text or "")[:80]))
check("...long enough to lift the classifier's title-only cap (>= 200 chars), within MAX_LEN",
      text is not None and 200 <= len(text) <= ats_pass.MAX_LEN and len(detail_html) > len(text))
check("...exactly one GET, to /wday/cxs/<tenant>/<site>/job/<path>, with a timeout",
      rec.calls == [("GET", PANW_CXS, None)] and rec.kwargs[0].get("timeout") == ats_pass.TIMEOUT, str(rec.calls))

REFUSED = [None, "", "not a url", "https://jobright.ai/jobs/info/0123456789abcdef0123456789abcdef",
           "https://job-boards.greenhouse.io/tailscale/jobs/4724309005",
           "https://jobs.ashbyhq.com/drata/b0d00418-9f98-4776-a83d-e0bbe9598025",
           "https://myworkdayjobs.com/Site/job/Austin-TX/Role_1",
           "https://acme.wd5.myworkdayjobs.com.evil.example/Site/job/Austin-TX/Role_1",
           "https://acme.wd5.myworkdayjobs.com@evil.example/Site/job/Austin-TX/Role_1",
           "https://evil.example/?next=acme.wd5.myworkdayjobs.com/Site/job/Austin-TX/Role_1",
           "ftp://acme.wd5.myworkdayjobs.com/Site/job/Austin-TX/Role_1",
           "https://acme.wd5.myworkdayjobs.com/Site"]
rec = Recorded(*RECORDED)
with rec.installed(), captured_logs():
    results = [ats_pass.fetch_workday_description(u) for u in REFUSED]
check(f"{len(REFUSED)} non-Workday or job-less URLs (jobright.ai among them) -> None, and not one request",
      results == [None] * len(REFUSED) and rec.calls == [], str(rec.calls))

rec = Recorded(extra={("GET", "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Site/job/Austin-TX/Role_1", None):
                      (200, {"jobPostingInfo": {"jobDescription": "<p>Role one.</p>"}})})
with rec.installed(), captured_logs():
    text = ats_pass.fetch_workday_description("https://someone@ACME.wd5.myworkdayjobs.com/Site/job/Austin-TX/Role_1")
check("the request goes to the checked hostname only (user info dropped, host lower-cased)",
      text == "Role one." and rec.calls == [("GET", "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Site/job/"
                                                    "Austin-TX/Role_1", None)], str(rec.calls))

LOCALE = "https://acme.wd5.myworkdayjobs.com/en-US/Site/job/Austin-TX/Role_1"
rec = Recorded(extra={("GET", "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Site/job/Austin-TX/Role_1", None):
                      (200, {"jobPostingInfo": {"jobDescription": "<p>Localised.</p>"}})})
with rec.installed(), captured_logs():
    text = ats_pass.fetch_workday_description(LOCALE)
check("a leading locale segment: the site is tried with it, then without it",
      text == "Localised." and [u for _m, u, _o in rec.calls] == [
          "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/en-US/Site/job/Austin-TX/Role_1",
          "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Site/job/Austin-TX/Role_1"], str(rec.calls))

ROLE = "https://acme.wd5.myworkdayjobs.com/Site/job/Austin-TX/Role_1"
ROLE_CXS = ("GET", "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Site/job/Austin-TX/Role_1", None)
FAILURES = [("HTTP 500", (500, {})), ("no jobPostingInfo", (200, {"hiringOrganization": {}})),
            ("jobPostingInfo null", (200, {"jobPostingInfo": None})),
            ("empty description", (200, {"jobPostingInfo": {"jobDescription": ""}})),
            ("a JSON list", (200, [])), ("invalid JSON", (200, ValueError("Expecting value"))),
            ("connection error", requests.exceptions.ConnectionError("connection refused")),
            ("timeout", requests.exceptions.Timeout("read timed out"))]
for label, answer in FAILURES:
    rec = Recorded(extra={ROLE_CXS: answer})
    with rec.installed(), captured_logs() as logs:
        try:
            out = ats_pass.fetch_workday_description(ROLE)
        except Exception as exc:  # noqa: BLE001 -- the check reports it
            out = f"raised {type(exc).__name__}"
    check(f"{label} -> None, no exception", out is None and len(rec.calls) == 1, str(out))
check("...a failure is logged by exception type (never an exception message)",
      [m.strip() for m in logs.messages()] == ["Workday description fetch failed (Timeout)"]
      and "read timed out" not in logs.text(), str(logs.messages()))

rec = Recorded(extra={ROLE_CXS: (200, {"jobPostingInfo": {"jobDescription": "<p>" + "x" * 30000 + "</p>"}})})
with rec.installed(), captured_logs():
    text = ats_pass.fetch_workday_description(ROLE)
check("a description longer than MAX_LEN is cut to MAX_LEN (12,000)", text is not None and len(text) == 12000)
check("workday_host(): the hostname of a Workday board URL, else None",
      ats_pass.workday_host("https://ACME.wd5.myworkdayjobs.com/x") == "acme.wd5.myworkdayjobs.com"
      and ats_pass.workday_host("https://jobright.ai/jobs/info/abc") is None and ats_pass.workday_host(None) is None)

# ─────────────────────────────────────────────────────────────────────────────
section("main.py with the real ATS pass")

p = Pipeline(real_db)
rec = Recorded(*RECORDED)
with p.install(main), patched(main, ats_pass=ats_pass), patched(ats_pass, ATS_BOARDS=BOARDS), rec.installed(), \
        captured_logs() as logs:
    state = main.RunState()
    queue = main.collect_ats(state)
check("collect_ats queues the 9 gate-passing rows, stored-row lookup included (none stored yet)",
      [j["title"] for j in queue] == [t for _c, t, _l, _f in EXPECTED_KEPT]
      and ("db.find_known_candidates", [j["id"] for j in queue]) in p.log)
check("...each with a queue order and source 'ats'", all(j.get("_order") and j["source"] == "ats" for j in queue))
check("...and the run's ATS stats (boards, with listings, listings, kept, candidates, per board)",
      (state.ats["boards"], state.ats["boards_with_listings"], state.ats["listings"], state.ats["kept"],
       state.ats["candidates"]) == (4, 3, 63, 9, 9)
      and state.ats["by_board"].get("Kyndryl") == {"platform": "workday", "listings": 50, "kept": 5},
      str({k: v for k, v in state.ats.items() if k != "dropped_samples"}))
check("...the interface log line", "ATS: 63 listings from 3/4 boards | kept by the gate 9 | new 9" in logs.messages())

rec = Recorded(*RECORDED)
fdb, fcl, fno = Forbidden("db"), Forbidden("classifier"), Forbidden("notifier")
# SAMPLES_PER_RULE=5 so a rule's total (40 non-US rows) differs from the number of titles sampled
with env_cleared(), rec.installed(), patched(ats_pass, ATS_BOARDS=BOARDS, SAMPLES_PER_RULE=5), \
        patched(main, db=fdb, classifier=fcl, notifier=fno), captured_logs(), captured_stdout() as out:
    try:
        code = main.run(["--dry-run", "--no-linkedin", "--no-jobright", "--sample", "3"])
    except AssertionError as exc:            # a Forbidden module was touched: report, don't crash
        code = f"AssertionError: {exc}"
report = out.getvalue()
check("dry run with the real pass: exit 0 with every secret blank; db, classifier, notifier untouched",
      code == 0 and all(f.touched == [] for f in (fdb, fcl, fno)), str(code))
check("...the ATS line", "ats:      4 boards (3 with listings) | raw 63 | kept 9 | new in run 9 | dropped: non-US "
                         "location 40, off-family 13, seniority/leadership title 1" in report, report[:400])
check("...one line per board, listings / kept",
      all(line in report for line in ("  per board, listings / kept by the gate:", "    Tailscale (greenhouse): 8 / 3",
                                      "    Drata (ashby): 5 / 1", "    Kyndryl (workday): 50 / 5",
                                      "    Broken Board (greenhouse): 0 / 0")))
check("...the board that returned nothing is named",
      "  boards with no listings (an error or an empty board): Broken Board" in report)
check("...what the caps would allow", "would classify: ats 9 / linkedin 0 / jobright 0; leftover 0" in report)
check("...kept samples, entry-marked and primary families first",
      "kept sample (ats, 3 of 9): company | title | location | family\n  Drata | Associate Solutions Architect |"
      in report)
check("...a dropped sample's count is the rule's total, not the sample size",
      "dropped sample (ats, non-US location, 3 of 40):" in report
      and "dropped sample (ats, seniority/leadership title, 1 of 1):" in report)
check("...and nothing was fetched per job (no Workday description in a dry run)",
      sorted(rec.calls, key=str) == sorted(LISTING_CALLS, key=str), str(rec.calls))


def panw_job():
    job = listing("Palo Alto Networks", "Academy Systems Engineer", "Office - USA - TX", PANW_POSTING)
    job.update(source="ats", family="PROGRAM", search_term="ats:Palo Alto Networks", _order=1,
               norm_key=make_norm_key("Palo Alto Networks", "Academy Systems Engineer"))
    return job


p = Pipeline(real_db)
rec = Recorded(*RECORDED, log=p.log)
job = panw_job()
p.db.rows[job["id"]] = {"id": job["id"], "tier": "APPLY", "status": "new"}
with p.install(main), patched(main, ats_pass=ats_pass), rec.installed(), captured_logs():
    outcome = main.process_job(job, main.RunState())
check("process_job, a Workday row already stored: skipped -- no description GET, no Claude call",
      outcome == "skipped" and rec.calls == [] and not p.calls("classifier."), f"{outcome} {rec.calls}")

p = Pipeline(real_db)
rec = Recorded(*RECORDED, log=p.log)
job = panw_job()
with p.install(main), patched(main, ats_pass=ats_pass), rec.installed(), captured_logs():
    outcome = main.process_job(job, main.RunState())
guard = p.log.index(("db.get_job_row", job["id"])) if ("db.get_job_row", job["id"]) in p.log else None
fetch = p.log.index(("http.GET", PANW_CXS)) if ("http.GET", PANW_CXS) in p.log else None
check("process_job, a new Workday row: one CXS GET, after the already-stored guard",
      rec.calls == [("GET", PANW_CXS, None)] and guard is not None and fetch is not None and guard < fetch,
      f"{rec.calls} guard={guard} fetch={fetch}")
check("...paced (the 0.5-1.0 s Workday sleep ran before it)", 0.5 in p.clock.sleeps, str(p.clock.sleeps))
check("...Claude sees the fetched description; the APPLY is stored and pinged",
      p.classifier.seen and (p.classifier.seen[0].get("description") or "").startswith("Our Mission")
      and outcome == "pushed" and p.db.inserted and p.db.inserted[0]["id"] == job["id"], outcome)

p = Pipeline(real_db)
rec = Recorded(*RECORDED, log=p.log)
unrecorded = listing("Kyndryl", "Product Support Engineer", "CIO KPop-Dallas (US152527)",
                     "https://kyndryl.wd5.myworkdayjobs.com/KyndrylEarlyCareers/job/Dallas/Product-Support-Engineer_R-1")
unrecorded.update(source="ats", family="SUPPORT_ENG", search_term="ats:Kyndryl", _order=1,
                  norm_key=make_norm_key("Kyndryl", "Product Support Engineer"))
with p.install(main), patched(main, ats_pass=ats_pass), rec.installed(), captured_logs():
    outcome = main.process_job(unrecorded, main.RunState())
check("...a Workday fetch that fails (404) leaves the row title-only for Claude, still classified",
      len(rec.calls) == 1 and p.classifier.seen and p.classifier.seen[0].get("description") is None
      and outcome == "pushed", f"{rec.calls} {outcome}")

p = Pipeline(real_db)
rec = Recorded(*RECORDED, log=p.log)
greenhouse_row = dict(beta, source="ats", family="SALES_SOLUTIONS", search_term="ats:Beta", _order=1,
                      norm_key=make_norm_key("Beta", "Associate Sales Engineer"))
with p.install(main), patched(main, ats_pass=ats_pass), rec.installed(), captured_logs():
    outcome = main.process_job(greenhouse_row, main.RunState())
check("...a Greenhouse row arrives with its description: no request at all",
      rec.calls == [] and p.classifier.seen[0]["description"] == "<p>Greenhouse description</p>", str(rec.calls))

sys.exit(testkit.finish())
