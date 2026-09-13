"""QuotaExceeded: a spent Supabase quota must stop a run, not fail open.

Offline: no network, no Supabase. Seams are stubbed by module-attribute
assignment, the same way the rest of this repo's tests work.

What is being protected:

  1. Every fail-open branch in db.py stays fail-open for a TRANSIENT error --
     that behaviour is load-bearing (see test_unknown_candidates.py: a
     candidate wrongly reported as known is a missed job).
  2. But the SAME branches must raise QuotaExceeded when the error is the
     gateway's "restricted due to the following violations" refusal, because
     that one never clears itself and fail-open then re-classifies the whole
     board with Claude on every run. Measured 2026-09-12: 724 jobs
     re-processed in one run, nothing stored.
  3. start_run, the first database call of every entry point, is the choke
     point: raising there means no description is fetched and no Claude call
     is made.

Run:  cd scraper && python test_quota_guard.py
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import db
from db import QuotaExceeded, DedupUnavailable

_pass = _fail = 0


def check(name, cond, detail=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{f' — {detail}' if detail else ''}")


def raises(fn, exc_type):
    try:
        fn()
    except exc_type:
        return True
    except Exception as other:  # noqa: BLE001 -- the test wants to name the wrong type
        return f"raised {type(other).__name__}: {other}"
    return "did not raise"


# ── The errors, as the client really surfaces them ───────────────────────────
# The 402 body Supabase's gateway returned on 2026-09-12, wrapped the way
# postgrest-py wraps it (str(exc) is the dict's repr; .message is the prose).
try:
    from postgrest.exceptions import APIError
except ImportError:  # pragma: no cover -- the guard must not depend on the class
    class APIError(Exception):
        def __init__(self, error):
            super().__init__(str(error))
            self.message = error.get("message")

QUOTA_BODY = {
    "message": "Service for this project is restricted due to the following "
               "violations: exceed_egress_quota. The project owner must upgrade "
               "their plan or remove spend caps to restore service.",
}
QUOTA = APIError(QUOTA_BODY)
TRANSIENT = APIError({"message": "canceling statement due to statement timeout",
                      "code": "57014"})
MISSING_FN = APIError({"message": "Could not find the function public.unknown_candidates "
                                  "in the schema cache", "code": "PGRST202"})


class _HttpError(Exception):
    """An httpx-shaped error: status lives on .response, not in the text."""
    def __init__(self, status):
        super().__init__("HTTP error")
        self.response = types.SimpleNamespace(status_code=status)


# ── 1. The detector ──────────────────────────────────────────────────────────
print("\n-- _raise_if_quota --")
check("the real 402 body raises QuotaExceeded",
      raises(lambda: db._raise_if_quota(QUOTA), QuotaExceeded) is True)
check("a 402 exposed only as response.status_code raises",
      raises(lambda: db._raise_if_quota(_HttpError(402)), QuotaExceeded) is True)
check("a transient statement timeout does NOT raise",
      db._raise_if_quota(TRANSIENT) is None)
check("a missing-function error does NOT raise (that is DedupUnavailable's job)",
      db._raise_if_quota(MISSING_FN) is None)
check("a 500 with no quota prose does NOT raise",
      db._raise_if_quota(_HttpError(500)) is None)
check("a plain exception does NOT raise",
      db._raise_if_quota(RuntimeError("connection reset")) is None)
try:
    db._raise_if_quota(QUOTA)
except QuotaExceeded as e:
    check("the message names the cause and chains the original",
          "quota" in str(e).lower() and e.__cause__ is QUOTA, str(e))


# ── A chainable stub: any method call returns the same object; execute() ends it
class _Chain:
    def __init__(self, data=None, exc=None):
        self._data, self._exc = data, exc

    def __getattr__(self, _name):
        return lambda *a, **kw: self

    def execute(self):
        if self._exc is not None:
            raise self._exc
        return types.SimpleNamespace(data=self._data)


def client(data=None, exc=None):
    chain = _Chain(data, exc)
    return types.SimpleNamespace(table=lambda *_a, **_k: chain, rpc=lambda *_a, **_k: chain)


_orig_get_client = db.get_client


def use(c):
    db.get_client = lambda: c


JOBS = [{"id": "ats:aaa", "company": "Acme", "title": "SWE Intern", "norm_key": "acme|swe intern"},
        {"id": "gh:bbb", "company": "Beta", "title": "iOS Intern", "norm_key": "beta|ios intern"}]

try:
    # ── 2. start_run: the choke point ──────────────────────────────────────
    print("\n-- start_run --")
    use(client(exc=QUOTA))
    check("quota -> raises, instead of 'proceeding without run-lock'",
          raises(lambda: db.start_run("linkedin"), QuotaExceeded) is True)
    use(client(exc=TRANSIENT))
    check("transient -> still proceeds without the lock (-1)", db.start_run("linkedin") == -1)
    # start_run first asks "is another run active?" (a select returning rows
    # means yes); an empty answer there and an id from the insert is healthy.
    use(client(data=[]))
    check("healthy, no active run -> proceeds (returns an id or -1, never raises)",
          db.start_run("linkedin") in (-1, None) or isinstance(db.start_run("linkedin"), int))

    # ── 3. the dedup lookups ────────────────────────────────────────────────
    print("\n-- find_unknown_candidates --")
    use(client(exc=QUOTA))
    check("quota -> raises, instead of 'every candidate is new'",
          raises(lambda: db.find_unknown_candidates(JOBS), QuotaExceeded) is True)
    use(client(exc=TRANSIENT))
    check("transient -> still fails OPEN (all ids reported unknown)",
          db.find_unknown_candidates(JOBS) == {"ats:aaa", "gh:bbb"})
    use(client(exc=MISSING_FN))
    check("missing function -> still DedupUnavailable, untouched",
          raises(lambda: db.find_unknown_candidates(JOBS), DedupUnavailable) is True)

    print("\n-- find_known_candidates --")
    use(client(exc=QUOTA))
    check("quota -> raises",
          raises(lambda: db.find_known_candidates(JOBS), QuotaExceeded) is True)
    use(client(exc=TRANSIENT))
    check("transient -> still fails open (nothing known)",
          db.find_known_candidates(JOBS) == (set(), set()))

    # ── 4. the per-job paths, so a quota that runs out MID-run stops the loop ─
    print("\n-- per-job paths --")
    use(client(exc=QUOTA))
    check("insert_job: quota -> raises, instead of False + next job",
          raises(lambda: db.insert_job({"id": "ats:aaa", "tier": "APPLY"}), QuotaExceeded) is True)
    check("get_job_row: quota -> raises, instead of 'as if new'",
          raises(lambda: db.get_job_row("ats:aaa"), QuotaExceeded) is True)
    check("fetch_pending_jobs: quota -> raises, instead of []",
          raises(lambda: db.fetch_pending_jobs(40), QuotaExceeded) is True)
    check("claim_notification: quota -> raises, instead of 'claim-failed'",
          raises(lambda: db.claim_notification("ats:aaa"), QuotaExceeded) is True)
    check("update_job_classification: quota -> raises, instead of False + next Claude call",
          raises(lambda: db.update_job_classification("ats:aaa", "APPLY", "fits", "General"),
                 QuotaExceeded) is True)
    check("count_pending_jobs: quota -> raises, instead of 0",
          raises(lambda: db.count_pending_jobs(), QuotaExceeded) is True)
    use(client(exc=TRANSIENT))
    check("update_job_classification: transient -> False, as before",
          db.update_job_classification("ats:aaa", "APPLY", "fits", "General") is False)
    check("count_pending_jobs: transient -> 0, as before", db.count_pending_jobs() == 0)
    check("insert_job: transient -> False, as before",
          db.insert_job({"id": "ats:aaa", "tier": "APPLY"}) is False)
    check("get_job_row: transient -> None, as before", db.get_job_row("ats:aaa") is None)
    check("fetch_pending_jobs: transient -> [], as before", db.fetch_pending_jobs(40) == [])
    check("claim_notification: transient -> (False, 'claim-failed'), as before",
          db.claim_notification("ats:aaa") == (False, "claim-failed"))
finally:
    db.get_client = _orig_get_client

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
