"""The jobright pass: jobright.py, and main.py's use of it (fetch, ETag round trip, dry-run report).

Offline. fixtures/jobright/*.md are the five READMEs the pass reads (Engineering, Sales, Software-Engineer,
Consultant, Support) as fetched 2026-09-30, trimmed to 157 of their 74,823 table rows -- whole company
blocks, file order kept, the text around the table unchanged (each file's first line says so). They keep
the format's real variations: "↳" rows, "|" inside a company name and inside a location, a title split
across two lines, "&amp;" in titles and year-less dates. Cases the real files do not contain (malformed
rows, stale dates, refused URLs, a moved list) are built inline with made-up companies and ids. A
stand-in for requests.get serves the fixtures and records every request.

Run:  cd scraper_brice && python -X utf8 test_jobright.py
"""

import testkit

testkit.block_network()

import ast  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import types  # noqa: E402
from collections import Counter  # noqa: E402
from datetime import date, datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from urllib.parse import urlparse  # noqa: E402

import requests  # noqa: E402
from requests.structures import CaseInsensitiveDict  # noqa: E402

import classifier  # noqa: E402
import config  # noqa: E402
import db as real_db  # noqa: E402
import jobright  # noqa: E402
import main  # noqa: E402
import title_gate  # noqa: E402
from db import make_norm_key  # noqa: E402
from testkit import (Forbidden, Pipeline, captured_logs, captured_stdout, check, env_cleared, patched,  # noqa: E402
                     section)

FIXTURES = testkit.HERE / "fixtures" / "jobright"
TODAY = date(2026, 9, 30)
LISTS = [(name, url, support) for name, url, support in config.JOBRIGHT_LISTS]
URL = {name: url for name, url, _s in LISTS}
SUPPORT = {name: support for name, _u, support in LISTS}
JOB_LINK = "jobright.ai/jobs/info/"
TMP = Path(tempfile.mkdtemp(prefix="brice_jobright_"))
PROFILE = TMP / "rubric.md"
PROFILE.write_text("placeholder rubric for preflight\n", encoding="utf-8")


def fixture_text(name: str) -> str:
    return (FIXTURES / f"2026-{name}-New-Grad.md").read_text(encoding="utf-8")


def parse(name: str, today: date = TODAY):
    return jobright.parse_readme(fixture_text(name), name, today)


def to_jobs(name: str, today: date = TODAY, samples=None):
    rows, _bad = parse(name, today)
    return jobright.rows_to_jobs(rows, name, support_only=SUPPORT[name], today=today, samples=samples)


def find(rows, title, company=None):
    return [r for r in rows if r["title"] == title and (company is None or r["company"] == company)]


def syn_row(company: str, title: str, jid: str, loc: str = "Austin, TX, United States", wm: str = "On Site",
            posted: str = "Sep 29") -> str:
    """A README row in jobright's format, for made-up companies and ids."""
    cell = company if company in ("↳", "") else f"**[{company}](https://example.com/{jid})**"
    return (f"| {cell} | **[{title}](https://jobright.ai/jobs/info/{jid}?utm_campaign=Test&utm_source=1103)** | "
            f"{loc} | {wm} | {posted} |")


HEADER = ["| Company | Job Title | Location | Work Model | Date Posted |",
          "| ----- | --------- |  --------- | ---- | ------- |"]


# ─────────────────────────────────────────────────────────────────────────────
section("infer_date: year-less 'Mon D' dates")
infer = jobright.infer_date
check("'Sep 29' read on 2026-09-30 -> 2026-09-29", infer("Sep 29", TODAY) == date(2026, 9, 29))
check("today's own date", infer("Sep 30", TODAY) == date(2026, 9, 30))
check("one day ahead is timezone skew: this year", infer("Oct 1", TODAY) == date(2026, 10, 1))
check("two days ahead is last year's date", infer("Oct 2", TODAY) == date(2025, 10, 2))
check("Dec -> Jan rollover: 'Dec 29' on 2027-01-03 -> 2026-12-29", infer("Dec 29", date(2027, 1, 3)) == date(2026, 12, 29))
check("the skew across New Year: 'Jan 1' on 2026-12-31 -> 2027-01-01", infer("Jan 1", date(2026, 12, 31)) == date(2027, 1, 1))
check("'Feb 30' -> None", infer("Feb 30", TODAY) is None)
check("leap day in a leap year", infer("Feb 29", date(2028, 3, 1)) == date(2028, 2, 29))
check("leap day read a year later -> the last real Feb 29", infer("Feb 29", date(2029, 3, 1)) == date(2028, 2, 29))
check("leap day with no leap year in reach -> None", infer("Feb 29", date(2027, 3, 1)) is None)
check("a one-digit day, surrounding spaces", infer(" Sep 3 ", TODAY) == date(2026, 9, 3))
check("anything else -> None (the row is kept, undated)",
      all(infer(x, TODAY) is None for x in ("", None, "Sept 29", "2026-09-29", "29 Sep", "Sep 29, 2026", "2d ago")))


# ─────────────────────────────────────────────────────────────────────────────
section("parse_readme: the five real READMEs (trimmed)")
EXPECTED_ROWS = {"Engineering": 49, "Sales": 33, "Software-Engineer": 21, "Consultant": 19, "Support": 35}
check("the fixtures are the five configured lists", sorted(EXPECTED_ROWS) == sorted(URL))
parsed = {name: parse(name) for name in URL}
for name, n in EXPECTED_ROWS.items():
    rows, bad = parsed[name]
    links = sum(1 for line in fixture_text(name).splitlines() if JOB_LINK in line)
    check(f"{name}: {n} rows, none unparsed, one per job link in the file",
          len(rows) == n and bad == 0 and links == n, f"{len(rows)} rows, {bad} unparsed, {links} links")
