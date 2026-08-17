"""Guards the PENDING queue — parking, the circuit breaker, and the drain.

Offline: no network, no API key, no Supabase. Every seam is stubbed by
module-attribute assignment, the same way the rest of this repo's tests work.

What is actually being protected here:

  1. A job whose classification fails must be PARKED, never dropped and never
     stored with a made-up verdict. Dropping loses LinkedIn jobs permanently
     once they age out of LOOKBACK_SECONDS; a fake verdict buried 82 jobs on
     2026-08-04 and 32 of them were real APPLYs.
  2. A billing outage must cost ONE failed API call per run, not 3 attempts x
     exponential backoff per job — the difference between a run that finishes
     and one that hits the 20-minute Actions timeout.
  3. A poison row must not block the queue behind it.

Run:  cd scraper && python test_pending_queue.py
"""

import sys
import types

import anthropic

import classifier
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


def _sdk_error(cls, message):
    """Build a real SDK exception without a network round-trip.

    The constructors want a live httpx response, so the instance is created
    uninitialised and given just the attribute _error_kind reads. Testing
    through the real classes matters — the point of _error_kind is its
    isinstance checks, and faking those would test nothing.
    """
    exc = cls.__new__(cls)
    exc.message = message
    exc.args = (message,)
    return exc


# ── 1. Error taxonomy ────────────────────────────────────────────────────
print("\n-- _error_kind: what is worth retrying --")

check("credit exhaustion is 'billing'",
      classifier._error_kind(
          _sdk_error(anthropic.BadRequestError,
                     "Your credit balance is too low to access the Anthropic API")) == "billing")
check("...case-insensitively",
      classifier._error_kind(
          _sdk_error(anthropic.BadRequestError, "YOUR CREDIT BALANCE IS TOO LOW")) == "billing",
      "the message is matched on a substring, not pinned to exact text")
check("another 400 is transient, not billing",
      classifier._error_kind(
          _sdk_error(anthropic.BadRequestError, "max_tokens must be positive")) == "transient",
      "only credit-balance 400s are permanent")
check("AuthenticationError is 'auth'",
      classifier._error_kind(_sdk_error(anthropic.AuthenticationError, "invalid x-api-key")) == "auth")
check("PermissionDeniedError is 'auth'",
      classifier._error_kind(_sdk_error(anthropic.PermissionDeniedError, "no access")) == "auth")
check("RateLimitError is 'transient'",
      classifier._error_kind(_sdk_error(anthropic.RateLimitError, "slow down")) == "transient")
check("a plain exception is 'transient'",
      classifier._error_kind(RuntimeError("connection reset")) == "transient")


# ── 2. The breaker ───────────────────────────────────────────────────────
print("\n-- the breaker: a billing outage costs ONE call, not 3x backoff per job --")


class _Recorder:
    """Stands in for the Anthropic client and counts calls."""

    def __init__(self, raises):
        self.calls = 0
        self._raises = raises
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls += 1
        raise self._raises


def _with_stubs(raises):
    classifier._API_HARD_DOWN = None
    rec = _Recorder(raises)
    classifier._get_client = lambda: rec
    classifier._system_prompt = lambda: "stub"
    slept = []
    classifier.time = types.SimpleNamespace(sleep=slept.append)
    return rec, slept


JOB = {"id": "li:1", "title": "SWE Intern", "company": "Acme",
       "location": "Atlanta, GA", "description": "d"}

_orig_client, _orig_prompt, _orig_time = (
    classifier._get_client, classifier._system_prompt, classifier.time)

rec, slept = _with_stubs(_sdk_error(anthropic.BadRequestError,
                                    "Your credit balance is too low"))
first = classifier.classify(dict(JOB))
check("a billing failure returns failed", first.get("failed") is True)
check("...tagged as billing", first.get("failed_kind") == "billing")
check("...after exactly ONE API call", rec.calls == 1,
      f"got {rec.calls} — a billing error must not burn the retry loop")
check("...with no backoff sleeps", slept == [], f"slept {slept}")

second = classifier.classify({**JOB, "id": "li:2"})
check("the NEXT job short-circuits with zero further calls", rec.calls == 1,
      f"got {rec.calls} — the breaker did not hold")
check("...and is still reported failed", second.get("failed") is True)
check("...carrying the original kind", second.get("failed_kind") == "billing")

rec, slept = _with_stubs(_sdk_error(anthropic.RateLimitError, "slow down"))
transient = classifier.classify(dict(JOB))
check("a transient error DOES use the retry loop",
      rec.calls == classifier.MAX_CLASSIFY_ATTEMPTS,
      f"got {rec.calls} of {classifier.MAX_CLASSIFY_ATTEMPTS}")
