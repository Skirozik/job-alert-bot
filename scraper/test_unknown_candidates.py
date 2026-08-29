"""find_unknown_candidates: the dedup question, asked server-side.

THE RISK DIRECTION IS INVERTED HERE, and that shapes every assertion below.
Everywhere else in this pipeline a wrong answer costs an extra push -- annoying,
recoverable. Here a candidate wrongly reported as *known* is skipped silently
and never notified: a missed job, which is the failure this whole bot exists to
prevent. So the tests care far more about "does it correctly report things as
NEW" than about over-reporting.

That is also why every error path must return ALL ids rather than none. Failing
open costs a re-classification; failing closed costs opportunities.

Run: cd scraper && python test_unknown_candidates.py
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import db

_fails = 0


def check(label, cond, why=""):
    global _fails
    if cond:
        print(f"  PASS  {label}")
    else:
        _fails += 1
        print(f"  FAIL  {label}" + (f"\n        {why}" if why else ""))


_sent: list = []


def _client(returns, raises=None):
    """Records every RPC payload so the positional pairing can be asserted."""
    def rpc(name, params):
        _sent.append((name, params))
        if raises is not None:
            raise raises
        idx = len(_sent) - 1
        data = returns[idx] if isinstance(returns, list) and idx < len(returns) else returns
        return types.SimpleNamespace(execute=lambda: types.SimpleNamespace(data=data))
    return types.SimpleNamespace(rpc=rpc)


def use(client):
    _sent.clear()
    db.get_client = lambda: client


_orig = db.get_client

JOBS = [
    {"id": "ats:aaa", "company": "Acme", "title": "SWE Intern"},
    {"id": "gh:bbb", "company": "Beta", "title": "iOS Intern"},
    {"id": "4451", "company": "Gamma", "title": "Data Intern"},
]

print("-- the question sent up must pair ids with norm_keys POSITIONALLY --")

use(_client([["gh:bbb"]]))
out = db.find_unknown_candidates(JOBS)
name, params = _sent[0]
check("calls the unknown_candidates RPC", name == "unknown_candidates")
check("sends one id per candidate, in order",
      params["p_ids"] == ["ats:aaa", "gh:bbb", "4451"])
check("sends a norm_key for every id",
      len(params["p_norm_keys"]) == len(params["p_ids"]),
      "unnest() pairs the two arrays by position; a length mismatch silently "
      "misaligns every row after the gap")
check("norm_keys are derived from company+title",
      params["p_norm_keys"][0] == db.make_norm_key("Acme", "SWE Intern"))
check("only the unknown id comes back", out == {"gh:bbb"})

print("\n-- a norm_key already on the row is reused, not recomputed --")

use(_client([[]]))
db.find_unknown_candidates([{"id": "x", "company": "C", "title": "T",
                             "norm_key": "precomputed|key"}])
check("an existing norm_key is trusted", _sent[0][1]["p_norm_keys"] == ["precomputed|key"],
      "the watchers set norm_key before calling; recomputing it here would let "
      "the two drift apart")

print("\n-- batching splits the request and unions the answers --")

use(_client([["a1"], ["b1"]]))
many = [{"id": f"id{i}", "company": "C", "title": f"T{i}"} for i in range(5)]
out = db.find_unknown_candidates(many, batch_size=3)
check("splits at batch_size", len(_sent) == 2)
check("first batch carries batch_size ids", len(_sent[0][1]["p_ids"]) == 3)
check("second batch carries the remainder", len(_sent[1][1]["p_ids"]) == 2)
check("answers from every batch are unioned", out == {"a1", "b1"},
      "dropping a batch's answer would mark real jobs as already-seen")

print("\n-- response shapes --")

use(_client([[{"unknown_candidates": "wrapped"}]]))
check("a wrapped {col: value} row is understood",
      db.find_unknown_candidates(JOBS[:1]) == {"wrapped"},
      "postgrest may return setof text wrapped; misreading it as empty would "
      "report every candidate as already-known")

use(_client([[]]))
check("an empty answer means nothing is new",
      db.find_unknown_candidates(JOBS) == set())

use(_client([None]))
check("a null answer means nothing is new, not an error",
      db.find_unknown_candidates(JOBS) == set())

print("\n-- EVERY error path must fail OPEN, or jobs go silently unnotified --")

use(_client(None, raises=Exception("connection reset by peer")))
check("a transport error reports every candidate as new",
      db.find_unknown_candidates(JOBS) == {"ats:aaa", "gh:bbb", "4451"},
      "returning an empty set here would skip all three, permanently")

use(_client(None, raises=Exception("500 Internal Server Error")))
check("a server error reports every candidate as new",
      db.find_unknown_candidates(JOBS) == {"ats:aaa", "gh:bbb", "4451"})

print("\n-- but an UNAPPLIED MIGRATION must fail LOUD, not fall open --")

# The distinction is the whole point: a transient error is brief, so a wasted
# pass is cheap. A missing function is permanent, and "every candidate is new"
# at ATS scale is ~30,700 insert_job round trips every five minutes -- worse
# than the full-table read this replaced, and invisible because runs stay green.
for shape in [
    "Could not find the function public.unknown_candidates in the schema cache",
    "{'code': 'PGRST202', 'message': 'not found'}",
]:
    use(_client(None, raises=Exception(shape)))
    try:
        db.find_unknown_candidates(JOBS)
        check(f"raises on: {shape[:44]}", False, "it fell open instead")
    except db.DedupUnavailable as e:
        check(f"raises on: {shape[:44]}", "migrations/" in str(e),
              "the message must name the migration to apply")
    except Exception as e:
        check(f"raises on: {shape[:44]}", False, f"wrong exception type: {type(e).__name__}")

check("DedupUnavailable is distinguishable from a transport failure",
      issubclass(db.DedupUnavailable, RuntimeError)
      and not isinstance(Exception("timeout"), db.DedupUnavailable))

print("\n-- degenerate input --")

use(_client([[]]))
check("an empty candidate list makes no request at all",
      db.find_unknown_candidates([]) == set() and _sent == [],
      "an empty sweep should not cost a round trip")

use(_client([[]]))
db.find_unknown_candidates([{"id": "", "company": "C", "title": "T"},
                            {"id": "keep", "company": "C", "title": "T"}])
check("rows with no id are dropped before the request",
      _sent[0][1]["p_ids"] == ["keep"],
      "a blank id would pair against the wrong norm_key and misalign the batch")

db.get_client = _orig

print("\n-- the rewired ATS loop: the actual place a job can go missing --")

import ats_watch as w

_w_orig = {k: getattr(w, k) for k in
           ("start_run", "finish_run", "insert_job", "process_job",
            "fetch_all_listings", "find_unknown_candidates")}

processed: list = []
w.start_run = lambda source="ats": 1
w.finish_run = lambda run_id, **kw: kw
w.insert_job = lambda job: True
w.process_job = lambda job: (processed.append(job["id"]), False)[1]

LISTINGS = [
    {"id": "ats:new1",   "company": "Acme",  "title": "Software Engineer Intern", "location": "Atlanta"},
    {"id": "ats:known",  "company": "Beta",  "title": "iOS Engineer Intern",      "location": "Austin"},
    # Same company AND title: one norm_key, neither stored yet. The old code
    # caught this by mutating the downloaded index; the server-side answer
    # cannot, because at question time neither exists.
    {"id": "ats:dupA",   "company": "Gamma", "title": "Data Intern",              "location": "NYC"},
    {"id": "ats:dupB",   "company": "Gamma", "title": "Data Intern",              "location": "NYC"},
    {"id": "ats:senior", "company": "Delta", "title": "Senior Staff Engineer",    "location": "SF"},
]
w.fetch_all_listings = lambda cfg: [dict(j) for j in LISTINGS]
w.find_unknown_candidates = lambda jobs: {j["id"] for j in jobs if j["id"] != "ats:known"}

w.run()

check("a genuinely new job is still processed", "ats:new1" in processed,
      "THE failure that matters: a new job skipped is a missed opportunity")
check("a job the server reports as stored is skipped", "ats:known" not in processed)
check("a pre-filtered title never reaches the classifier", "ats:senior" not in processed)
check("two listings sharing a norm_key in one sweep are processed once",
      len([p for p in processed if p.startswith("ats:dup")]) == 1,
      f"got {processed} — the within-sweep sets are what catch this now")

for k, v in _w_orig.items():
    setattr(w, k, v)

total = 5 + 1 + 4 + 3 + 2 + 3 + 2 + 4
print(f"\n{total - _fails} passed, {_fails} failed")
sys.exit(1 if _fails else 0)
