"""The PENDING queue: parking a failed classification, the already-stored guard, and the drain.

Offline: every collaborator of main.py is a recording fake (testkit.Pipeline).
What is protected (the main pipeline's lessons, scraper/test_pending_queue.py):

  1. A job whose classification fails is PARKED as PENDING -- never dropped
     (a LinkedIn job is gone once it leaves the lookback window) and never
     stored with a made-up verdict (dedup would hide it forever).
  2. A stored row is never re-fetched, re-classified or re-pinged.
  3. The drain promotes through update_job_classification, pings only rows
     still 'new', and stops fast on a billing outage or a poison row.

Run:  cd scraper_brice && python -X utf8 test_pending_queue.py
"""

import testkit

testkit.block_network()

import sys  # noqa: E402

import classifier as real_classifier  # noqa: E402
import db as real_db  # noqa: E402
import main  # noqa: E402
from testkit import Pipeline, check, li_job, section  # noqa: E402

FAIL_BILLING = {"failed": True, "failed_kind": "billing", "tier": "APPLY_CAVEAT", "reason": "x"}
FAIL_TRANSIENT = {"failed": True, "failed_kind": "transient", "tier": "APPLY_CAVEAT", "reason": "x"}
FAIL_MALFORMED = {"failed": True, "failed_kind": "malformed", "tier": "APPLY_CAVEAT", "reason": "x"}


def linkedin_candidate(n, title="Associate Network Engineer"):
    job = li_job(n, title)
    job.update(source="linkedin", norm_key=main.make_norm_key(job["company"], job["title"]), family=None,
               gate=None, search_term="network engineer", _order=n)
    return job


def one_job(verdict, *, row=None, insert_ok=True, job=None):
    """process_job on a single LinkedIn candidate; returns (outcome, pipeline, state)."""
    p = Pipeline(real_db)
    p.classifier.verdict = verdict if callable(verdict) else (lambda _j: dict(verdict))
    p.db.insert_ok = insert_ok
    job = job or linkedin_candidate(1)
    if row is not None:
        p.db.rows[job["id"]] = row
    with p.install(main):
        state = main.RunState()
        outcome = main.process_job(job, state)
    return outcome, p, state


section("process_job parks a failed classification")
out, p, state = one_job(FAIL_BILLING)
row = p.db.inserted[0] if p.db.inserted else {}
check("a failed classification still writes a row", len(p.db.inserted) == 1)
check("...as tier PENDING, with the parked reason",
      row.get("tier") == "PENDING" and row.get("reason") == main.PARKED_REASON)
check("...keeping the description fetched before the call", (row.get("description") or "").startswith("LinkedIn description"))
check("...with no ping", p.notifier.jobs == [])
check("...counted by kind, and as an account-wide outage",
      out == "parked" and state.parked == {"billing": 1} and state.hard_down == {"billing": 1})
out, p, state = one_job(FAIL_TRANSIENT, insert_ok=False)
check("a park whose write failed is not counted as parked", out == "parked" and not state.parked)
out, p, state = one_job(FAIL_TRANSIENT)
check("a transient failure parks but is not an outage", state.parked == {"transient": 1} and not state.hard_down)

section("the already-stored guard")
out, p, state = one_job({"tier": "APPLY", "reason": "r"}, row={"id": "x", "tier": "APPLY", "status": "applied"})
check("a stored row is skipped", out == "skipped")
check("...before the description fetch, the Claude call, the write and the ping",
      p.linkedin.described == [] and p.classifier.seen == [] and p.db.inserted == [] and p.notifier.jobs == [])
check("...and does not count against the cap", state.used == {} and state.attempted == 0)
out, p, state = one_job({"tier": "INELIGIBLE", "reason": "r"}, row={"id": "x", "tier": "INELIGIBLE", "status": "new"})
check("an INELIGIBLE stored row is skipped too", out == "skipped" and p.classifier.seen == [])

section("a stored PENDING row is classified and promoted, not inserted")
out, p, state = one_job({"tier": "APPLY", "reason": "r"}, row={"id": "x", "tier": "PENDING", "status": "new"})
check("it falls through to the classifier", len(p.classifier.seen) == 1)
check("...is promoted with update_job_classification (insert_job would change nothing)",
      len(p.db.updated) == 1 and p.db.updated[0]["tier"] == "APPLY" and p.db.inserted == [])