all_rows = [r for name in URL for r in parsed[name][0]]
check("every id is 'jr:' + jobright's 24-hex id", all(re.fullmatch(r"jr:[0-9a-f]{24}", r["id"]) for r in all_rows))
check("ids are unique across the five lists", len({r["id"] for r in all_rows}) == len(all_rows) == 157)
check("url = https://jobright.ai/jobs/info/<id>, the utm_ query stripped",
      all(r["url"] == "https://jobright.ai/jobs/info/" + r["id"][3:] for r in all_rows))
check("each row has exactly the documented keys, and its list's name",
      all(set(r) == {"id", "title", "company", "location", "work_model", "posted", "url", "list"} for r in all_rows)
      and all(r["list"] == name for name in URL for r in parsed[name][0]))
check("every date parsed: Sep 23 .. Sep 29, 2026 (the README's 7-day window)",
      {r["posted"] for r in all_rows} == {f"2026-09-{d}" for d in range(23, 30)})
check("work models are the README's three", {r["work_model"] for r in all_rows} == {"On Site", "Hybrid", "Remote"})

eng, sales, swe, cons, sup = (parsed[n][0] for n in ("Engineering", "Sales", "Software-Engineer", "Consultant", "Support"))
check("a row, field by field", eng[0] == {
    "id": "jr:6a9755d3f5337b2cf73219df", "title": "Automation Engineer", "company": "ASC Machine Tools, Inc",
    "location": "Spokane Valley, WA, United States", "work_model": "On Site", "posted": "2026-09-29",
    "url": "https://jobright.ai/jobs/info/6a9755d3f5337b2cf73219df", "list": "Engineering"}, str(eng[0]))
lines = fixture_text("Consultant").splitlines()
check("'↳' rows take the company above them (four Terminix rows, three written '↳')",
      [r["company"] for r in cons[:4]] == ["Terminix"] * 4
      and sum(1 for line in lines if line.startswith("| ↳ | **[Pest Control Consultant]")) == 3)
check("...also across different titles (Amazon -> Network Install Technician; Trane -> the Laval row)",
      find(eng, "Network Install Technician")[0]["company"] == "Amazon"
      and sales[3]["company"] == "Trane Technologies" and sales[3]["location"] == "Laval, QC, Canada")
check("'|' inside a company name: 'Savers | Value Village', 'ISS | Institutional Shareholder Services'",
      [r["title"] for r in sup if r["company"] == "Savers | Value Village"]
      == ["GreenDrop Customer Service Attendant", "Retail Warehouse & Production Associate"]
      and find(sales, "Business Development Representative")[0]["company"] == "ISS | Institutional Shareholder Services"
      and find(eng, "Associate Engineer")[0]["company"] == "DELTA |v| Forensic Engineering")
check("an extra cell belongs to the location (Trexon: 'CO - Longmont | Integrated Cable Systems')",
      [(r["location"], r["work_model"], r["posted"]) for r in find(eng, "ICS Production Assembler", "Trexon")]
      == [("CO - Longmont | Integrated Cable Systems", "On Site", "2026-09-28")])
check("...and an unspaced '|' inside a location (Banfield: 'FL|005044, United States of America')",
      [r["location"] for r in sup if r["location"].startswith("Banfield ")]
      == ["Banfield Hollywood FL | 005044, United States of America",
          "Banfield Trussville AL | 001334, United States of America"])
split_line = next(line for line in fixture_text("Sales").splitlines() if line.startswith("| **[Nike-LA]"))
check("the Sales README splits one title across two lines (the first line has no job link)",
      JOB_LINK not in split_line)
check("...and that row is rejoined, not lost",
      find(sales, "Retail Associate, SEAS - Nike Citadel R-93180", "Nike-LA")
      and find(sales, "Retail Associate, SEAS - Nike Citadel R-93180")[0]["id"] == "jr:6ab9d3c739fd8792cb741069"
      and find(sales, "Retail Associate, SEAS - Nike Citadel R-93180")[0]["location"] == "Los Angeles, CA, United States")
check("HTML entities are decoded ('&amp;' -> '&')",
      find(eng, "Test Technician (2nd Shift & Part Time)", "Parker Hannifin")
      and find(eng, "Appliance & Refrigeration Repair Tech - Full & Part Time", "Sears")
      and not any("&amp;" in r["title"] or "&amp;" in r["company"] for r in all_rows))


# ─────────────────────────────────────────────────────────────────────────────
section("parse_readme: edge cases the real files do not have")
SYN = "\n".join(["# A made-up list", "", *HEADER,
                 "| ↳ | **[Orphan Engineer](https://jobright.ai/jobs/info/00000000000000000000a001)** | Austin, TX | "
                 "On Site | Sep 29 |",
                 syn_row("Acme Networks", "Associate Network Engineer", "00000000000000000000a002"),
                 syn_row("↳", "Junior Network Engineer", "00000000000000000000a003", loc="United States", wm="Remote"),
                 "| **[Acme Networks](https://example.com)** | **[Broken Link Engineer](https://jobright.ai/jobs/info/"
                 "00000000000000000000a004 | Austin, TX | On Site | Sep 29 |",
                 "| **[Acme Networks](https://example.com)** | **[Short Row Engineer](https://jobright.ai/jobs/info/"
                 "00000000000000000000a005)** | On Site | Sep 29 |",
                 syn_row("", "Blank Company Engineer", "00000000000000000000a006"),
                 syn_row("↳", "Follows A Blank Company", "00000000000000000000a007"),
                 syn_row("Initech", "Network Engineer", "00000000000000000000A008"),
                 syn_row("Initech", "Systems Administrator", "00000000000000000000a009", posted="Sept 29"),
                 "A job link outside the table: https://jobright.ai/jobs/info/00000000000000000000a00a",
                 "| **[Initech](https://example.com)** | **[Last Row Engineer](https://jobright.ai/jobs/info/"
                 "00000000000000000000a00b)** | Austin, TX | On Site | Sep 28",
                 "<!-- TABLE_END -->", ""])
