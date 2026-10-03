"""main.py end to end, with every collaborator replaced by a recording fake.

Offline: db, classifier, notifier, linkedin, ats_pass and jobright are fakes
(testkit.Pipeline); time runs on a FakeClock; random.uniform returns its lower
bound. The dry-run cases replace db, classifier and notifier with modules whose
every attribute access fails, and blank every secret.

Run:  cd scraper_brice && python -X utf8 test_main_flow.py
"""

import testkit

testkit.block_network()

import contextlib  # noqa: E402
import random  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

import db as real_db  # noqa: E402
import main  # noqa: E402
from testkit import (Forbidden, Pipeline, ats_job, captured_logs, captured_stdout, check, env_cleared,  # noqa: E402
                     jr_job, li_job, patched, section)

TMP = Path(tempfile.mkdtemp(prefix="brice_main_flow_"))
PROFILE = TMP / "rubric.md"
PROFILE.write_text("placeholder rubric for preflight\n", encoding="utf-8")

LIST_A = ("ListA", "https://raw.githubusercontent.com/example/list-a/master/README.md", False)
LIST_B = ("ListB", "https://raw.githubusercontent.com/example/list-b/master/README.md", True)


@contextlib.contextmanager
def configured(**overrides):
    """A complete (fake) configuration: every preflight name set, two terms, two jobright lists."""
    values = dict(SUPABASE_URL="https://project.invalid", SUPABASE_SERVICE_KEY="service-key-under-test",
                  ANTHROPIC_API_KEY="anthropic-key-under-test", NTFY_TOPIC="brice-topic-under-test",
                  OWNER_NTFY_TOPIC="owner-topic-under-test", CANDIDATE_PROFILE_PATH=PROFILE,
                  SEARCH_TERMS=["term one", "term two"], LOCATIONS=["United States"],
                  JOBRIGHT_LISTS=[LIST_A, LIST_B])
    values.update(overrides)
    with patched(main.config, **values):
        yield


def first(log, name):
    return next((i for i, (n, _d) in enumerate(log) if n == name), None)


def names(pairs):
    return [n for n, _d in pairs]


# ─────────────────────────────────────────────────────────────────────────────
section("(a) dry run: no database, no Claude, no ntfy, no secrets")


def dry(argv, *, pages=None, ats=None, lists=None, use_random=False):
    p = Pipeline(real_db)
    p.linkedin.pages = pages or (lambda term, start: [li_job((0 if term == "term one" else 500) + start + i,
                                                             "Associate Sales Engineer") for i in range(10)])
    p.ats_pass.candidates = ats if ats is not None else [ats_job(1, "Associate Network Engineer", "Acme")]
    p.jobright.lists = lists if lists is not None else {
        "ListA": {"url": LIST_A[1], "status": 200, "etag": "A1", "rows": 3,
                  "jobs": [jr_job(1, "Associate Solutions Engineer", "Delta", "ListA")]},
        "ListB": {"url": LIST_B[1], "status": 200, "etag": "B1", "rows": 2, "jobs": []}}
    if use_random:
        p.random = random
    fdb, fcl, fno = Forbidden("db"), Forbidden("classifier"), Forbidden("notifier")
    with env_cleared(), configured(SUPABASE_URL="", SUPABASE_SERVICE_KEY="", ANTHROPIC_API_KEY="", NTFY_TOPIC="",
                                   OWNER_NTFY_TOPIC="", CANDIDATE_PROFILE_PATH=TMP / "no-such-rubric.md"), \
            p.install(main), patched(main, db=fdb, classifier=fcl, notifier=fno), captured_stdout() as out:
        try:
            code = main.run(argv)
        except SystemExit as exc:
            code = exc.code
        except AssertionError as exc:       # a Forbidden module was touched: report, don't crash
            code = f"AssertionError: {exc}"
    return code, out.getvalue(), p, (fdb, fcl, fno)


code, out, p, forb = dry(["--dry-run", "--linkedin-terms", "2", "--linkedin-pages", "2"])
check("exit 0 with every secret blank and no rubric file (no preflight)", code == 0, str(code))
check("db, classifier and notifier were never touched", all(f.touched == [] for f in forb),
      str([f.touched for f in forb]))
check("the report names the mode and every source",
      out.startswith("=== DRY RUN — no database, no Claude, no ntfy ===")
      and "\nats:" in out and "\nlinkedin:" in out and "\njobright:" in out, out[:300])
check("...per-source counts",
      "ats:      26 boards (26 with listings) | raw 1000 | kept 1 | new in run 1" in out
      and "linkedin: 2 searches (0 rate limited) | raw 40 |" in out
      and "jobright: 2 lists | rows 5 | kept 1 | new in run 1" in out, out)
check("...what the caps would allow", "would classify: ats 1 / linkedin 40 / jobright 1; leftover 0" in out)
check("...and kept samples as 'company | title | location | family'",
      "Acme | Associate Network Engineer | Austin, TX | NETWORK_INFRA" in out
      and "Delta | Associate Solutions Engineer | Remote - US | SALES_SOLUTIONS" in out)
