"""backfill_norm_keys.py, offline: what it plans, how it writes, how it pages.

This script rewrites the dedup key on ~119,000 rows across three production
databases, so each property it relies on is pinned here against a fake client:
it rewrites exactly the stale keys, never a row that changed after it was read,
never skips or repeats a row while paging, asks for nothing back, and stops the
moment Supabase says the quota is spent.

Run: cd scraper && python test_backfill_norm_keys.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import backfill_norm_keys as b
import db

_fails = 0


def check(label, cond, why=""):
    global _fails
    if cond:
        print(f"  PASS  {label}")
    else:
        _fails += 1
        print(f"  FAIL  {label}" + (f"\n        {why}" if why else ""))


print("\n-- plan: exactly the stale keys, computed the way every writer computes them --")

ROWS = [
    # full-time row carrying today's shared key -> gains |ft
    {"id": "4301092859", "company": "Meta", "title": "Production Engineering",
     "norm_key": "meta|production engineering"},
    # internship already correct -> untouched
    {"id": "4470115507", "company": "Meta", "title": "Production Engineering Intern",
     "norm_key": "meta|production engineering"},
    # tracker row with no intern word -> keeps its internship-class key
    {"id": "gh:0123456789abcdef", "company": "Acme", "title": "Software Engineer",
     "norm_key": "acme|software engineer"},
    # internship keyed under the old season-stripping rule -> brought current
    {"id": "4359297232", "company": "RoviSys", "title": "Engineering Co-op - Fall 2026",
     "norm_key": "rovisys|engineering"},
    # blank title -> untouched ("acme|" never becomes "acme||ft")
    {"id": "ats:blank", "company": "Acme", "title": "", "norm_key": "acme|"},
    # NULL key -> filled
    {"id": "ats:null", "company": "Acme", "title": "Data Engineer", "norm_key": None},
]
planned = {job["id"]: new for job, new in b.plan(ROWS)}
check("the full-time row gains |ft",
      planned.get("4301092859") == "meta|production engineering|ft", str(planned))
check("a correct internship key is left alone", "4470115507" not in planned)
check("a tracker row keeps its internship-class key", "gh:0123456789abcdef" not in planned)
check("an old-rule internship key is brought to the current rule",
      planned.get("4359297232") == "rovisys|engineering fall 2026")
check("a blank title is left alone", "ats:blank" not in planned)
check("a NULL key is filled", planned.get("ats:null") == "acme|data engineer|ft")
check("nothing else is planned", len(planned) == 3, str(sorted(planned)))


print("\n-- write_one: conditional, minimal, counted --")


class _Resp:
    def __init__(self, count):
        self.count = count
        self.data = []


class _Update:
    def __init__(self, log, count):
        self.log, self.count = log, count

    def eq(self, col, val):
        self.log.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self.log.append(("is", col, val))
        return self

    def execute(self):
        return _Resp(self.count)


class _WriteTable:
    def __init__(self, log, count):
        self.log, self.count = log, count

    def update(self, payload, **kw):
        self.log.append(("update", payload, kw.get("returning"), kw.get("count")))
        return _Update(self.log, self.count)


class _WriteClient:
    def __init__(self, count=1):
        self.log = []
        self.count = count

    def table(self, name):
        assert name == "jobs"
        return _WriteTable(self.log, self.count)


c = _WriteClient(count=1)
n = b.write_one(c, ROWS[0], "meta|production engineering|ft")
update = c.log[0]
check("it writes only the norm_key column", update[1] == {"norm_key": "meta|production engineering|ft"})
check("it asks for nothing back (returning=minimal)", update[2] == b.ReturnMethod.minimal,
      "the default returns the whole row, description included, on every one of ~119k writes")
check("it asks for the matched-row count", update[3] == b.CountMethod.exact)
check("it targets the row by id", ("eq", "id", "4301092859") in c.log)
check("it only writes if the key is still the one that was read",
      ("eq", "norm_key", "meta|production engineering") in c.log, str(c.log))
check("a written row reports 1", n == 1)

c = _WriteClient(count=1)
b.write_one(c, ROWS[5], "acme|data engineer|ft")
check("a NULL key is matched with IS NULL, not = NULL", ("is", "norm_key", "null") in c.log, str(c.log))

c = _WriteClient(count=0)
check("a row that changed since the read reports 0, so it is skipped, not overwritten",
      b.write_one(c, ROWS[0], "meta|production engineering|ft") == 0)


print("\n-- write_all: a spent quota stops the run --")


class _QuotaUpdate(_Update):
    def execute(self):
        raise RuntimeError("exceed_egress_quota: restricted due to the following violations")


class _QuotaTable(_WriteTable):
    def update(self, payload, **kw):
        return _QuotaUpdate(self.log, 0)


class _QuotaClient(_WriteClient):
    def table(self, name):
        return _QuotaTable(self.log, 0)


stopped = False
try:
    b.write_all(_QuotaClient(), [(ROWS[0], "meta|production engineering|ft")] * 50, workers=4)
except db.QuotaExceeded:
    stopped = True
check("QuotaExceeded propagates instead of being counted as 50 failures", stopped)


print("\n-- write_all: one client per worker thread when a factory is given --")

made: list = []


def _factory():
    c = _WriteClient(count=1)
    made.append(c)
    return c


written, skipped, failed = b.write_all(None, [(ROWS[0], "meta|production engineering|ft")] * 40,
                                       workers=4, make_client=_factory)
check("every row is written through a thread's own client", (written, skipped, failed) == (40, 0, 0),
      str((written, skipped, failed)))
check("at most one client per worker thread", 1 <= len(made) <= 4, f"{len(made)} clients for 4 workers")
check("each client carried real writes", sum(len(c.log) for c in made) > 0)


print("\n-- load_all_jobs: keyset paging never skips or repeats a row --")


class _PageQuery:
    def __init__(self, rows, calls):
        self.rows, self.calls = rows, calls
        self.after, self.n = None, None

    def select(self, cols):
        return self

    def order(self, col):
        assert col == "id"
        return self

    def limit(self, n):
        self.n = n
        return self

    def gt(self, col, val):
        assert col == "id"
        self.after = val
        return self

    def execute(self):
        self.calls.append(self.after)
        page = [r for r in sorted(self.rows, key=lambda r: r["id"])
                if self.after is None or r["id"] > self.after][: self.n]
        # A row inserted mid-walk, sorting BEFORE the current cursor: offset
        # paging would shift and skip; keyset paging must simply not care.
        if len(self.calls) == 2:
            self.rows.append({"id": "000-inserted-mid-walk", "title": "", "company": "", "norm_key": ""})
        return type("R", (), {"data": page})()


class _PageClient:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def table(self, name):
        return _PageQuery(self.rows, self.calls)


orig_page = b.PAGE
b.PAGE = 3
table = [{"id": f"id{i:03d}", "title": "t", "company": "c", "norm_key": "c|t"} for i in range(10)]
got = b.load_all_jobs(_PageClient(table))
b.PAGE = orig_page
ids = [r["id"] for r in got]
check("every original row is read exactly once",
      sorted(i for i in ids if i.startswith("id")) == [f"id{i:03d}" for i in range(10)]
      and len(ids) == len(set(ids)), str(ids))


print()
if _fails:
    print(f"{_fails} FAILED")
    sys.exit(1)
print("all backfill checks passed")