rows, bad = jobright.parse_readme(SYN, "Synthetic", TODAY)
check("good rows parse; the header, the separator and prose lines are not rows",
      [r["id"][3:] for r in rows] == ["00000000000000000000a002", "00000000000000000000a003",
                                      "00000000000000000000a009", "00000000000000000000a00b"], str(rows))
check("six broken job-link rows are counted as unparsed (the canary's input)", bad == 6, str(bad))
check("...an orphan '↳' is not stored with an empty company", not find(rows, "Orphan Engineer"))
check("...nor a blank company cell, nor the '↳' row after it",
      not find(rows, "Blank Company Engineer") and not find(rows, "Follows A Blank Company"))
check("...an id with an uppercase tail is not cut short into a wrong id", not find(rows, "Network Engineer", "Initech"))
check("an unreadable date leaves the row in, undated", find(rows, "Systems Administrator")[0]["posted"] is None)
check("a last row without its closing '|' does not swallow the comment after it",
      find(rows, "Last Row Engineer")[0]["posted"] == "2026-09-28")
check("CRLF line endings read the same", jobright.parse_readme(SYN.replace("\n", "\r\n"), "Synthetic", TODAY) == (rows, bad))
check("an empty or missing README is 0 rows, 0 unparsed",
      jobright.parse_readme("", "X", TODAY) == ([], 0) and jobright.parse_readme(None, "X", TODAY) == ([], 0))


# ─────────────────────────────────────────────────────────────────────────────
section("rows_to_jobs: the filter, one copy per posting, and the job dicts")
EXPECTED = {
    "Engineering": ({"SYSTEMS_IT": 3, "SECURITY": 4, "DATA_CENTER": 2, "SUPPORT_ENG": 2, "NETWORK_INFRA": 4,
                     "SALES_SOLUTIONS": 4},
                    {"off-family": 15, "non-US location": 2, "seniority/leadership title": 2,
                     "below the engineer floor (technician)": 2, "internship/co-op title": 2, "level II+/2+ title": 1,
                     "duplicate within the list": 6}),
    "Sales": ({"SALES_SOLUTIONS": 14, "SECURITY": 1},
              {"off-family": 6, "non-US location": 3, "seniority/leadership title": 1, "duplicate within the list": 8}),
    "Software-Engineer": ({"SALES_SOLUTIONS": 6, "NETWORK_INFRA": 6},
                          {"non-US location": 3, "off-family": 5, "duplicate within the list": 1}),
    "Consultant": ({"SALES_SOLUTIONS": 4, "SECURITY": 2},
                   {"off-family": 7, "non-US location": 4, "seniority/leadership title": 1,
                    "duplicate within the list": 1}),
    "Support": ({"SUPPORT_ENG": 12},
                {"non-US location": 5, "support list: not a support-engineer title": 17, "duplicate within the list": 1}),
}
results, samples = {}, {}
for name in URL:
    samples[name] = {}
    results[name] = to_jobs(name, samples=samples[name])
    jobs, dropped = results[name]
    fams, drops = EXPECTED[name]
    check(f"{name}: kept {sum(fams.values())} by family {fams}",
          Counter(j["family"] for j in jobs) == Counter(fams), str(Counter(j["family"] for j in jobs)))
    check(f"{name}: dropped by rule as expected", Counter(dropped) == Counter(drops), str(dict(dropped)))
    check(f"{name}: every row is accounted for (kept + dropped == rows)",
          len(jobs) + sum(dropped.values()) == len(parsed[name][0]))
all_jobs = [j for name in URL for j in results[name][0]]
check("64 candidates across the five lists", len(all_jobs) == 64, str(len(all_jobs)))

impact = next(j for j in results["Software-Engineer"][0] if j["company"] == "impact.com")
check("a job dict, field by field (README fields only; the work model joins the location)", impact == {
    "id": "jr:6ab68370634ec6aa7c0d2dec", "title": "Associate Solutions Architect", "company": "impact.com",
    "location": "United States (Remote)", "url": "https://jobright.ai/jobs/info/6ab68370634ec6aa7c0d2dec",
    "apply_url": None, "posted_at": "2026-09-27T00:00:00+00:00", "description": None, "is_easy_apply": False,
    "logo_url": None, "search_term": "jobright:Software-Engineer", "source": "jobright", "family": "SALES_SOLUTIONS",
    "norm_key": make_norm_key("impact.com", "Associate Solutions Architect")}, str(impact))
check("' (Hybrid)' for a hybrid row, nothing for on-site",
      next(j for j in all_jobs if j["company"] == "Ronco")["location"] == "Albany, NY, United States (Hybrid)"
      and next(j for j in all_jobs if j["company"] == "Verkada")["location"] == "San Mateo, CA, United States")
check("every job: title-only (no description, no apply link), source jobright, search_term jobright:<list>",
      all(j["description"] is None and j["apply_url"] is None and j["is_easy_apply"] is False
          and j["logo_url"] is None and j["source"] == "jobright" for j in all_jobs)
      and all(j["search_term"] == f"jobright:{name}" for name in URL for j in results[name][0]))
check("every job's key is db.make_norm_key(company, title), keyed full-time ('|ft')",
      all(j["norm_key"] == make_norm_key(j["company"], j["title"]) and j["norm_key"].endswith("|ft") for j in all_jobs))
check("every job is in an allowed family -- never PROGRAM (no program pass-through here)",
      all(j["family"] in title_gate.ALLOWED_FAMILIES for j in all_jobs))
check("every job links jobright.ai/jobs/info/<id> without tracking",
      all(j["url"] == "https://jobright.ai/jobs/info/" + j["id"][3:] for j in all_jobs))