check("jobright is read without an ETag (no bot_state in a dry run)", all(etag is None for _u, etag in p.jobright.fetches))
starts = [(term, start) for _t, term, _l, start in p.linkedin.requests]
check("--linkedin-pages 2 / --linkedin-terms 2: two pages of each of the first two terms",
      starts == [("associate sales engineer", 0), ("associate sales engineer", 10), ("sales engineer", 0),
                 ("sales engineer", 10)] or starts == [("term one", 0), ("term one", 10), ("term two", 0),
                                                        ("term two", 10)], str(starts))
gaps = [b[0] - a[0] for a, b in zip(p.linkedin.requests, p.linkedin.requests[1:])]
check("every LinkedIn request is >= 5.0 s after the previous one", gaps and min(gaps) >= 5.0, str(gaps))

code, out, p, forb = dry(["--dry-run", "--linkedin-pages", "1"], use_random=True)
gaps = [b[0] - a[0] for a, b in zip(p.linkedin.requests, p.linkedin.requests[1:])]
check("...also with real random pacing (5.0-6.0 s)", gaps and min(gaps) >= 5.0 and max(gaps) <= 6.0, str(gaps))
check("--linkedin-pages 1: only page 0 of each term", {s for _t, _k, _l, s in p.linkedin.requests} == {0})

pages_seen = []


def empty_then_full(term, start):
    pages_seen.append((term, start))
    return [] if len(pages_seen) == 1 else [li_job(100 + len(pages_seen), "Junior Network Engineer")]


code, out, p, forb = dry(["--dry-run", "--linkedin-terms", "1"], pages=empty_then_full)
gaps = [b[0] - a[0] for a, b in zip(p.linkedin.requests, p.linkedin.requests[1:])]
check("the empty-page retry is paced >= 5.0 s too", len(p.linkedin.requests) == 2 and min(gaps) >= 5.0, str(gaps))

code, out, p, forb = dry(["--dry-run", "--no-linkedin"])
check("--no-linkedin: not one LinkedIn request", code == 0 and p.linkedin.requests == [])
code, out, p, forb = dry(["--dry-run", "--no-linkedin", "--no-ats", "--no-jobright"])
check("all sources off: exit 0, nothing requested",
      code == 0 and not p.linkedin.requests and not p.jobright.fetches and not p.calls("ats_pass."))

prefiltered_page = lambda term, start: [li_job(1, "Senior Network Engineer"), li_job(2, "Help Desk Technician"),  # noqa: E731
                                        li_job(3, "Associate Sales Engineer")]
code, out, p, forb = dry(["--dry-run", "--linkedin-terms", "1", "--sample", "5"], pages=prefiltered_page)
check("dropped samples are printed per rule",
      "dropped sample (linkedin, seniority/leadership title, 1 of 1):" in out
      and "Company 1 | Senior Network Engineer" in out, out[-600:])

def argparse_exit(argv):
    """main.run(argv) under a fake pipeline, with argparse's usage text swallowed. Returns the exit code."""
    p = Pipeline(real_db)
    with p.install(main), captured_stdout(), contextlib.redirect_stderr(sys.stdout):
        try:
            return main.run(argv), p
        except SystemExit as exc:
            return exc.code, p


for flags in (["--no-ats"], ["--linkedin-pages", "2"], ["--sample", "3"], ["--no-jobright", "--no-linkedin"]):
    code, p = argparse_exit(flags)
    check(f"{' '.join(flags)} without --dry-run -> argparse error, exit 2, nothing called", code == 2 and p.log == [])
for flags in (["--dry-run", "--linkedin-pages", "11"], ["--dry-run", "--linkedin-pages", "0"],
              ["--dry-run", "--linkedin-terms", str(len(main.config.SEARCH_TERMS) + 1)]):
    code, p = argparse_exit(flags)
    check(f"{' '.join(flags)} -> exit 2", code == 2 and p.log == [])


# ─────────────────────────────────────────────────────────────────────────────
section("(b) preflight: a missing setting stops the run before any collaborator is called")


def preflight_case(**overrides):
    p = Pipeline(real_db)
    with configured(**overrides), p.install(main):
        code = main.run([])
    return code, p


for name in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "ANTHROPIC_API_KEY", "NTFY_TOPIC"):
    code, p = preflight_case(**{name: ""})
    check(f"{name} blank -> exit 1, nothing called but the owner alert",
          code == 1 and names(p.log) == ["notifier.push_owner_alert"] and name in p.notifier.alerts[0][0])
code, p = preflight_case(CANDIDATE_PROFILE_PATH=TMP / "missing.md")
check("rubric missing -> exit 1, nothing called but the owner alert",
      code == 1 and names(p.log) == ["notifier.push_owner_alert"] and "CANDIDATE_PROFILE_PATH" in p.notifier.alerts[0][0])
