"""Guards the ping-time changes — parallel ATS, streaming LinkedIn, fresh-first.

Offline: no network, no keys. Stubs by module-attribute assignment, same as
test_pending_queue.py.

What is being protected:

  1. The ATS sweep runs the ~100 boards CONCURRENTLY (sequential took minutes
     and held the run-lock long enough to skip github_watch passes), while one
     broken board still never blocks the others.
  2. main.run() keeps the GitHub fallback alive, but LinkedIn candidates are
     processed page-by-page instead of waiting for all searches to finish.
  3. Batches are processed newest-posting-first and the pending backlog runs
     only after fresh-source work.

Run:  cd scraper && python test_latency_changes.py
"""

import sys
import threading
import types

import ats_sources
import main

_pass = _fail = 0


def check(name, cond, detail=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{f' — {detail}' if detail else ''}")


# ── 1. Parallel sweep ────────────────────────────────────────────────────
print("\n-- fetch_all_listings: concurrent, and one bad board never blocks the rest --")

COMPANIES = {f"Co{i}": {"platform": "greenhouse", "token": f"t{i}"} for i in range(5)}

_orig_fetch = ats_sources.fetch_company_listings


def _fake_fetch(company, platform, token):
    if company == "Co2":
        raise RuntimeError("leaked past the fetcher")  # tests the sweep-level guard
    return [{"id": f"ats:{company}", "title": "SWE Intern", "company": company}]


ats_sources.fetch_company_listings = _fake_fetch
jobs = ats_sources.fetch_all_listings(COMPANIES)
got = {j["company"] for j in jobs}
check("every healthy board's jobs are present", got == {"Co0", "Co1", "Co3", "Co4"},
      f"got {sorted(got)}")
check("a leaking board is isolated, not fatal", len(jobs) == 4,
      "the module contract: one broken entry never blocks the other 49")

# Concurrency proof: three boards wait on a barrier that only clears if they
# run at the same time. Sequential execution would time the barrier out.
_barrier = threading.Barrier(3, timeout=10)
_timed_out = []


def _barrier_fetch(company, platform, token):
    try:
        _barrier.wait()
    except threading.BrokenBarrierError:
        _timed_out.append(company)
    return []


ats_sources.fetch_company_listings = _barrier_fetch
ats_sources.fetch_all_listings({f"B{i}": {"platform": "greenhouse", "token": "t"} for i in range(3)})
check("boards actually run concurrently", not _timed_out,
      "3 boards never met at the barrier — the sweep is still sequential")

ats_sources.fetch_company_listings = _orig_fetch


# ── 2. Freshest-first ────────────────────────────────────────────────────
print("\n-- _freshest_first: newest posting gets the front of the queue --")

batch = [
    {"id": "a", "posted_at": "2026-08-15T09:00:00+00:00"},
    {"id": "b", "posted_at": None},
    {"id": "c", "posted_at": "2026-08-17T21:30:00+00:00"},
    {"id": "d", "posted_at": "2026-08-16"},
    {"id": "e"},
]
ordered = [j["id"] for j in main._freshest_first(batch)]
check("newest first, oldest later", ordered[:3] == ["c", "d", "a"], f"got {ordered}")
check("missing posted_at sorts last", set(ordered[3:]) == {"b", "e"}, f"got {ordered}")
check("input list is not mutated", [j["id"] for j in batch] == ["a", "b", "c", "d", "e"])


# ── 3. Source order and LinkedIn streaming ───────────────────────────────
print("\n-- run(): fresh work precedes backlog, LinkedIn pages stream --")

import inspect

_run = inspect.getsource(main.run)
_scan = inspect.getsource(main.scan_linkedin)
check("fresh LinkedIn work runs before retry_pending",
      _run.index("scan_linkedin()") < _run.index("retry_pending()"),
      "a parked backlog must not delay a newly-posted job")
check("LinkedIn runs before the GitHub fallback",
      _run.index("scan_linkedin()") < _run.index("scan_github_fallback()"),
      "the tracker already has a two-minute watcher and must not delay LinkedIn")
check("gh batch is processed freshest-first",
      "_freshest_first(batch)" in inspect.getsource(main.scan_github_fallback))
check("LinkedIn page is processed freshest-first", "_freshest_first(page_new)" in _scan)
check("LinkedIn page is processed inside the pagination loop",
      _scan.index("_freshest_first(page_new)") < _scan.index("if all_db_duplicate"),
      "a new page-zero job must push before the next search starts")


print("\n-- scan_linkedin(): a page-zero match processes before the next query --")
_orig_stream = {k: getattr(main, k) for k in (
    "SEARCH_TERMS", "LOCATIONS", "fetch_listings", "find_known_candidates",
    "_process_linkedin_candidate", "time",
)}
_events = []
main.SEARCH_TERMS = ["first", "second"]
main.LOCATIONS = ["United States"]


def _stream_fetch(term, location, lookback, start=0):
    _events.append(f"fetch:{term}")
    if term == "first":
        return ([{
            "id": "100", "title": "Software Engineer Intern", "company": "Acme",
            "location": location, "url": "https://linkedin.test/100",
            "posted_at": "2026-08-22", "description": None, "is_easy_apply": False,
        }], None)
    return ([], "blocked-for-test")


main.fetch_listings = _stream_fetch
main.find_known_candidates = lambda jobs: (set(), set())
main._process_linkedin_candidate = lambda job: (_events.append(f"process:{job['id']}"), True)[1]
main.time = types.SimpleNamespace(sleep=lambda seconds: None)
stream_stats = main.scan_linkedin(max_pages_per_search=1)
check("first page was processed before the second search",
      _events.index("process:100") < _events.index("fetch:second"), f"events={_events}")
check("streamed job contributes to new/notified stats",
      stream_stats[1:] == (1, 0, 1), f"got {stream_stats}")

for k, v in _orig_stream.items():
    setattr(main, k, v)


# ── 4. gh jobs survive a LinkedIn-blocked run ────────────────────────────
print("\n-- run() end-to-end with stubs: gh job pushes even when LinkedIn is blocked --")

_orig = {k: getattr(main, k) for k in (
    "start_run", "finish_run", "find_known_candidates", "fetch_pending_jobs",
    "fetch_github_listings", "fetch_listings", "fetch_external_description",
    "classify", "insert_job", "push_job", "push_canary", "time",
    "get_job_row", "claim_notification",
)}

_state = {"pushed": [], "canaries": [], "finish": None}

GH_JOB = {"id": "gh:abc123", "title": "SWE Intern", "company": "Acme",
          "location": "Remote, US", "url": "https://example.com/j",
          "apply_url": "https://example.com/apply", "description": None,
          "is_easy_apply": False, "posted_at": "2026-08-17T00:00:00+00:00",
          "search_term": "github:test"}

main.start_run = lambda source="linkedin": 1
main.finish_run = lambda run_id, **kw: _state.__setitem__("finish", kw)
main.find_known_candidates = lambda jobs: (set(), set())
main.fetch_pending_jobs = lambda limit: []
main.fetch_github_listings = lambda: [dict(GH_JOB)]
main.fetch_listings = lambda *a, **kw: ([], None)          # LinkedIn: nothing, every page
main.fetch_external_description = lambda url: "a description"
main.classify = lambda job: {"tier": "APPLY", "reason": "fit", "suggested_resume": "General"}
main.insert_job = lambda job: True
# Both are new gates on the push path and both must be stubbed here, or this
# test exercises their error branches instead of the behaviour it is about:
# get_job_row would report every job as unseen (fail open, harmless), while
# claim_notification fails CLOSED by design and would silently suppress every
# push, failing the assertions below for entirely the wrong reason.
main.get_job_row = lambda _id: None
main.claim_notification = lambda _id: (True, "claimed")
main.push_job = lambda job: _state["pushed"].append(dict(job))
main.push_canary = lambda msg: _state["canaries"].append(msg)
main.time = types.SimpleNamespace(sleep=lambda s: None)     # skip the pacing sleeps
main._PARKED_THIS_RUN.clear()
main._SUPPRESSED_THIS_RUN.clear()

main.run()

check("the gh job was classified and pushed", len(_state["pushed"]) == 1
      and _state["pushed"][0]["id"] == "gh:abc123",
      "gh processing must no longer sit behind (or die with) the LinkedIn half")
check("the LinkedIn-blocked canary still fired", len(_state["canaries"]) == 1,
      "0 raw results across all searches must still alert")
check("finish_run counts the gh job in new_jobs",
      (_state["finish"] or {}).get("new_jobs") == 1, f"got {_state['finish']}")
check("finish_run counts the push in notified",
      (_state["finish"] or {}).get("notified") == 1, f"got {_state['finish']}")

for k, v in _orig.items():
    setattr(main, k, v)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