def dropped_as(name, rule, text):
    return any(line.startswith(text) for line in samples[name].get(rule, []))


check("non-US rows drop first, even a support-engineer title (Focusrite, UK)",
      dropped_as("Support", "non-US location", "Focusrite | Technical Support Engineer Placement | High Wycombe")
      and dropped_as("Consultant", "non-US location", "Vena Solutions | Associate Consultant - UK"))
check("the engineer floor drops technician titles",
      dropped_as("Engineering", "below the engineer floor (technician)", "Amazon | Network Install Technician")
      and dropped_as("Engineering", "below the engineer floor (technician)", "Amazon | Data Center Technician"))
check("internships, seniority and level II+ drop",
      dropped_as("Engineering", "internship/co-op title", "Bayer | Systems Engineer Co-Op")
      and dropped_as("Sales", "seniority/leadership title", "DDN | Senior Sales Engineer")
      and dropped_as("Engineering", "level II+/2+ title", "S3I Engineering | Systems Engineer II"))
check("program titles are NOT passed through on jobright (off-family here)...",
      dropped_as("Engineering", "off-family", "Entegris | Entegris Leadership Development Program - Engineering")
      and dropped_as("Engineering", "off-family", "FlatironDragados | New College Grad Field Engineer"))
check("...while the ATS setting would have kept them as PROGRAM",
      title_gate.source_gate("Entegris Leadership Development Program - Engineering", "Entegris",
                             "Colorado Springs, CO, United States", "", program_passthrough=True) == (True, "PROGRAM")
      and title_gate.source_gate("New College Grad Field Engineer", "FlatironDragados", "Port Arthur, TX, United States",
                                 "", program_passthrough=True) == (True, "PROGRAM"))
sup_rows = parsed["Support"][0]
rule = "support list: not a support-engineer title"
check("Support list: help desk, IT support and customer service drop",
      dropped_as("Support", rule, "Terrestris LLC | Remote IT Help Desk Specialist")
      and dropped_as("Support", rule, "AET | IT Support Specialist")
      and dropped_as("Support", rule, "National Fitness Partners | FT Customer Service Representative"))
lenient, _ = jobright.rows_to_jobs(sup_rows, "Support", support_only=False, today=TODAY)
extra = sorted((j["company"], j["family"]) for j in lenient if j["id"] not in {x["id"] for x in results["Support"][0]})
check("...and so do family titles that are not support-ENGINEER ones (kept without the Support flag)",
      extra == [("Google", "SALES_SOLUTIONS"), ("Viasat", "SUPPORT_ENG"), ("Voxai Solutions", "NETWORK_INFRA")], str(extra))
check("Support list keeps support-engineer titles (incl. 'Technical Services Engineer', 'Tech Support Engineer')",
      {"Intaker", "TD SYNNEX North America", "EnsoData", "Yaskawa America, Inc. - Drives & Motion Division"}
      <= {j["company"] for j in results["Support"][0]}
      and all(title_gate.SUPPORT_ENGINEER_RE.search(j["title"]) for j in results["Support"][0]))

oracle = [r for r in eng if r["company"] == "Oracle"]
kept_oracle = [j for j in results["Engineering"][0] if j["company"] == "Oracle"]
check("one copy per company + title: Oracle's four 'Support Engineer 1' rows -> the first",
      len(oracle) == 4 and [j["id"] for j in kept_oracle] == [oracle[0]["id"]])
check("...two companies with the same title are two postings (ICIMS, Rural King)",
      sorted(j["company"] for j in results["Support"][0] if j["title"] == "Associate Technical Support Engineer - Core")
      == ["ICIMS", "Rural King"])
same = [{"id": f"jr:{i:024x}", "title": "Associate Network Engineer", "company": "Acme Networks", "location": loc,
         "work_model": "On Site", "posted": "2026-09-29", "url": f"https://jobright.ai/jobs/info/{i:024x}", "list": "X"}
        for i, loc in ((1, "Remote"), (2, "Austin, TX, United States"), (3, "Denver, CO, United States"))]
jobs, dropped = jobright.rows_to_jobs(same, "X", support_only=False, today=TODAY)
check("...the first copy with a confirmed U.S. location wins over an unknown one",
      [j["id"] for j in jobs] == [same[1]["id"]] and dropped == Counter({jobright.DUPLICATE_RULE: 2}), str(jobs))
twice = [dict(same[1]), dict(same[1], title="Junior Network Engineer")]
jobs, dropped = jobright.rows_to_jobs(twice, "X", support_only=False, today=TODAY)
check("...and one id listed twice is one job", len(jobs) == 1 and dropped == Counter({jobright.DUPLICATE_RULE: 1}))

check("the age rule's name says the limit (and testkit's fake jobright uses the same text)",
      jobright.STALE_RULE == "posted more than 10 days ago" == testkit.FakeJobright.STALE_RULE)
jobs, dropped = to_jobs("Engineering", today=date(2026, 10, 9))
check("read on 2026-10-09: rows posted Sep 23-28 are stale (12), Sep 29 (10 days) still counts",
      dropped[jobright.STALE_RULE] == 12 and jobs and all(j["posted_at"].startswith("2026-09-29") for j in jobs),
      str(dropped))
jobs, dropped = to_jobs("Engineering", today=date(2026, 10, 10))
check("read on 2026-10-10: every row is stale, nothing reaches the gate",
      jobs == [] and dropped == Counter({jobright.STALE_RULE: 49}), str(dropped))
undated = dict(same[1], posted=None)
tomorrow = dict(same[2], title="Junior Network Engineer", posted="2026-10-01")
jobs, _ = jobright.rows_to_jobs([undated, tomorrow], "X", support_only=False, today=TODAY)
check("an undated row and a row dated tomorrow (skew) are kept",
      [j["posted_at"] for j in jobs] == [None, "2026-10-01T00:00:00+00:00"])