empty = TMP / "empty.md"
empty.write_text("", encoding="utf-8")
code, p = preflight_case(CANDIDATE_PROFILE_PATH=empty)
check("rubric empty -> exit 1", code == 1 and names(p.log) == ["notifier.push_owner_alert"])
code, p = preflight_case(NTFY_TOPIC="", OWNER_NTFY_TOPIC="")
check("no owner topic -> exit 1 and no alert at all (never Brice's topic)", code == 1 and p.log == [])
with captured_logs() as logs:
    preflight_case(SUPABASE_SERVICE_KEY="", ANTHROPIC_API_KEY="")
check("the preflight log names the missing settings, never a value",
      "SUPABASE_SERVICE_KEY" in logs.text() and "anthropic-key-under-test" not in logs.text()
      and "owner-topic-under-test" not in logs.text())


# ─────────────────────────────────────────────────────────────────────────────
section("(c) a normal run, stubbed")


def normal_run(setup=None, **config_overrides):
    p = Pipeline(real_db)
    # ATS: a Workday row without a description, a Greenhouse row with one, and a Workday row already stored
    p.ats_pass.candidates = [
        ats_job(1, "Associate Network Engineer", "Acme"),
        ats_job(2, "Associate Sales Engineer", "Beta", family="SALES_SOLUTIONS", workday=False,
                description="Greenhouse description. " * 20),
        ats_job(3, "Network Engineer I", "Gamma"),
    ]
    p.db.rows[p.ats_pass.candidates[2]["id"]] = {"id": p.ats_pass.candidates[2]["id"], "tier": "APPLY", "status": "new"}

    def pages(term, start):
        if term == "term one":
            return [li_job(1, "Associate Network Engineer", "Acme"),      # the ATS row's twin
                    li_job(2, "Senior Network Engineer"),                  # pre-filtered
                    li_job(3, "Junior Network Engineer"),
                    li_job(4, "Help Desk Technician")]                     # pre-filtered
        return [li_job(5, "Solutions Engineer")]
    p.linkedin.pages = pages
    p.jobright.lists = {
        "ListA": {"url": LIST_A[1], "status": 200, "etag": "A1", "rows": 2,
                  "jobs": [jr_job(1, "Associate Sales Engineer", "Beta", "ListA"),     # the ATS row's twin
                           jr_job(2, "Associate Solutions Engineer", "Delta", "ListA")]},
        "ListB": {"url": LIST_B[1], "status": 304, "etag": "B0"},
    }
    p.classifier.verdict = lambda job: ({"tier": "APPLY_CAVEAT", "reason": "asks 2+ years"}
                                        if job["title"] == "Solutions Engineer" else {"tier": "APPLY", "reason": "fit"})
    if setup:
        setup(p)
    with configured(**config_overrides), p.install(main):
        try:
            code = main.run([])
        except Exception as exc:  # noqa: BLE001 -- a crash case inspects it
            code = exc
    return code, p


code, p = normal_run()
log = p.log
check("exit 0", code == 0, str(code))
check("the run-lock is taken first", names(log)[0] == "db.start_run")
check("collection order: ATS, then LinkedIn, then jobright",
      first(log, "ats_pass.collect_ats_candidates") < first(log, "linkedin.fetch_listings")
      < first(log, "jobright.fetch_readme"))
check("nothing is classified until every source is collected",
      first(log, "classifier.classify") > max(i for i, (n, _d) in enumerate(log) if n == "jobright.fetch_readme"))
classified = [d for n, d in log if n == "classifier.classify"]
ats1, ats2, ats3 = (j["id"] for j in p.ats_pass.candidates)
check("processing order: ATS, then LinkedIn, then jobright (inside a source: entry-marked, primary family first)",
      classified == [ats2, ats1, li_job(3)["id"], li_job(5)["id"], jr_job(2, "", "", "")["id"]], str(classified))
QUEUE = [{"title": "(New Grad) Sales Development Representative", "family": "TECH_SALES", "_order": 1},
         {"title": "Associate Account Executive", "family": None, "_order": 2},          # a LinkedIn row: no family
         {"title": "Network Engineer", "family": "NETWORK_INFRA", "_order": 3},
         {"title": "Associate Sales Engineer", "family": "SALES_SOLUTIONS", "_order": 4},
         {"title": "SOC Analyst", "family": None, "_order": 5},
         {"title": "Security Analyst, Sales Compliance", "family": "SECURITY", "_order": 6}]   # a family: not selling
check("queue order: selling titles after every other title, even entry-marked, even untagged LinkedIn rows",
      [j["_order"] for j in sorted(QUEUE, key=main._queue_key)] == [4, 3, 6, 5, 1, 2],
      str([j["title"] for j in sorted(QUEUE, key=main._queue_key)]))
check("the stored ATS row is skipped: guard only, no fetch, no classify",
      ("db.get_job_row", ats3) in log and ats3 not in classified
      and not any(d == p.ats_pass.candidates[2]["url"] for n, d in log if n == "ats_pass.fetch_workday_description"))
check("the Workday description is fetched once, for the row without one, after its guard",
      p.ats_pass.workday_fetches == [p.ats_pass.candidates[0]["url"]]
      and log.index(("db.get_job_row", ats1)) < log.index(("ats_pass.fetch_workday_description",
                                                            p.ats_pass.candidates[0]["url"])))
