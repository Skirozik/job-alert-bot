"""archive_stale.py, offline: it touches only untouched review rows past the cutoff.

Run: cd scraper && python test_archive_stale.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import archive_stale as a

_fails = 0


def check(label, cond, why=""):
    global _fails
    if cond:
        print(f"  PASS  {label}")
    else:
        _fails += 1
        print(f"  FAIL  {label}" + (f"\n        {why}" if why else ""))


class _Query:
    def __init__(self, log, count):
        self.log, self._count = log, count

    def eq(self, col, val):
        self.log.append(("eq", col, val)); return self

    def in_(self, col, vals):
        self.log.append(("in", col, tuple(vals))); return self

    def lt(self, col, val):
        self.log.append(("lt", col, val)); return self

    def limit(self, n):
        self.log.append(("limit", n)); return self

    def execute(self):
        return type("R", (), {"count": self._count, "data": []})()


class _Table:
    def __init__(self, log, count):
        self.log, self.count = log, count

    def select(self, cols, **kw):
        self.log.append(("select", cols, kw.get("count"))); return _Query(self.log, self.count)

    def update(self, payload, **kw):
        self.log.append(("update", payload, kw.get("returning"), kw.get("count")))
        return _Query(self.log, self.count)


class _Client:
    def __init__(self, count=7):
        self.log, self.count = [], count

    def table(self, name):
        assert name == "jobs", name
        return _Table(self.log, self.count)


print("\n-- the cutoff --")
now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
check("the cutoff is 45 days back", a.cutoff(now).startswith("2026-08-22T12:00"), a.cutoff(now))

print("\n-- the update --")
c = _Client(count=7)
n = a.archive_stale(c, "2026-08-22T00:00:00+00:00")
check("it reports the matched-row count", n == 7)
check("it writes only status='archived'", c.log[0][:2] == ("update", {"status": "archived"}), str(c.log[0]))
check("it asks for nothing back", c.log[0][2] == a.ReturnMethod.minimal)
check("it asks for an exact count", c.log[0][3] == a.CountMethod.exact)
check("only rows still 'new' (never applied, saved or dismissed)", ("eq", "status", "new") in c.log, str(c.log))
check("only review tiers (INELIGIBLE and PENDING untouched)",
      ("in", "tier", ("APPLY", "APPLY_CAVEAT")) in c.log, str(c.log))
check("only rows found before the cutoff", ("lt", "found_at", "2026-08-22T00:00:00+00:00") in c.log, str(c.log))

print("\n-- the dry run counts exactly what the update would touch --")
c2 = _Client(count=3)
check("count_stale returns the count", a.count_stale(c2, "X") == 3)
filters = lambda log: [e for e in log if e[0] in ("eq", "in", "lt")]
c3 = _Client(); a.archive_stale(c3, "X")
check("dry run and update use identical filters", filters(c2.log) == filters(c3.log),
      f"{filters(c2.log)} vs {filters(c3.log)}")

print()
if _fails:
    print(f"{_fails} FAILED"); sys.exit(1)
print("all archive checks passed")