many = [dict(same[1], id=f"jr:{i:024x}", title=f"Pastry Chef {i}") for i in range(45)]
kept_samples: dict = {}
jobs, dropped = jobright.rows_to_jobs(many, "X", support_only=False, today=TODAY, samples=kept_samples)
check("dropped samples: 'company | title | location', at most SAMPLES_PER_RULE per rule, count is the total",
      dropped["off-family"] == 45 and len(kept_samples["off-family"]) == jobright.SAMPLES_PER_RULE == 40
      and kept_samples["off-family"][0] == "Acme Networks | Pastry Chef 0 | Austin, TX, United States")
check("samples=None collects nothing and changes nothing",
      jobright.rows_to_jobs(many, "X", support_only=False, today=TODAY) == (jobs, dropped))


# ─────────────────────────────────────────────────────────────────────────────
section("fetch_readme: raw.githubusercontent.com only, conditional GET")


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"", etag=None):
        self.status_code = status
        self.content = body
        self.headers = CaseInsensitiveDict({"ETag": etag} if etag else {})


class FakeGet:
    """Stands in for requests.get. `routes` = {url: (body_bytes, etag)}: a GET whose If-None-Match equals the
    etag gets a 304, otherwise a 200; unknown URLs get a 404. `raise_for` = {url: exception}. Every call is
    recorded as (url, kwargs), and in `log` when given, to order it against a Pipeline's calls."""

    def __init__(self, routes=None, raise_for=None, status_for=None, log=None):
        self.routes = dict(routes or {})
        self.raise_for = dict(raise_for or {})
        self.status_for = dict(status_for or {})
        self.calls: list = []
        self.log = log

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.log is not None:
            self.log.append(("requests.get", url))
        if url in self.raise_for:
            raise self.raise_for[url]
        if url in self.status_for:
            return FakeResponse(self.status_for[url])
        if url not in self.routes:
            return FakeResponse(404)
        body, etag = self.routes[url]
        if etag and (kwargs.get("headers") or {}).get("If-None-Match") == etag:
            return FakeResponse(304, b"", etag)
        return FakeResponse(200, body, etag)

    def hosts(self):
        return {urlparse(u).hostname for u, _k in self.calls}


RAW = URL["Consultant"]
body = fixture_text("Consultant").encode("utf-8")
get = FakeGet({RAW: (body, 'W/"consultant-1"')})
with patched(requests, get=get):
    status, text, etag = jobright.fetch_readme(RAW)
url0, kw = get.calls[0]
check("200: (200, the README text, the response's ETag)",
      (status, etag) == (200, 'W/"consultant-1"') and text == fixture_text("Consultant"))
check("...decoded as UTF-8 from the bytes ('↳' intact, whatever the response's charset)", "| ↳ |" in text)
check("...no If-None-Match without a stored ETag; the bot's User-Agent; timeout 30 s; no redirects followed",
      "If-None-Match" not in kw["headers"] and kw["headers"]["User-Agent"] == jobright.USER_AGENT
      and "github.com/Skirozik/job-alert-bot" in jobright.USER_AGENT and kw["timeout"] == 30
      and kw["allow_redirects"] is False)
with patched(requests, get=get):
    status, text, etag = jobright.fetch_readme(RAW, 'W/"consultant-1"')
check("with the stored ETag: If-None-Match is sent, and a 304 -> (304, '', that ETag)",
      get.calls[-1][1]["headers"].get("If-None-Match") == 'W/"consultant-1"'
      and (status, text, etag) == (304, "", 'W/"consultant-1"'))
with patched(requests, get=get):
    status, text, etag = jobright.fetch_readme(RAW, 'W/"consultant-0"')
check("a stale ETag gets the new README and the new ETag", (status, etag) == (200, 'W/"consultant-1"') and text)
with patched(requests, get=FakeGet()):
    result = jobright.fetch_readme(URL["Sales"])
check("404 -> (404, '', None)", result == (404, "", None))
moved = FakeGet(status_for={RAW: 301})
with patched(requests, get=moved):
    result = jobright.fetch_readme(RAW)
check("a redirect is not followed: (301, '', None) after exactly one request",
      result == (301, "", None) and len(moved.calls) == 1)
boom = FakeGet(raise_for={RAW: requests.ConnectionError("SECRET-DETAIL while connecting")})
with patched(requests, get=boom), captured_logs() as logs:
    result = jobright.fetch_readme(RAW)
check("a failed request -> (0, '', None), logged by exception type only",
      result == (0, "", None) and "ConnectionError" in logs.text() and "SECRET-DETAIL" not in logs.text())

REFUSED = ["https://jobright.ai/jobs/info/6a9755d3f5337b2cf73219df", "https://jobright.ai/",
           "http://raw.githubusercontent.com/jobright-ai/2026-Sales-New-Grad/master/README.md",
           "https://raw.githubusercontent.com.example.net/README.md",
           "https://github.com/jobright-ai/2026-Sales-New-Grad/blob/master/README.md",
           "https://raw.githubusercontent.com@jobright.ai/jobs/info/6a9755d3f5337b2cf73219df",
           "https://jobright.ai@raw.githubusercontent.com/jobright-ai/x/master/README.md",
           "https://raw.githubusercontent.com:8443/jobright-ai/x/master/README.md",
           "https://[::1", "", None, "not a url"]
refuse = FakeGet()
with patched(requests, get=refuse), captured_logs() as logs:
    results_refused = [jobright.fetch_readme(u) for u in REFUSED]
check("any other host, scheme, port or user info -> (0, '', None) without a request",
      all(r == (0, "", None) for r in results_refused) and refuse.calls == [], str(refuse.calls))
check("...and the refusal is logged", logs.text().count("refusing to request") == len(REFUSED))