seen = {j["id"]: j for j in p.classifier.seen}
check("...the classifier then sees it", seen[ats1]["description"].startswith("Workday description"))
check("the Greenhouse row keeps its own description and triggers no fetch",
      seen[ats2]["description"].startswith("Greenhouse description"))
check("jobright rows trigger no fetch of any kind",
      p.linkedin.described == [li_job(3)["id"], li_job(5)["id"]] and len(p.ats_pass.workday_fetches) == 1
      and p.classifier.seen[4]["description"] is None)
prefiltered = [j for j in p.db.inserted if (j.get("reason") or "").startswith("Pre-filtered:")]
check("gate drops are stored INELIGIBLE with 'Pre-filtered: <rule>'",
      sorted((j["title"], j["tier"], j["reason"]) for j in prefiltered) == [
          ("Help Desk Technician", "INELIGIBLE", "Pre-filtered: below the engineer floor (help desk/service desk/desktop support)"),
          ("Senior Network Engineer", "INELIGIBLE", "Pre-filtered: seniority/leadership title")])
check("...without a Claude call or a guard lookup",
      not any(d in (li_job(2)["id"], li_job(4)["id"]) for n, d in log if n in ("classifier.classify", "db.get_job_row")))
check("cross-source dedup keeps the ATS copy (the LinkedIn and jobright twins never reach Claude)",
      li_job(1)["id"] not in classified and jr_job(1, "", "", "")["id"] not in classified)
pinged = [j["id"] for j in p.notifier.jobs]
check("the first run pings every APPLY / APPLY_CAVEAT it stored (no silent seed)",
      pinged == classified, str(pinged))
check("...with the tier the classifier gave", [j["tier"] for j in p.notifier.jobs].count("APPLY_CAVEAT") == 1)
check("the 200 list's ETag is saved (no leftovers); the 304 list's is left alone",
      p.db.state.get("jobright_etag:ListA") == "A1" and "jobright_etag:ListB" not in p.db.state
      and ("db.clear_state", "jobright_etag:ListB") not in log)
check("the stored ETag is sent on the next conditional GET (read from bot_state)",
      ("db.get_state", "jobright_etag:ListA") in log)
run_id, stats = p.db.finished[0] if p.db.finished else (None, {})
check("finish_run is called once, with this run's id", len(p.db.finished) == 1 and run_id == 7)
check("...with exactly the scrape_runs stat keys", tuple(stats) == main.FINISH_RUN_KEYS, str(tuple(stats)))
check("...and the right numbers", stats == {"total_raw": 5, "new_jobs": 4 + 3 + 1, "notified": 5, "rate_limited": 0,
                                            "ats_candidates": 3, "jobright_candidates": 1, "classified": 5,
                                            "failed": 0, "leftover": 0}, str(stats))
check("FINISH_RUN_KEYS are the schema's stat columns",
      main.FINISH_RUN_KEYS == ("total_raw", "new_jobs", "notified", "rate_limited", "ats_candidates",
                               "jobright_candidates", "classified", "failed", "leftover"))
check("the run's pings all went to push_job; no owner alert on a healthy run", p.notifier.alerts == [])
check("PENDING retries run after every source", names(log).index("db.fetch_pending_jobs") > max(
    i for i, (n, _d) in enumerate(log) if n == "classifier.classify"))


section("(c) per-source caps leave leftovers unstored")


def many(p):
    p.ats_pass.candidates = [ats_job(10, "Network Engineer I", "Stored Co")] + [
        ats_job(10 + i, "Associate Network Engineer", f"Ats {i}") for i in range(1, 5)]
    p.db.rows = {p.ats_pass.candidates[0]["id"]: {"id": p.ats_pass.candidates[0]["id"], "tier": "APPLY", "status": "new"}}
    p.linkedin.pages = lambda term, start: ([li_job(20 + i, "Junior Network Engineer") for i in range(3)]
                                            + [li_job(30, "Senior Network Engineer"), li_job(31, "IT Technician")]
                                            if term == "term one" else [])
    p.jobright.lists = {"ListA": {"url": LIST_A[1], "status": 200, "etag": "A2", "rows": 3,
                                  "jobs": [jr_job(40 + i, "Associate Solutions Engineer", f"Jr {i}", "ListA")
                                           for i in range(3)]},
                        "ListB": {"url": LIST_B[1], "status": 200, "etag": "B2", "rows": 1,
                                  "jobs": [jr_job(50, "Technical Support Engineer", "Jr B", "ListB", "SUPPORT_ENG")]}}


with patched(main.config, MAX_CLASSIFY_PER_RUN={"ats": 2, "linkedin": 2, "jobright": 2}):
    code, p = normal_run(setup=many)
used = {s: sum(1 for d in (d for n, d in p.log if n == "classifier.classify")
               if (s == "ats" and d.startswith("ats:")) or (s == "jobright" and d.startswith("jr:"))
               or (s == "linkedin" and d[0].isdigit())) for s in ("ats", "linkedin", "jobright")}
