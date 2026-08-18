"""Guards the ping-time changes — parallel ATS sweep, gh-first, freshest-first.

Offline: no network, no keys. Stubs by module-attribute assignment, same as
test_pending_queue.py.

What is being protected:

  1. The ATS sweep runs the ~100 boards CONCURRENTLY (sequential took minutes
     and held the run-lock long enough to skip github_watch passes), while one
     broken board still never blocks the others.
  2. main.run() processes the GitHub-tracker sources BEFORE the LinkedIn
     search loop, so a gh: push never waits behind pagination sleeps — and
     still goes out even when LinkedIn is blocked and the canary fires.
  3. Batches are processed newest-posting-first.

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


# ── 3. Source order in run() ─────────────────────────────────────────────
print("\n-- run(): gh-tracker block sits before the LinkedIn loop --")

import inspect

_run = inspect.getsource(main.run)
check("retry_pending still runs first",
      _run.index("retry_pending()") < _run.index("fetch_github_listings"))
check("gh-tracker fetch runs before the LinkedIn search loop",
      _run.index("fetch_github_listings") < _run.index("for term in SEARCH_TERMS"),
      "gh pushes must not wait behind 1-2 min of pagination sleeps")
check("gh batch is processed freshest-first", "_freshest_first(gh_batch)" in _run)
check("LinkedIn batch is processed freshest-first", "_freshest_first(new_jobs)" in _run)


# ── 4. gh jobs survive a LinkedIn-blocked run ────────────────────────────
print("\n-- run() end-to-end with stubs: gh job pushes even when LinkedIn is blocked --")

_orig = {k: getattr(main, k) for k in (
    "start_run", "finish_run", "load_dedup_index", "fetch_pending_jobs",
    "fetch_github_listings", "fetch_listings", "fetch_external_description",
    "classify", "insert_job", "push_job", "push_canary", "time",
)}

_state = {"pushed": [], "canaries": [], "finish": None}

GH_JOB = {"id": "gh:abc123", "title": "SWE Intern", "company": "Acme",
          "location": "Remote, US", "url": "https://example.com/j",
          "apply_url": "https://example.com/apply", "description": None,
          "is_easy_apply": False, "posted_at": "2026-08-17T00:00:00+00:00",
          "search_term": "github:test"}

main.start_run = lambda: 1
main.finish_run = lambda run_id, **kw: _state.__setitem__("finish", kw)
main.load_dedup_index = lambda: (set(), set())
main.fetch_pending_jobs = lambda limit: []
main.fetch_github_listings = lambda: [dict(GH_JOB)]
main.fetch_listings = lambda *a, **kw: ([], None)          # LinkedIn: nothing, every page
main.fetch_external_description = lambda url: "a description"
main.classify = lambda job: {"tier": "APPLY", "reason": "fit", "suggested_resume": "General"}
main.insert_job = lambda job: True
main.push_job = lambda job: _state["pushed"].append(dict(job))
main.push_canary = lambda msg: _state["canaries"].append(msg)
main.time = types.SimpleNamespace(sleep=lambda s: None)     # skip the pacing sleeps
main._PARKED_THIS_RUN.clear()

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