check("...and pinged while its status is still 'new'", out == "pushed" and len(p.notifier.jobs) == 1)
out, p, state = one_job({"tier": "APPLY", "reason": "r"}, row={"id": "x", "tier": "PENDING", "status": "applied"})
check("a PENDING row already applied to is promoted but not pinged",
      len(p.db.updated) == 1 and p.notifier.jobs == [] and out == "classified")
out, p, state = one_job(FAIL_TRANSIENT, row={"id": "x", "tier": "PENDING", "status": "new"})
check("a PENDING row that fails again stays parked: no second row, not recounted",
      out == "parked" and p.db.inserted == [] and not state.parked and state.failures == {"transient": 1})

section("new rows: stored first, then pinged by tier")
out, p, state = one_job({"tier": "APPLY", "reason": "r"})
check("APPLY -> stored and pinged", out == "pushed" and len(p.db.inserted) == 1 and len(p.notifier.jobs) == 1)
check("...the log order is guard, fetch, classify, store, ping",
      [n for n, _ in p.log] == ["db.get_job_row", "linkedin.fetch_description", "classifier.classify",
                                "db.insert_job", "notifier.push_job"])
out, p, state = one_job({"tier": "APPLY_CAVEAT", "reason": "r"})
check("APPLY_CAVEAT -> stored and pinged (silently, by the notifier)", out == "pushed" and len(p.notifier.jobs) == 1)
out, p, state = one_job({"tier": "INELIGIBLE", "reason": "r"})
check("INELIGIBLE -> stored, never pinged", out == "classified" and len(p.db.inserted) == 1 and p.notifier.jobs == [])
out, p, state = one_job({"tier": "APPLY", "reason": "r"}, insert_ok=False)
check("a failed write is never pinged (the next run finds the job again)", p.notifier.jobs == [])
out, p, state = one_job({"tier": "APPLY", "reason": "r", "salary": "$90,000/yr"})
check("the classifier's salary is kept when the listing had none", p.db.inserted[0].get("salary") == "$90,000/yr")

section("retry_pending drains the backlog")
PENDING = [
    {"id": "p1", "title": "A", "company": "C", "location": "L", "description": "d", "salary": None, "status": "new"},
    {"id": "p2", "title": "B", "company": "C", "location": "L", "description": "d", "salary": "$30/hr", "status": "new"},
    {"id": "p3", "title": "C", "company": "C", "location": "L", "description": None, "salary": None, "status": "applied"},
]


def drain(results, *, rows=PENDING, state=None, advance=0.0):
    p = Pipeline(real_db)
    p.db.pending = [dict(r) for r in rows]
    p.db.state = dict(state or {})
    seq = list(results)
    p.classifier.verdict = lambda _j: dict(seq.pop(0))
    with p.install(main):
        run_state = main.RunState()
        p.clock.advance(advance)
        main.retry_pending(run_state)
    return p, run_state


p, st = drain([{"tier": "APPLY", "reason": "good", "salary": "$40/hr"},
               {"tier": "INELIGIBLE", "reason": "no"},
               {"tier": "APPLY", "reason": "good"}])
check("every row is promoted with update_job_classification", [u["id"] for u in p.db.updated] == ["p1", "p2", "p3"])
check("...never through insert_job", p.db.inserted == [])
check("an APPLY promotion of a 'new' row pings; INELIGIBLE never does; an 'applied' row never does",
      [j["id"] for j in p.notifier.jobs] == ["p1"])
check("...and the ping carries the new verdict", p.notifier.jobs[0]["tier"] == "APPLY")
check("salary is passed only when the row lacked one",
      p.db.updated[0]["salary"] == "$40/hr" and p.db.updated[1]["salary"] is None)
check("retries count as attempted and succeeded", st.attempted == 3 and st.succeeded == 3 and st.pushed == 1)
check("a row parked without a description is re-classified title-only (the cap applies in classify)",
      p.classifier.seen[2]["description"] is None)

p, st = drain([FAIL_BILLING, {"tier": "APPLY", "reason": ""}])
check("a billing failure stops the drain after ONE attempt",
      len(p.classifier.seen) == 1 and p.db.updated == [] and p.notifier.jobs == [])
check("...and is counted for the classifier-down alert", st.hard_down == {"billing": 1})