check("each source classifies exactly its cap (2)", used == {"ats": 2, "linkedin": 2, "jobright": 2}, str(used))
check("a row skipped by the guard does not count against the cap",
      ("db.get_job_row", p.ats_pass.candidates[0]["id"]) in p.log)
inserted = {j["id"] for j in p.db.inserted}
check("leftovers are NOT stored (the next run finds them again)",
      len([i for i in inserted if i.startswith("ats:")]) == 2 and len([i for i in inserted if i.startswith("jr:")]) == 2)
check("pre-filtered LinkedIn rows are all stored and do not count against the cap",
      {li_job(30)["id"], li_job(31)["id"]} <= inserted)
check("the leftover stat counts them: ats 2 + linkedin 1 + jobright 2",
      p.db.finished[0][1]["leftover"] == 5, str(p.db.finished[0][1]))
check("a list that left rows behind has its ETag cleared, not saved",
      "jobright_etag:ListA" not in p.db.state and ("db.clear_state", "jobright_etag:ListA") in p.log)
check("...and ListB's leftover (its one row lost the priority order) clears its ETag too",
      ("db.clear_state", "jobright_etag:ListB") in p.log and "jobright_etag:ListB" not in p.db.state)


section("(c) the time budget stops starting new work")


def slow(p):
    many(p)
    p.classifier.cost_s = 1000.0         # 3 calls pass 48 minutes
    p.db.pending = [{"id": "p1", "title": "T", "company": "C", "location": "L", "description": None,
                     "salary": None, "status": "new"}]


code, p = normal_run(setup=slow)
classified = [d for n, d in p.log if n == "classifier.classify"]
check("no job starts after 48 minutes: three calls, then leftovers", len(classified) == 3, str(classified))
check("...the PENDING drain does not start either", "p1" not in classified)
check("...and everything left is counted: ats 1 + linkedin 3 + jobright 4",
      p.db.finished[0][1]["leftover"] == 8, str(p.db.finished[0][1]))
check("exit 0: running out of time is normal", code == 0)


section("(c) LinkedIn pagination")


def scan(pages_fn, *, stored=(), terms=("t",), cost=0.0, dry_run=False, pre=0.0, deep=False):
    """scan_linkedin alone. `pre` = seconds the run spent before it (the ATS sweep); `deep` = a fresh leftover marker."""
    p = Pipeline(real_db)
    p.linkedin.pages = pages_fn
    p.linkedin.cost_s = cost
    p.db.stored_ids = set(stored)
    with configured(SEARCH_TERMS=list(terms)), p.install(main):
        state = main.RunState(dry_run=dry_run)
        state.li_deep = deep
        p.clock.advance(pre)
        jobs = main.scan_linkedin(state)
    return jobs, state, p


def page(start, n=10, title="Associate Network Engineer"):
    return [li_job(1000 + start + i, title) for i in range(n)]


all_ids = [j["id"] for s in range(0, 100, 10) for j in page(s)]
jobs, st, p = scan(lambda t, s: page(s), stored=all_ids)
check("two consecutive all-duplicate pages stop the search (page 2 never requested)",
      [s for *_x, s in p.linkedin.requests] == [0, 10] and jobs == [])
dup_pages = {0, 20, 30}
jobs, st, p = scan(lambda t, s: page(s), stored=[j["id"] for s in dup_pages for j in page(s)])
check("a page with a new job resets the count (dup, new, dup, dup -> stop after 4 pages)",
      [s for *_x, s in p.linkedin.requests] == [0, 10, 20, 30] and len(jobs) == 10)
jobs, st, p = scan(lambda t, s: page(s, 10 if s == 0 else 5))
check("a partial page is the last page", [s for *_x, s in p.linkedin.requests] == [0, 10] and len(jobs) == 15)
jobs, st, p = scan(lambda t, s: page(s))
check("ten full pages is the limit", [s for *_x, s in p.linkedin.requests] == list(range(0, 100, 10)) and len(jobs) == 100)
jobs, st, p = scan(lambda t, s: "rate_limited")
check("a rate-limited search stops and is counted", st.li["rate_limited"] == 1 and len(p.linkedin.requests) == 1)
calls = []
jobs, st, p = scan(lambda t, s: (calls.append(s), [] if len(calls) == 1 else page(s, 3))[1])
check("an empty page is retried once, then continues", [s for *_x, s in p.linkedin.requests] == [0, 0] and len(jobs) == 3)
jobs, st, p = scan(lambda t, s: [])
check("empty twice -> done, nothing new", len(p.linkedin.requests) == 2 and jobs == [])
jobs, st, p = scan(lambda t, s: page(s, 3), terms=("t1", "t2", "t3", "t4"), cost=600.0)
check("the 25-minute search budget stops starting new terms",
      [t for _c, t, _l, _s in p.linkedin.requests] == ["t1", "t2", "t3"] and st.li["skipped_terms"] == ["t4"],
      str([t for _c, t, _l, _s in p.linkedin.requests]))