# ─────────────────────────────────────────────────────────────────────────────
section("canary_problems: when a list needs the owner")
canary = jobright.canary_problems
check("a clean 200 and a 304 are healthy", canary(200, 100, 0) == [] and canary(304, 0, 0) == [])
check("HTTP status other than 200/304", canary(404, 0, 0) == ["HTTP 404"] and canary(301, 0, 0) == ["HTTP 301"])
check("no response at all", canary(0, 0, 0) == ["no HTTP response (request failed or refused)"])
check("a 200 with nothing parsed", canary(200, 0, 0) == ["HTTP 200 with 0 parsed rows"])
check("exactly 5 % unparsed is tolerated, more is format drift",
      canary(200, 95, 5) == [] and canary(200, 94, 6) == ["format drift: 6 of 100 job-link rows unparsed"])
check("both at once", canary(200, 0, 3) == ["HTTP 200 with 0 parsed rows", "format drift: 3 of 3 job-link rows unparsed"])
check("the five real READMEs are healthy", all(canary(200, len(parsed[n][0]), parsed[n][1]) == [] for n in URL))
check("the made-up list above (4 rows, 6 broken) is drift", canary(200, 4, 6) == ["format drift: 6 of 10 job-link rows unparsed"])


# ─────────────────────────────────────────────────────────────────────────────
section("main.py, dry run: the real module against the recorded READMEs")


class FixedDatetime(datetime):
    """main.datetime pinned to FIXED -- by default 2026-09-30 15:00 UTC, the day the fixtures were fetched."""

    FIXED = datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.FIXED if tz is not None else cls.FIXED.replace(tzinfo=None)


def readme_routes(version: int = 1, overrides=None):
    routes = {URL[n]: (fixture_text(n).encode("utf-8"), f'W/"{n}-{version}"') for n in URL}
    routes.update(overrides or {})
    return routes


p = Pipeline(real_db)
get = FakeGet(readme_routes(), log=p.log)
fdb, fcl, fno = Forbidden("db"), Forbidden("classifier"), Forbidden("notifier")
with env_cleared(), p.install(main), patched(main, jobright=jobright, db=fdb, classifier=fcl, notifier=fno,
                                             datetime=FixedDatetime), patched(requests, get=get), \
        captured_stdout() as out:
    try:
        code = main.run(["--dry-run", "--no-linkedin", "--no-ats", "--sample", "3"])
    except AssertionError as exc:     # a Forbidden module was touched: report, don't crash
        code = f"AssertionError: {exc}"
out = out.getvalue()
check("exit 0; db, classifier and notifier never touched", code == 0 and all(f.touched == [] for f in (fdb, fcl, fno)),
      str(code))
check("five GETs, one per list, in config order", [u for u, _k in get.calls] == [u for _n, u, _s in LISTS])
check("every request went to raw.githubusercontent.com; none to jobright.ai",
      get.hosts() == {"raw.githubusercontent.com"} and not any("jobright.ai" in u for u, _k in get.calls))
check("no ETag in a dry run", not any("If-None-Match" in k["headers"] for _u, k in get.calls))
check("paced 1-2 s between lists", p.clock.sleeps == [1.0] * 4, str(p.clock.sleeps))
check("the jobright summary line", "jobright: 5 lists | rows 157 | kept 64 | new in run 64 | dropped: " in out, out[:600])
check("one line per list: HTTP, rows, in window, kept, new, canary",
      "  jobright Engineering: HTTP 200 | rows 49 (0 unparsed) | in window 49 | kept 19 | new 19 | canary: none" in out
      and "  jobright Support: HTTP 200 | rows 35 (0 unparsed) | in window 35 | kept 12 | new 12 | canary: none" in out)
check("...with that list's drops by rule",
      "    dropped: below the engineer floor (technician) 2, duplicate within the list 6, internship/co-op title 2, "
      "level II+/2+ title 1, non-US location 2, off-family 15, seniority/leadership title 2" in out)
check("what the cap allows", "would classify: ats 0 / linkedin 0 / jobright 64; leftover 0" in out)
check("kept samples in queue order: entry-marked, primary family first",
      "kept sample (jobright, 3 of 64): company | title | location | family\n"
      "  Motorola Solutions | Presales Systems Engineer - Entry Level | Chicago, IL, United States (Remote) | SALES_SOLUTIONS\n"
      "  IBM | Solution Architect - Entry Level Sales Program 2027 | New York, NY, United States (Hybrid) | SALES_SOLUTIONS\n"
      "  Motive | Associate Solutions Engineer, Commercial (Nashville - Onsite) | Nashville, Tennessee, United States | "
      "SALES_SOLUTIONS" in out, out)
check("dropped samples per jobright rule, with the rule's total across lists",
      "dropped sample (jobright, support list: not a support-engineer title, 3 of 17):\n"
      "  National Fitness Partners | FT Customer Service Representative | Montgomeryville, PA, United States" in out
      and "dropped sample (jobright, off-family, 3 of 33):" in out)

p = Pipeline(real_db)
with env_cleared(), p.install(main), patched(main, jobright=jobright, db=Forbidden("db"), datetime=FixedDatetime), \
        patched(FixedDatetime, FIXED=datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)), \
        patched(requests, get=FakeGet(readme_routes())), captured_stdout() as out:
    main.run(["--dry-run", "--no-linkedin", "--no-ats"])
out = out.getvalue()
check("read on 2026-10-09, the report counts only the rows inside the 10-day window",
      "  jobright Engineering: HTTP 200 | rows 49 (0 unparsed) | in window 37 | kept 15 |" in out
      and "posted more than 10 days ago 12" in out, out[:900])