check("...and does sleep between attempts", len(slept) == classifier.MAX_CLASSIFY_ATTEMPTS - 1)
check("...and does not trip the breaker", classifier._API_HARD_DOWN is None,
      "a rate limit must not stop the whole run")

classifier._get_client, classifier._system_prompt, classifier.time = (
    _orig_client, _orig_prompt, _orig_time)
classifier._API_HARD_DOWN = None


# ── 3. Parking ───────────────────────────────────────────────────────────
print("\n-- process_job parks instead of dropping --")

_m = {}
_orig = {k: getattr(main, k) for k in
         ("classify", "insert_job", "push_job", "fetch_description")}


def _stub_main(classify_result, insert_ok=True):
    _m.clear()
    _m["inserted"], _m["pushed"] = [], []
    main.classify = lambda job: dict(classify_result)
    main.insert_job = lambda job: (_m["inserted"].append(dict(job)), insert_ok)[1]
    main.push_job = lambda job: _m["pushed"].append(dict(job))
    main.fetch_description = lambda _id: ("desc from linkedin", None, None, False, None)
    main._PARKED_THIS_RUN.clear()


_stub_main({"failed": True, "failed_kind": "billing", "tier": "APPLY_CAVEAT",
            "reason": "x", "suggested_resume": "General"})
out = main.process_job({"id": "ats:acme:1", "title": "SWE Intern", "company": "Acme",
                        "location": "Atlanta", "description": "a real description"})
check("a failed classification still writes a row", len(_m["inserted"]) == 1,
      "dropping it loses the job once the listing ages out")
check("...as tier PENDING", _m["inserted"][0]["tier"] == "PENDING")
check("...preserving the fetched description",
      _m["inserted"][0]["description"] == "a real description",
      "the description is the expensive part; it must survive the park")
check("...with no push", _m["pushed"] == [],
      "PENDING is a queue state, not a verdict — it must never notify")
check("...returning False", out is False, "the watchers truth-test this for 'notified'")
check("...and counting the park by kind", main._PARKED_THIS_RUN.get("billing") == 1)

_stub_main({"failed": True, "failed_kind": "transient", "tier": "APPLY_CAVEAT",
            "reason": "x", "suggested_resume": "General"}, insert_ok=False)
main.process_job({"id": "ats:acme:2", "title": "T", "company": "C", "location": "L",
                  "description": "d"})
check("a failed park is not counted as parked", not main._PARKED_THIS_RUN,
      "the counter drives the canary; it must reflect rows that actually landed")

for k, v in _orig.items():
    setattr(main, k, v)


# ── 4. The drain ─────────────────────────────────────────────────────────
print("\n-- retry_pending drains the backlog --")

_d = {}
_orig_d = {k: getattr(main, k) for k in
           ("classify", "fetch_pending_jobs", "count_pending_jobs",
            "update_job_classification", "push_job", "push_canary",
            "get_state", "set_state", "clear_state")}


def _stub_drain(rows, results, state=None):
    _d.clear()
    _d.update(updates=[], pushed=[], canaries=[], state=dict(state or {}), cleared=[])
    seq = list(results)
    main.fetch_pending_jobs = lambda limit: [dict(r) for r in rows]
    main.count_pending_jobs = lambda: 0
    main.classify = lambda job: dict(seq.pop(0))
    main.update_job_classification = lambda *a, **kw: (
        _d["updates"].append((a, kw)), True)[1]
    main.push_job = lambda job: _d["pushed"].append(dict(job))
    main.push_canary = lambda msg: _d["canaries"].append(msg)
    main.get_state = lambda k: _d["state"].get(k)
    main.set_state = lambda k, v: _d["state"].__setitem__(k, v)
    main.clear_state = lambda k: _d["cleared"].append(k)
    main._PARKED_THIS_RUN.clear()


ROWS = [{"id": "li:a", "title": "A", "company": "C", "location": "L",
         "description": "d", "salary": None},
        {"id": "li:b", "title": "B", "company": "C", "location": "L",
         "description": "d", "salary": "$30/hr"}]

_stub_drain(ROWS, [
    {"tier": "APPLY", "reason": "good", "suggested_resume": "General", "salary": "$40/hr"},
    {"tier": "INELIGIBLE", "reason": "no", "suggested_resume": "General"},
])
notified = main.retry_pending()
check("both rows were updated", len(_d["updates"]) == 2)
check("an APPLY promotion pushes", len(_d["pushed"]) == 1 and notified == 1)
check("an INELIGIBLE promotion does NOT push",
      all(p["tier"] != "INELIGIBLE" for p in _d["pushed"]))
check("salary is passed when the row lacked one",
      _d["updates"][0][1].get("salary") == "$40/hr")
check("salary is NOT passed when the row already had one",
      _d["updates"][1][1].get("salary") is None,
      "promotion must never blank or overwrite a stored salary")