jobs, st, p = scan(lambda t, s: page(s, 3), terms=("t1", "t2", "t3"), pre=24 * 60)
check("...counted from the LinkedIn pass's own start: after a 24-minute ATS sweep every term still runs",
      [t for _c, t, _l, _s in p.linkedin.requests] == ["t1", "t2", "t3"] and st.li["skipped_terms"] == [],
      str([t for _c, t, _l, _s in p.linkedin.requests]))
jobs, st, p = scan(lambda t, s: page(s, 3), terms=("t1", "t2"), pre=main.config.RUN_TIME_BUDGET_S)
check("...while the run's 48-minute budget still stops new searches",
      p.linkedin.requests == [] and st.li["skipped_terms"] == ["t1", "t2"] and st.li["terms"] == 2)
jobs, st, p = scan(lambda t, s: page(s), stored=all_ids, deep=True)
check("with a fresh leftover marker, all-stored pages do not stop a search: all ten pages are read",
      [s for *_x, s in p.linkedin.requests] == list(range(0, 100, 10)) and jobs == [])
jobs, st, p = scan(lambda t, s: "network down")
check("a search error is counted (a scan with errors is not a complete one)", st.li["errors"] == 1)
jobs, st, p = scan(lambda t, s: page(0, 3) if s == 0 else [], terms=("t1", "t2"))
check("the same posting from two terms is new once", len(jobs) == 3 and st.li["total_raw"] == 6)
check("each new job carries its term, source and gate verdict",
      jobs[0]["search_term"] == "t1" and jobs[0]["source"] == "linkedin" and jobs[0]["gate"] is None
      and jobs[0]["norm_key"].endswith("|ft"))
jobs, st, p = scan(lambda t, s: page(s, 10 if s < 20 else 2))
gaps = [b[0] - a[0] for a, b in zip(p.linkedin.requests, p.linkedin.requests[1:])]
check("normal pacing between pages is 2.0-3.5 s (lower bound here)", gaps == [2.0, 2.0], str(gaps))


section("(c) LinkedIn leftovers are found again by the next runs")
FOUR_PAGES = lambda term, start: page(start) if start < 40 else []   # noqa: E731  -- 40 new jobs, one term


def li_run(prev=None, *, pages=FOUR_PAGES, cap=20, marker=None, setup=None):
    """A full main.run([]) with one LinkedIn term and nothing else, the database carried over from `prev`."""
    p = Pipeline(real_db)
    p.linkedin.pages = pages
    if prev is not None:
        p.db.stored_ids = set(prev.db.stored_ids) | {j["id"] for j in prev.db.inserted}
        p.db.stored_nks = set(prev.db.stored_nks) | {main.make_norm_key(j["company"], j["title"])
                                                     for j in prev.db.inserted}
        p.db.rows = dict(prev.db.rows, **{j["id"]: {"id": j["id"], "tier": j["tier"], "status": "new"}
                                          for j in prev.db.inserted})
        p.db.state = dict(prev.db.state)
    if marker is not None:
        p.db.state[main.LI_LEFTOVER_KEY] = marker
    if setup:
        setup(p)
    with configured(SEARCH_TERMS=["t"], JOBRIGHT_LISTS=[],
                    MAX_CLASSIFY_PER_RUN={"ats": 100, "linkedin": cap, "jobright": 100}), p.install(main):
        code = main.run([])
    return code, p


def classified_ids(p):
    return [d for n, d in p.log if n == "classifier.classify"]


def starts(p):
    return [s for *_x, s in p.linkedin.requests]


page_ids = {s: [j["id"] for j in page(s)] for s in range(0, 40, 10)}
code, run1 = li_run()
check("run 1: 40 new jobs on four pages, the cap classifies 20 and leaves 20 unstored",
      code == 0 and classified_ids(run1) == page_ids[0] + page_ids[10]
      and run1.db.finished[0][1]["leftover"] == 20, str(classified_ids(run1)[:3]))
check("...and stamps linkedin_leftover_at in bot_state", main.LI_LEFTOVER_KEY in run1.db.state)
code, run2 = li_run(run1)
check("run 2: the fresh marker pages past the two all-stored pages to the leftovers",
      starts(run2)[:4] == [0, 10, 20, 30], str(starts(run2)))
check("...and classifies exactly the 20 jobs run 1 left behind",
      classified_ids(run2) == page_ids[20] + page_ids[30], str(classified_ids(run2)[:3]))
check("...then clears the marker: a complete scan left nothing behind", main.LI_LEFTOVER_KEY not in run2.db.state)
code, run3 = li_run(run2)
check("run 3: no marker, so a search stops after two all-stored pages again",
      starts(run3) == [0, 10] and classified_ids(run3) == [], str(starts(run3)))
no_marker = Pipeline(real_db)
no_marker.db.inserted = list(run1.db.inserted)
code, run2_old = li_run(no_marker)
check("without the marker (the old behaviour) the leftovers were never reached",
      starts(run2_old) == [0, 10] and classified_ids(run2_old) == [], str(starts(run2_old)))

stale = (main.datetime.now(main.timezone.utc) - main.timedelta(hours=25)).isoformat()
code, run_old = li_run(run2, marker=stale)
check("a marker older than the 24 h window neither deepens the scan nor survives the run",
      starts(run_old) == [0, 10] and main.LI_LEFTOVER_KEY not in run_old.db.state, str(starts(run_old)))