p = Pipeline(real_db)
get = FakeGet(readme_routes(overrides={URL["Sales"]: (fixture_text("Software-Engineer").encode("utf-8"), 'W/"x"')}))
with env_cleared(), p.install(main), patched(main, jobright=jobright, db=Forbidden("db"), datetime=FixedDatetime), \
        patched(requests, get=get), captured_stdout() as out:
    main.run(["--dry-run", "--no-linkedin", "--no-ats"])
out = out.getvalue()
check("a posting an earlier list already produced this run is not new again (run-level dedup)",
      "  jobright Sales: HTTP 200 | rows 21 (0 unparsed) | in window 21 | kept 12 | new 12 |" in out
      and "  jobright Software-Engineer: HTTP 200 | rows 21 (0 unparsed) | in window 21 | kept 12 | new 0 |" in out, out)


# ─────────────────────────────────────────────────────────────────────────────
section("main.py, normal runs: ETag round trip, database dedup, title-only jobs")


def real_run(get, *, state=None, stored_ids=(), caps=None):
    """main.run([]) with every collaborator faked except jobright (and requests.get stubbed). No LinkedIn terms, no
    ATS candidates, so the jobright pass is the whole run."""
    p = Pipeline(real_db)
    p.db.state = dict(state or {})
    p.db.stored_ids = set(stored_ids)
    get.log = p.log
    values = dict(SUPABASE_URL="https://project.invalid", SUPABASE_SERVICE_KEY="service-key-under-test",
                  ANTHROPIC_API_KEY="anthropic-key-under-test", NTFY_TOPIC="brice-topic-under-test",
                  OWNER_NTFY_TOPIC="owner-topic-under-test", CANDIDATE_PROFILE_PATH=PROFILE, SEARCH_TERMS=[])
    if caps:
        values["MAX_CLASSIFY_PER_RUN"] = caps
    with patched(main.config, **values), p.install(main), patched(main, jobright=jobright, datetime=FixedDatetime), \
            patched(requests, get=get), captured_logs() as logs:
        code = main.run([])
    return code, p, logs


get = FakeGet(readme_routes())
code, p, logs = real_run(get)
classified = [d for n, d in p.log if n == "classifier.classify"]
check("exit 0; the five lists are read once each, with no ETag yet",
      code == 0 and len(get.calls) == 5 and not any("If-None-Match" in k["headers"] for _u, k in get.calls))
check("every request went to raw.githubusercontent.com; none to jobright.ai",
      get.hosts() == {"raw.githubusercontent.com"} and not any("jobright.ai" in u for u, _k in get.calls))
check("all 64 candidates are classified, after all five READMEs are read",
      len(classified) == 64 and all(c.startswith("jr:") for c in classified)
      and p.log.index(("classifier.classify", classified[0])) > max(
          i for i, (n, _d) in enumerate(p.log) if n == "requests.get"))
check("...each one title-only: the classifier sees no description",
      all(j["description"] is None for j in p.classifier.seen))
check("...and not one per-job fetch of any kind (never jobright.ai)",
      not p.linkedin.described and not p.ats_pass.workday_fetches and len(get.calls) == 5)
stored = {j["id"]: j for j in p.db.inserted}
check("stored rows carry the README fields and jobright:<list> as search_term",
      len(stored) == 64 and stored["jr:6ab68370634ec6aa7c0d2dec"]["search_term"] == "jobright:Software-Engineer"
      and stored["jr:6ab68370634ec6aa7c0d2dec"]["location"] == "United States (Remote)"
      and stored["jr:6ab68370634ec6aa7c0d2dec"]["posted_at"] == "2026-09-27T00:00:00+00:00")
check("the first run pings what it stores (the fake classifier says APPLY to all)", len(p.notifier.jobs) == 64)
check("each list's ETag is saved (nothing was left behind)",
      {k: v for k, v in p.db.state.items() if k.startswith("jobright_etag:")}
      == {f"jobright_etag:{n}": f'W/"{n}-1"' for n in URL})
check("the per-list log line", "jobright Engineering: HTTP 200 | 49 rows (0 unparsed, 49 in window) | kept 19 | new 19"
      in logs.text())
check("finish_run counts them", p.db.finished and p.db.finished[0][1]["jobright_candidates"] == 64
      and p.db.finished[0][1]["new_jobs"] == 64 and p.db.finished[0][1]["leftover"] == 0)
check("no owner alert on a healthy run", p.notifier.alerts == [])

state1, stored1 = dict(p.db.state), set(stored)
code, p, logs = real_run(get, state=state1, stored_ids=stored1)
check("next run: If-None-Match carries each stored ETag, every list answers 304",
      [k["headers"].get("If-None-Match") for _u, k in get.calls[5:]] == [f'W/"{n}-1"' for n in URL])
check("...so nothing is parsed or classified, no canary, and the ETags stay as they were",
      not p.calls("classifier.") and p.notifier.alerts == [] and not p.calls("db.set_state")
      and not p.calls("db.clear_state") and "not modified since the last complete read" in logs.text())

extra_row = syn_row("Initech", "Associate Network Engineer", "0000000000000000000000b1")
eng_v2 = fixture_text("Engineering").replace(HEADER[1] + "\n", HEADER[1] + "\n" + extra_row + "\n", 1)
get = FakeGet(readme_routes(overrides={URL["Engineering"]: (eng_v2.encode("utf-8"), 'W/"Engineering-2"')}))
code, p, logs = real_run(get, state=state1, stored_ids=stored1)
check("a changed README is re-read (200); rows already stored are skipped by the database lookup",
      [d for n, d in p.log if n == "classifier.classify"] == ["jr:0000000000000000000000b1"])
check("...and its new ETag replaces the old one",
      p.db.state["jobright_etag:Engineering"] == 'W/"Engineering-2"' and p.db.state["jobright_etag:Sales"] == 'W/"Sales-1"')

