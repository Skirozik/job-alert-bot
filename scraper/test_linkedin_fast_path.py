"""Offline regressions for candidate dedup and LinkedIn detail pacing."""

import sys
import types

import db
import linkedin


passed = failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{f' — {detail}' if detail else ''}")


print("\n-- find_known_candidates: indexed lookups stay candidate-scoped --")


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, recorder):
        self.recorder = recorder
        self.column = None
        self.values = []

    def select(self, columns):
        return self

    def in_(self, column, values):
        self.column = column
        self.values = list(values)
        self.recorder.append((column, list(values)))
        return self

    def execute(self):
        if self.column == "id" and "100" in self.values:
            return _Result([{"id": "100", "norm_key": "acme|software engineer"}])
        if self.column == "norm_key" and "other|data engineer" in self.values:
            return _Result([{"id": "ats:other:1", "norm_key": "other|data engineer"}])
        return _Result([])


class _Client:
    def __init__(self, recorder):
        self.recorder = recorder

    def table(self, name):
        assert name == "jobs"
        return _Query(self.recorder)


calls = []
original_client = db.get_client
db.get_client = lambda: _Client(calls)
candidates = [
    {"id": "100", "company": "Acme", "title": "Software Engineer Intern"},
    {"id": "200", "company": "Other", "title": "Data Engineer Intern"},
]
known_ids, known_norms = db.find_known_candidates(candidates)
db.get_client = original_client

check("existing id is returned", "100" in known_ids)
check("cross-source norm duplicate is returned", "other|data engineer" in known_norms)
check("queries contain only current candidate values",
      all(len(values) <= len(candidates) for _, values in calls), f"calls={calls}")


print("\n-- start_run: locks are scoped by source --")


class _LockQuery:
    def __init__(self, recorder):
        self.recorder = recorder
        self.mode = "select"

    def select(self, columns):
        return self

    def eq(self, column, value):
        self.recorder.append(("eq", column, value))
        return self

    def gte(self, column, value):
        return self

    def is_(self, column, value):
        return self

    def insert(self, payload):
        self.mode = "insert"
        self.recorder.append(("insert", payload))
        return self

    def execute(self):
        return _Result([{"id": 42}]) if self.mode == "insert" else _Result([])


class _LockClient:
    def __init__(self, recorder):
        self.recorder = recorder

    def table(self, name):
        assert name == "scrape_runs"
        return _LockQuery(self.recorder)


lock_calls = []
db.get_client = lambda: _LockClient(lock_calls)
run_id = db.start_run("linkedin")
db.get_client = original_client
check("source is part of the active-run lookup",
      ("eq", "source", "linkedin") in lock_calls, f"calls={lock_calls}")
check("source is stored with the run row",
      any(call[0] == "insert" and call[1].get("source") == "linkedin" for call in lock_calls),
      f"calls={lock_calls}")
check("the inserted run id is returned", run_id == 42, f"got {run_id}")


print("\n-- detail pacing: first request is immediate, later starts are spaced --")


class _Clock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


clock = _Clock()
original_time = linkedin.time
original_random = linkedin.random
linkedin.time = clock
linkedin.random = types.SimpleNamespace(uniform=lambda lo, hi: 2.5)
linkedin._DETAIL_NEXT_REQUEST_AT = 0.0

linkedin._wait_for_detail_slot()
check("first detail request does not sleep", clock.sleeps == [], f"slept={clock.sleeps}")
linkedin._wait_for_detail_slot()
check("second request waits for the shared slot", clock.sleeps == [2.5], f"slept={clock.sleeps}")

linkedin.time = original_time
linkedin.random = original_random
linkedin._DETAIL_NEXT_REQUEST_AT = 0.0


print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