_stub_drain(ROWS, [
    {"failed": True, "failed_kind": "billing", "tier": "APPLY_CAVEAT",
     "reason": "", "suggested_resume": "General"},
    {"tier": "APPLY", "reason": "", "suggested_resume": "General"},
])
main.retry_pending()
check("a billing failure stops the drain immediately", _d["updates"] == [],
      "every remaining row would fail identically")
check("...pushing nothing", _d["pushed"] == [])
check("...and counting the outage for the canary",
      main._PARKED_THIS_RUN.get("billing") == 1)

_stub_drain(ROWS, [
    {"failed": True, "failed_kind": "malformed", "tier": "APPLY_CAVEAT",
     "reason": "", "suggested_resume": "General"},
    {"tier": "APPLY", "reason": "", "suggested_resume": "General"},
])
main.retry_pending()
check("a poison row is skipped, not fatal", len(_d["updates"]) == 1,
      "an oldest-first queue must not be blocked by one bad row")

_stub_drain(ROWS, [
    {"failed": True, "failed_kind": "malformed", "tier": "APPLY_CAVEAT",
     "reason": "", "suggested_resume": "General"},
    {"failed": True, "failed_kind": "transient", "tier": "APPLY_CAVEAT",
     "reason": "", "suggested_resume": "General"},
])
main.retry_pending()
check("two consecutive failures stop the pass", _d["updates"] == [])


# ── 5. Canaries ──────────────────────────────────────────────────────────
print("\n-- canaries: one per outage, not one per run --")

_stub_drain([], [])
main._PARKED_THIS_RUN.update({"billing": 3})
main._maybe_alert_classifier_down()
check("an outage with no prior marker alerts", len(_d["canaries"]) == 1)
check("...naming the kind", "billing" in _d["canaries"][0].lower())
check("...and records the marker", main._DOWN_ALERT_KEY in _d["state"])

before = len(_d["canaries"])
main._maybe_alert_classifier_down()
check("a fresh marker suppresses the next alert", len(_d["canaries"]) == before,
      "a multi-day outage must not push every 20 minutes")

_stub_drain([], [])
main._PARKED_THIS_RUN.update({"transient": 5})
main._maybe_alert_classifier_down()
check("transient parks alone never alert", _d["canaries"] == [],
      "a network blip self-heals in 20 minutes and is not worth a 3am push")

_stub_drain(ROWS[:1], [{"tier": "APPLY", "reason": "", "suggested_resume": "General"}],
            state={main._DOWN_ALERT_KEY: "2026-08-16T00:00:00+00:00"})
main.retry_pending()
check("recovery alerts once when the marker exists", len(_d["canaries"]) == 1)
check("...and clears the marker", main._DOWN_ALERT_KEY in _d["cleared"],
      "otherwise every later drain run re-announces the recovery")

_stub_drain(ROWS[:1], [{"tier": "APPLY", "reason": "", "suggested_resume": "General"}])
main.retry_pending()
check("no recovery alert when no outage was announced", _d["canaries"] == [])

for k, v in _orig_d.items():
    setattr(main, k, v)


# ── 6. Contracts the rest of the pipeline depends on ─────────────────────
print("\n-- contracts that must not drift --")

check("PENDING is not a valid classifier verdict",
      "PENDING" not in classifier._VALID_TIERS,
      "it is a queue state; the model must never be able to produce it")
check("_failed still marks failed", classifier._failed("x").get("failed") is True)
check("_failed defaults to transient", classifier._failed("x")["failed_kind"] == "transient")
check("the watchers' imports from main still resolve",
      all(hasattr(main, n) for n in
          ("process_job", "_is_senior_role", "_is_new_grad_role", "_is_non_internship_title")),
      "ats_watch.py and github_watch.py import these by name")

import ast
import inspect

_run = inspect.getsource(main.run)

# scrape_runs has exactly four stat columns. An unknown key makes the whole
# UPDATE inside finish_run fail, the row stays unfinished, and start_run()'s
# 20-minute lock then blocks the NEXT scheduled run — so this is checked by
# parsing the actual call, not by looking for substrings.
_tree = ast.parse(inspect.getsource(main).lstrip())
_kwargs = None
for node in ast.walk(_tree):
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "finish_run"):
        _kwargs = {kw.arg for kw in node.keywords}
        break
check("finish_run receives exactly its four existing stat keys",
      _kwargs == {"total_raw", "new_jobs", "notified", "rate_limited"},
      f"got {sorted(_kwargs) if _kwargs else None} — an unknown column leaves the "
      f"run-lock held for 20 minutes")
check("retry_pending runs before the search loop",
      _run.index("retry_pending()") < _run.index("for term in SEARCH_TERMS"))
check("...and inside the try, so finish_run still releases the lock",
      _run.index("try:") < _run.index("retry_pending()"))

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
