"""claim_notification's fail-open / fail-closed boundary.

This is the highest-consequence branch in the notification path, in both
directions:

  - Too permissive, and a Supabase blip re-notifies everything -- the burst of
    duplicate pushes this whole change exists to stop.
  - Too strict, and a missing migration silently mutes the pipeline. That
    failure is invisible: runs still exit 0, the dashboard still fills, and the
    only symptom is notifications that never arrive.

So the contract is asserted explicitly rather than inferred from behaviour.

Run: cd scraper && python test_notification_ledger.py
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# db imports config, which requires nothing at import time, and supabase, which
# is installed. get_client is stubbed per-case below so nothing touches network.
import db

_fails = 0


def check(label, cond, why=""):
    global _fails
    if cond:
        print(f"  PASS  {label}")
    else:
        _fails += 1
        print(f"  FAIL  {label}" + (f"\n        {why}" if why else ""))


def _client_raising(exc):
    def rpc(*_a, **_kw):
        raise exc
    return types.SimpleNamespace(rpc=lambda *a, **kw: types.SimpleNamespace(
        execute=lambda: (_ for _ in ()).throw(exc)))


def _client_returning(data):
    return types.SimpleNamespace(rpc=lambda *a, **kw: types.SimpleNamespace(
        execute=lambda: types.SimpleNamespace(data=data)))


_orig_get_client = db.get_client


def with_client(client):
    db.get_client = lambda: client


print("-- a missing migration must fall OPEN, or the pipeline goes silently mute --")

for shape in [
    "{'code': 'PGRST202', 'message': 'Could not find the function'}",
    "Could not find the function public.claim_job_notification(p_id) in the schema cache",
    "PGRST202",
    "searched for the function in the schema cache but it was not found",
]:
    with_client(_client_returning(None))
    db.get_client = lambda e=Exception(shape): (_ for _ in ()).throw(e)
    allowed, why = db.claim_notification("li:1")
    check(f"falls open on: {shape[:52]}...", allowed is True and why == "rpc-missing",
          f"got ({allowed}, {why!r}) — a strict match here mutes every push")

print("\n-- every OTHER failure must fall CLOSED --")

for exc in [
    Exception("connection reset by peer"),
    Exception("timeout"),
    Exception("500 Internal Server Error"),
    Exception("permission denied for function claim_job_notification"),
]:
    db.get_client = lambda e=exc: (_ for _ in ()).throw(e)
    allowed, why = db.claim_notification("li:1")
    check(f"falls closed on: {str(exc)[:44]}", allowed is False and why == "claim-failed",
          f"got ({allowed}, {why!r}) — falling open here is the duplicate-push bug")

print("\n-- well-formed responses are honoured --")

with_client(_client_returning([{"should_notify": True, "reason": "claimed"}]))
check("a granted claim allows the push", db.claim_notification("li:1") == (True, "claimed"))

with_client(_client_returning([{"should_notify": False, "reason": "sibling:notified"}]))
check("a sibling that was already notified suppresses",
      db.claim_notification("li:1") == (False, "sibling:notified"))

with_client(_client_returning([{"should_notify": False, "reason": "row-not-new:applied"}]))
check("a row the user already applied to suppresses",
      db.claim_notification("li:1") == (False, "row-not-new:applied"))

with_client(_client_returning({"should_notify": True, "reason": "claimed"}))
check("a bare dict (not wrapped in a list) is accepted",
      db.claim_notification("li:1") == (True, "claimed"),
      "postgrest may return the row unwrapped for a single-row function")

print("\n-- an unrecognised response shape must not be read as permission --")

for bad in [[], None, [{"unexpected": 1}], "nonsense"]:
    with_client(_client_returning(bad))
    allowed, why = db.claim_notification("li:1")
    check(f"declines on {bad!r}", allowed is False and why == "claim-malformed",
          f"got ({allowed}, {why!r})")

db.get_client = _orig_get_client

total = 4 + 4 + 4 + 4
print(f"\n{total - _fails} passed, {_fails} failed")
sys.exit(1 if _fails else 0)