get = FakeGet(readme_routes())
code, p, logs = real_run(get, caps={"ats": 100, "linkedin": 180, "jobright": 60})
done = {d for n, d in p.log if n == "classifier.classify"}
left_lists = {n for n in URL if any(j["id"] not in done for j in results[n][0])}
check("a capped run (60) classifies 60 and leaves the other 4 unstored",
      len(done) == 60 and len(p.db.inserted) == 60 and p.db.finished[0][1]["leftover"] == 4)
check("...lists with leftovers have their ETag cleared (the next run re-reads them); the others keep theirs",
      left_lists and set(URL) - left_lists
      and all(f"jobright_etag:{n}" not in p.db.state and ("db.clear_state", f"jobright_etag:{n}") in p.log
              for n in left_lists)
      and all(p.db.state.get(f"jobright_etag:{n}") == f'W/"{n}-1"' for n in set(URL) - left_lists), str(left_lists))

drift = re.sub(r"\)\*\* \|", ") |", fixture_text("Sales"))          # every title link loses its closing "**"
get = FakeGet(readme_routes(overrides={URL["Sales"]: (drift.encode("utf-8"), 'W/"Sales-9"')}),
              status_for={URL["Consultant"]: 404})
code, p, logs = real_run(get)
titles = [t for _m, t, _p in p.notifier.alerts]
check("a broken list (404) and a drifted one (0 rows parsed) each alert the owner, not Brice",
      sorted(titles) == ["Brice: jobright Consultant list", "Brice: jobright Sales list"]
      and not any(j["id"] in {r["id"] for r in parsed["Sales"][0]} for j in p.notifier.jobs), str(titles))
check("...naming what is wrong",
      any("HTTP 404" in m for m, _t, _p in p.notifier.alerts)
      and any("HTTP 200 with 0 parsed rows" in m and "format drift: 33 of 33" in m for m, _t, _p in p.notifier.alerts))
check("...while the healthy lists are still processed",
      {d for n, d in p.log if n == "classifier.classify"} == {j["id"] for n in ("Engineering", "Software-Engineer", "Support")
                                                                 for j in results[n][0]})
state_after = dict(p.db.state)
code, p, logs = real_run(get, state=state_after, stored_ids={j["id"] for j in all_jobs})
check("...and the same problem a run later stays quiet (24 h throttle)",
      p.notifier.alerts == [] and "CANARY: HTTP 404" in logs.text()
      and "Owner alert throttled (alert_at:jobright:Consultant)" in logs.text())


# ─────────────────────────────────────────────────────────────────────────────
section("classifier.classify on a jobright job: title-only, capped at APPLY_CAVEAT")
calls: list = []


def stub_client(tier, reason):
    def create(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="tool_use",
                                                                    input={"tier": tier, "reason": reason})])
    return types.SimpleNamespace(messages=types.SimpleNamespace(create=create))


motorola = next(j for j in results["Engineering"][0] if j["company"] == "Motorola Solutions")
verdicts = {}
with patched(classifier, _profile="PROFILE PLACEHOLDER", _API_HARD_DOWN=None,
             time=types.SimpleNamespace(sleep=lambda s: None)):
    for tier, reason in (("APPLY", "fit"), ("APPLY_CAVEAT", "asks for a CCNA"), ("INELIGIBLE", "I-5: sales quota role")):
        with patched(classifier, _get_client=lambda t=tier, r=reason: stub_client(t, r)):
            verdicts[tier] = classifier.classify(dict(motorola))
prompt = calls[0]["messages"][0]["content"] if calls else ""
check("the model reads the README fields and is told there is no description",
      "Title: Presales Systems Engineer - Entry Level" in prompt and "Company: Motorola Solutions" in prompt
      and "Location: Chicago, IL, United States (Remote)" in prompt
      and "Description: (not available — classify on title/company/location only)" in prompt)
check("APPLY on the title alone becomes APPLY_CAVEAT (a silent ping)",
      verdicts["APPLY"]["tier"] == "APPLY_CAVEAT"
      and verdicts["APPLY"]["reason"] == "Title-only: no description available — check the posting")
check("APPLY_CAVEAT and INELIGIBLE are left as the model gave them",
      (verdicts["APPLY_CAVEAT"]["tier"], verdicts["APPLY_CAVEAT"]["reason"]) == ("APPLY_CAVEAT", "asks for a CCNA")
      and (verdicts["INELIGIBLE"]["tier"], verdicts["INELIGIBLE"]["reason"]) == ("INELIGIBLE", "I-5: sales quota role"))


# ─────────────────────────────────────────────────────────────────────────────
section("jobright.py makes one kind of request, and parses as Python 3.12")
src = (testkit.HERE / "jobright.py").read_text(encoding="utf-8")
tree = ast.parse(src, feature_version=(3, 12))
http_calls = [f"{n.func.value.id}.{n.func.attr}" for n in ast.walk(tree) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
              and n.func.value.id in ("requests", "urllib", "httpx", "http", "socket", "session")]
check("exactly one HTTP call in the module: requests.get (in fetch_readme)", http_calls == ["requests.get"], str(http_calls))
modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {
    n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
other_clients = sorted(m for m in modules if m.split(".")[0] in {"urllib3", "httpx", "http", "socket", "aiohttp", "bs4"}
                       or m in ("urllib", "urllib.request"))
check("no other HTTP client is imported (urllib.parse only parses)", not other_clients and "urllib.parse" in modules,
      str(sorted(modules)))
check("this test file parses as Python 3.12 too",
      bool(ast.parse(Path(__file__).read_text(encoding="utf-8"), feature_version=(3, 12))))
check("the fixtures are LF-only UTF-8", all(b"\r" not in (FIXTURES / f"2026-{n}-New-Grad.md").read_bytes() for n in URL))

shutil.rmtree(TMP, ignore_errors=True)
sys.exit(testkit.finish())