fresh = run1.db.state[main.LI_LEFTOVER_KEY]
code, run_rl = li_run(run1, pages=lambda term, start: page(start) if start < 20 else "rate_limited")
check("a deep scan cut by a rate limit keeps the marker for the next run",
      run_rl.db.state.get(main.LI_LEFTOVER_KEY) == fresh and classified_ids(run_rl) == [])
code, run_fail = li_run(cap=100, setup=lambda p: setattr(p.db, "insert_ok", False))
check("a LinkedIn job whose write failed stamps the marker too (nothing stored, all 40 unstored)",
      main.LI_LEFTOVER_KEY in run_fail.db.state and run_fail.db.inserted == [])


section("(c) owner alerts: never Brice's topic, never push_job")


def no_linkedin(p):
    p.linkedin.pages = lambda term, start: []


code, p = normal_run(setup=no_linkedin)
titles = [t for _m, t, _p in p.notifier.alerts]
check("LinkedIn returning nothing -> an owner alert", "Brice: LinkedIn returned nothing" in titles, str(titles))
check("...while ATS and jobright are still processed",
      any(d.startswith("ats:") for n, d in p.log if n == "classifier.classify")
      and any(d.startswith("jr:") for n, d in p.log if n == "classifier.classify"))
check("...and no job ping carries alert text", all(j["id"][:3] in ("ats", "jr:") or j["id"][0].isdigit()
                                                     for j in p.notifier.jobs))

code, p = normal_run(SEARCH_TIME_BUDGET_S=0)
titles = [t for _m, t, _p in p.notifier.alerts]
check("a search budget that skips terms -> 'LinkedIn search incomplete' to the owner, not 'returned nothing'",
      "Brice: LinkedIn search incomplete" in titles and "Brice: LinkedIn returned nothing" not in titles
      and "alert_at:linkedin_incomplete" in p.db.state, str(titles))
msg = next((m for m, t, _p in p.notifier.alerts if t == "Brice: LinkedIn search incomplete"), "")
check("...naming the skipped terms and the ATS sweep's minutes",
      "2 of 2 terms skipped" in msg and "The ATS sweep took" in msg, msg)


def mostly_rate_limited(p):
    p.linkedin.pages = lambda term, start: ("rate_limited" if term != "term three"
                                            else [li_job(700 + start, "Junior Network Engineer")])


code, p = normal_run(setup=mostly_rate_limited, SEARCH_TERMS=["term one", "term two", "term three"])
check("more than half the searches rate limited -> the same alert",
      "Brice: LinkedIn search incomplete" in [t for _m, t, _p in p.notifier.alerts])


def ats_slow(p):
    p.ats_pass.stats = dict(p.ats_pass.stats, cut_short=True)


code, p = normal_run(setup=ats_slow)
check("an ATS sweep cut short by its budget -> 'ATS sweep cut short' to the owner, throttled 24 h",
      [t for _m, t, _p in p.notifier.alerts] == ["Brice: ATS sweep cut short"] and "alert_at:ats_slow" in p.db.state)


def pings_rejected(p):
    p.notifier.push_ok = False


code, p = normal_run(setup=pings_rejected)
titles = [t for _m, t, _p in p.notifier.alerts]
check("every ping of a run rejected (5 tried) -> 'pings failing' to the owner, throttled 6 h",
      titles == ["Brice: pings failing"] and "5 attempted" in p.notifier.alerts[0][0]
      and "alert_at:pings_failed" in p.db.state and p.db.finished[0][1]["notified"] == 0, str(titles))


def all_fail(p):
    p.classifier.verdict = lambda job: {"failed": True, "failed_kind": "transient", "tier": "APPLY_CAVEAT",
                                        "reason": "x"}


code, p = normal_run(setup=all_fail)
titles = [t for _m, t, _p in p.notifier.alerts]
check("every classification failing (>= 3 attempted) -> one owner alert",
      titles.count("Brice: all classifications failed") == 1 and "alert_at:all_failed" in p.db.state, str(titles))
check("...naming the counts per kind", "transient" in p.notifier.alerts[0][0])
check("...with every failed job parked, none pinged",
      p.notifier.jobs == [] and sum(1 for j in p.db.inserted if j["tier"] == "PENDING") == 5)
check("...counted in scrape_runs.failed", p.db.finished[0][1]["failed"] == 5)
marker = dict(p.db.state)


def all_fail_again(p):
    all_fail(p)
    p.db.state.update(marker)


code, p = normal_run(setup=all_fail_again)
check("a second failing run inside 6 h does not alert again",
      "Brice: all classifications failed" not in [t for _m, t, _p in p.notifier.alerts])


def billing(p):
    p.classifier.verdict = lambda job: {"failed": True, "failed_kind": "billing", "tier": "APPLY_CAVEAT", "reason": "x"}