p, st = drain([FAIL_MALFORMED, {"tier": "APPLY", "reason": ""}, {"tier": "APPLY", "reason": ""}])
check("a poison row is skipped, not fatal", [u["id"] for u in p.db.updated] == ["p2", "p3"])
p, st = drain([FAIL_MALFORMED, FAIL_TRANSIENT, {"tier": "APPLY", "reason": ""}])
check("two consecutive failures stop the pass", len(p.classifier.seen) == 2 and p.db.updated == [])
p, st = drain([{"tier": "APPLY", "reason": ""}] * 3, advance=main.config.RUN_TIME_BUDGET_S + 1)
check("the time budget stops the drain before any call", p.classifier.seen == [] and p.db.updated == [])
p, st = drain([], rows=[])
check("an empty queue makes no Claude call and no count query",
      p.classifier.seen == [] and not p.calls("db.count_pending_jobs"))

section("owner alerts for the classifier")
p, st = drain([{"tier": "APPLY", "reason": ""}] * 3, state={main.DOWN_ALERT_KEY: "2026-09-30T00:00:00+00:00"})
check("recovery after an announced outage alerts the OWNER once",
      len(p.notifier.alerts) == 1 and "recovered" in p.notifier.alerts[0][0].lower())
check("...at default priority, and clears the marker",
      p.notifier.alerts[0][2] == "default" and main.DOWN_ALERT_KEY not in p.db.state)
check("...never through push_job", all(j["id"].startswith("p") for j in p.notifier.jobs))
p, st = drain([{"tier": "APPLY", "reason": ""}] * 3)
check("no recovery alert when no outage was announced", p.notifier.alerts == [])


def alerts_for(hard_down=None, parked=None, attempted=0, succeeded=0, state=None, failures=None):
    p = Pipeline(real_db)
    p.db.state = dict(state or {})
    p.db.count_pending = 12
    with p.install(main):
        run_state = main.RunState()
        run_state.hard_down.update(hard_down or {})
        run_state.parked.update(parked or {})
        run_state.failures.update(failures or {})
        run_state.attempted, run_state.succeeded = attempted, succeeded
        main.send_alerts(run_state)
    return p


p = alerts_for(hard_down={"billing": 3}, parked={"billing": 3}, attempted=3, failures={"billing": 3})
titles = [t for _m, t, _p in p.notifier.alerts]
check("billing parks -> one 'classifier down' alert to the owner", titles == ["Brice: classifier down"], str(titles))
check("...naming the kind, the parked count and the queue",
      "(billing)" in p.notifier.alerts[0][0] and "parked 3" in p.notifier.alerts[0][0] and "12 waiting" in p.notifier.alerts[0][0])
check("...recording the throttle marker", main.DOWN_ALERT_KEY in p.db.state)
check("...and no 'all classifications failed' alert on top of it", "Brice: all classifications failed" not in titles)
fresh = main.datetime.now(main.timezone.utc).isoformat()
p = alerts_for(hard_down={"billing": 3}, parked={"billing": 3}, attempted=3, state={main.DOWN_ALERT_KEY: fresh})
check("a marker younger than 6 h suppresses the next down alert", p.notifier.alerts == [])
old = (main.datetime.now(main.timezone.utc) - main.timedelta(hours=7)).isoformat()
p = alerts_for(hard_down={"auth": 1}, parked={"auth": 1}, attempted=1, state={main.DOWN_ALERT_KEY: old})
check("a marker older than 6 h alerts again (kind auth)",
      len(p.notifier.alerts) == 1 and "(auth)" in p.notifier.alerts[0][0])
p = alerts_for(hard_down={"billing": 1}, attempted=1, state={main.DOWN_ALERT_KEY: "not a date"})
check("an unreadable marker alerts rather than stays silent", len(p.notifier.alerts) == 1)
p = alerts_for(parked={"transient": 2}, attempted=2, failures={"transient": 2})
check("transient parks alone never raise the down alert", p.notifier.alerts == [])

section("contracts the rest of the pipeline depends on")
check("PENDING is not a verdict the classifier can return", "PENDING" not in real_classifier._VALID_TIERS)
check("classifier._failed marks failed, kind transient by default",
      real_classifier._failed("x").get("failed") is True and real_classifier._failed("x")["failed_kind"] == "transient")

sys.exit(testkit.finish())
