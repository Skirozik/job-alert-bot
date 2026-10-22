"""The hourly deep sweep: a full run that reads past a page of stored jobs.

WHY THIS EXISTS: the full run stopped paginating at the first page whose jobs
were all stored. The fast watcher (linkedin_watch.py, every 5 min) stores page 0
of every search, so from 2026-08-22 a full run always stopped at page 0 -- 131 of
140 searches on 2026-10-22 never read page 1. LinkedIn's guest search ranks by
relevance, not date, and Twitch's "Undergrad Software Engineering Intern (Grad
Year: Winter 2027)" sat on page 1 of "software engineer intern" for hours,
unseen, while its Spring 2028 twin on page 0 was stored.

Runs offline -- no network, no API key, no DB.
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import main

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def listing(n, title="Software Engineer Intern", company="Acme"):
    return {"id": str(4480000000 + n), "title": title, "company": company, "location": "San Francisco, CA",
            "url": f"https://www.linkedin.com/jobs/view/{4480000000 + n}", "is_easy_apply": False,
            "posted_at": "2026-10-22T00:00:00+00:00"}


# Page 0: ten stored jobs (the fast watcher saw them). Page 1: nine stored and the
# Twitch posting. Page 2: a short page, the natural end of the results.
PAGE0 = [listing(i) for i in range(10)]
TWITCH = listing(99, "Undergrad Software Engineering Intern (Grad Year: Winter 2027)", "Twitch")
PAGE1 = [listing(i) for i in range(10, 19)] + [TWITCH]
PAGE2 = [listing(i) for i in range(20, 24)]
STORED = {j["id"] for j in PAGE0 + PAGE1 + PAGE2} - {TWITCH["id"]}

saved = {k: getattr(main, k) for k in (
    "fetch_listings", "find_known_candidates", "_process_linkedin_candidate", "SEARCH_TERMS", "LOCATIONS")}
saved_sleep = main.time.sleep


def scan(stop_on_all_duplicates):
    fetched, processed = [], []

    def fake_fetch(term, location, lookback, start=0):
        fetched.append(start)
        return {0: PAGE0, 10: PAGE1, 20: PAGE2}.get(start, []), None

    main.fetch_listings = fake_fetch
    main.find_known_candidates = lambda jobs: ({j["id"] for j in jobs if j["id"] in STORED}, set())
    main._process_linkedin_candidate = lambda job: (processed.append(job["id"]), False)[1]
    main.SEARCH_TERMS, main.LOCATIONS = ["software engineer intern"], ["United States"]
    main.time.sleep = lambda s: None
    try:
        main.scan_linkedin(stop_on_all_duplicates=stop_on_all_duplicates)
    finally:
        for k, v in saved.items():
            setattr(main, k, v)
        main.time.sleep = saved_sleep
    return fetched, processed


print("-- scan_linkedin: the stop rule --")
fetched, processed = scan(stop_on_all_duplicates=True)
check("a normal run stops after page 0 when it is all stored", fetched == [0], str(fetched))
check("...so the Twitch posting on page 1 is never processed (the bug, kept for normal runs)",
      TWITCH["id"] not in processed, str(processed))

fetched, processed = scan(stop_on_all_duplicates=False)
check("a deep sweep reads past the all-stored page 0", fetched[:3] == [0, 10, 20], str(fetched))
check("...processes the Twitch posting on page 1", processed == [TWITCH["id"]], str(processed))
check("...and still ends at the short page", fetched == [0, 10, 20], str(fetched))

print("\n-- _deep_sweep_due --")
now = datetime(2026, 10, 22, 21, 0, tzinfo=timezone.utc)
saved_get = main.get_state
try:
    for label, value, want in [
        ("never run", None, True),
        ("10 minutes ago", (now - timedelta(minutes=10)).isoformat(), False),
        ("59 minutes ago", (now - timedelta(minutes=59)).isoformat(), False),
        ("exactly the interval ago", (now - timedelta(seconds=main.DEEP_SWEEP_INTERVAL_SECONDS)).isoformat(), True),
        ("2 hours ago", (now - timedelta(hours=2)).isoformat(), True),
        ("an unreadable value", "not-a-timestamp", True),
    ]:
        main.get_state = lambda key, v=value: v
        check(f"last sweep {label} -> due={want}", main._deep_sweep_due(now) is want)
finally:
    main.get_state = saved_get
check("the interval is an hour", main.DEEP_SWEEP_INTERVAL_SECONDS == 3600)
check("...well inside the 6 h lookback, so a posting stays in range for several sweeps",
      main.LOOKBACK_SECONDS >= 4 * main.DEEP_SWEEP_INTERVAL_SECONDS)

print("\n-- run(): which runs sweep deep, and when the stamp is written --")
names = ("start_run", "finish_run", "scan_linkedin", "scan_github_fallback", "retry_pending", "push_canary",
         "get_state", "set_state", "_maybe_alert_classifier_down")
saved_run = {k: getattr(main, k) for k in names}


def run_with(last_sweep, scan_raises=False):
    calls = {"scan": [], "set": []}

    def fake_scan(max_pages_per_search=main.MAX_PAGES_PER_SEARCH, stop_on_all_duplicates=True):
        calls["scan"].append(stop_on_all_duplicates)
        if scan_raises:
            raise RuntimeError("LinkedIn fell over mid-sweep")
        return 50, 0, 0, 0

    main.start_run = lambda source="linkedin": 1
    main.finish_run = lambda run_id, **kw: None
    main.scan_linkedin = fake_scan
    main.scan_github_fallback = lambda: (0, 0)
    main.retry_pending = lambda: 0
    main.push_canary = lambda msg: None
    main.get_state = lambda key: last_sweep if key == main._DEEP_SWEEP_KEY else None
    main.set_state = lambda key, value: calls["set"].append((key, value))
    main._maybe_alert_classifier_down = lambda: None
    try:
        main.run()
    except RuntimeError:
        pass
    finally:
        for k, v in saved_run.items():
            setattr(main, k, v)
    return calls


recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
calls = run_with(recent)
check("a run 5 minutes after the last deep sweep keeps the early stop", calls["scan"] == [True], str(calls))
check("...and leaves the stamp alone", calls["set"] == [], str(calls["set"]))

calls = run_with(None)
check("a run with no deep sweep on record sweeps deep", calls["scan"] == [False], str(calls))
stamp = calls["set"][0] if calls["set"] else ("", "")
check("...and stamps it", stamp[0] == main._DEEP_SWEEP_KEY and datetime.fromisoformat(stamp[1]).tzinfo is not None,
      str(calls["set"]))

calls = run_with(None, scan_raises=True)
check("a deep sweep that crashes is not stamped, so the next run retries it", calls["set"] == [], str(calls["set"]))

print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAILED'}")
sys.exit(1 if failures else 0)