code, p = normal_run(setup=billing)
titles = [t for _m, t, _p in p.notifier.alerts]
check("a billing outage -> the classifier-down alert, not the all-failed one",
      "Brice: classifier down" in titles and "Brice: all classifications failed" not in titles, str(titles))


def broken_sources(p):
    p.ats_pass.stats = {"boards": 26, "boards_with_listings": 0, "listings": 0, "dropped_by": {}}
    p.ats_pass.candidates = []
    p.jobright.lists["ListA"].update(rows=0, jobs=[], problems=["HTTP 200 with 0 parsed rows"])


code, p = normal_run(setup=broken_sources)
titles = [t for _m, t, _p in p.notifier.alerts]
check("every ATS board empty -> an owner alert", "Brice: ATS boards returned nothing" in titles, str(titles))
check("a jobright canary -> an owner alert for that list",
      "Brice: jobright ListA list" in titles and "Brice: jobright ListB list" not in titles)
check("...throttled for 24 h via bot_state", "alert_at:jobright:ListA" in p.db.state and "alert_at:ats_empty" in p.db.state)


section("(c) the run-lock, a Supabase outage, a spent quota, a crash")


def locked(p):
    p.db.start_run_result = None


code, p = normal_run(setup=locked)
check("another run in progress -> exit 0 with no work at all", code == 0 and names(p.log) == ["db.start_run"])


def db_down(p):
    p.db.start_run_result = real_db.DbUnavailable("connection refused")


code, p = normal_run(setup=db_down)
check("Supabase unavailable at start -> exit 1, nothing fetched",
      code == 1 and names(p.log) == ["db.start_run", "notifier.push_owner_alert"])
check("...and the owner is told, by exception type only",
      "Supabase unavailable at run start (DbUnavailable)" in p.notifier.alerts[0][0]
      and "connection refused" not in p.notifier.alerts[0][0])


def quota_at_start(p):
    p.db.start_run_result = real_db.QuotaExceeded("restricted")


code, p = normal_run(setup=quota_at_start)
check("quota spent at start -> exit 1, owner alert, nothing fetched",
      code == 1 and names(p.log) == ["db.start_run", "notifier.push_owner_alert"]
      and "quota" in p.notifier.alerts[0][0].lower())


def quota_mid_run(p):
    p.db.quota_on_insert = 2


code, p = normal_run(setup=quota_mid_run)
classified = [d for n, d in p.log if n == "classifier.classify"]
check("quota spent mid-run -> exit 1", code == 1, str(code))
check("...no Claude call after the refused write", len(classified) == 1 and names(p.log)[-3:] == [
    "db.insert_job", "db.finish_run", "notifier.push_owner_alert"], str(names(p.log)[-4:]))
check("...finish_run still called, and one quota alert to the owner",
      len(p.db.finished) == 1 and [t for _m, t, _p in p.notifier.alerts] == ["Brice: Supabase quota spent"])


def crash(p):
    def boom(job):
        raise RuntimeError("boom")
    p.classifier.verdict = boom


code, p = normal_run(setup=crash)
check("an unexpected exception propagates (the workflow goes red)", isinstance(code, RuntimeError))
check("...after finish_run released the lock and the owner heard 'run crashed: RuntimeError'",
      len(p.db.finished) == 1 and ("run crashed: RuntimeError", "Brice: run crashed", "urgent") in p.notifier.alerts)

section("nothing private in the run log")
with captured_logs() as logs:
    code, p = normal_run()
text = logs.text()
check("no secret, topic or classifier reason is logged",
      not any(s in text for s in ("service-key-under-test", "anthropic-key-under-test", "brice-topic-under-test",
                                  "owner-topic-under-test", "project.invalid", "asks 2+ years")), text[:200])
check("the run summary line is logged",
      "Run summary: ats cand 3 | linkedin new 4 (claude 2, pre-filtered 2) | jobright cand 1 | classified 5 | "
      "parked 0 | pushed 5 | leftover 0" in text)
check("the interface log formats are present",
      "Searching: 'term one' in United States" in text and "  p0 (start=0): 4 listings, 3 new" in text
      and "Total raw: 5 | New: 4 | Rate limited: 0/2 searches" in text
      and "  Pre-filter SKIP (seniority/leadership title)" in text and "  -> classified | id=" in text
      and "  Description: " in text)
check("no line names a job's tier: beside the title it would publish the rubric's private rules",
      not any(word in text for word in ("APPLY", "INELIGIBLE", "tier=")),
      [line for line in logs.messages() if "APPLY" in line or "INELIGIBLE" in line or "tier=" in line][:3])


def stored_twice(p):
    p.db.rows[p.ats_pass.candidates[0]["id"]] = {"id": p.ats_pass.candidates[0]["id"], "tier": "INELIGIBLE",
                                                 "status": "applied"}


with captured_logs() as logs:
    normal_run(setup=stored_twice)
check("...nor the stored row's tier or status when the already-stored guard skips a job",
      "  Already stored — no re-fetch, no re-classify, no push" in logs.text()
      and "INELIGIBLE" not in logs.text() and "applied" not in logs.text())

shutil.rmtree(TMP, ignore_errors=True)
sys.exit(testkit.finish())
