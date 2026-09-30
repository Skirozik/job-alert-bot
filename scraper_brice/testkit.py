"""Shared helpers for scraper_brice's offline tests. No test logic lives here.

Every test file starts with:

    import testkit
    testkit.block_network()

before it imports any pipeline module, then reports through check() and ends
with sys.exit(testkit.finish()).

Importing this module also blanks the five secret environment variables.
main.py loads ../.env.brice with python-dotenv, which never overrides a
variable that already exists, so a developer's real credentials cannot leak
into a test run -- and every test works with no .env.brice at all.
"""

import contextlib
import io
import logging
import os
import socket
import sys
import types
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

SECRET_ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "ANTHROPIC_API_KEY", "NTFY_TOPIC", "OWNER_NTFY_TOPIC")
for _name in SECRET_ENV:
    os.environ[_name] = ""


# ── reporting ────────────────────────────────────────────────────────────────

_passed = 0
_failed = 0


def check(name: str, cond, detail: str = "") -> bool:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))
    return bool(cond)


def section(title: str) -> None:
    print(f"\n-- {title} --")


def finish() -> int:
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


def raises(fn, exc_type):
    """True if fn() raises exc_type; otherwise a string saying what happened."""
    try:
        fn()
    except exc_type:
        return True
    except Exception as other:  # noqa: BLE001 -- the test wants to name the wrong type
        return f"raised {type(other).__name__}: {other}"
    return "did not raise"


# ── no network ───────────────────────────────────────────────────────────────

NETWORK_MESSAGE = "network in an offline test"


def _refuse(*_args, **_kwargs):
    raise AssertionError(NETWORK_MESSAGE)


def block_network() -> None:
    """Make every HTTP library and raw socket connect raise AssertionError.

    requests (module functions and Session.request) covers linkedin.py and
    notifier.py; socket.create_connection / socket.connect cover httpx, which
    the Anthropic and Supabase clients use.
    """
    import requests
    import requests.api

    for name in ("get", "post", "put", "patch", "delete", "head", "request"):
        setattr(requests, name, _refuse)
        setattr(requests.api, name, _refuse)
    requests.Session.request = lambda self, *a, **k: _refuse()
    socket.create_connection = _refuse
    socket.socket.connect = lambda self, *a, **k: _refuse()
    socket.socket.connect_ex = lambda self, *a, **k: _refuse()


# ── Supabase stand-in ────────────────────────────────────────────────────────

class Call:
    """One recorded query: the table and every builder call made before execute()."""

    def __init__(self, table: str, ops: list):
        self.table = table
        self.ops = ops

    @property
    def names(self) -> list[str]:
        return [name for name, _a, _k in self.ops]

    def op(self, name: str):
        """(args, kwargs) of the first builder call with this name, or None."""
        for n, a, k in self.ops:
            if n == name:
                return a, k
        return None

    def __repr__(self):
        return f"Call({self.table}, {self.names})"


class _Query:
    def __init__(self, fake: "FakeClient", table: str):
        self._fake = fake
        self._table = table
        self._ops: list = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def builder(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self
        return builder

    def execute(self):
        call = Call(self._table, list(self._ops))
        self._fake.calls.append(call)
        return self._fake._respond(call)


class FakeClient:
    """A chainable supabase client: every builder call returns the query and
    execute() answers from `responder(call)` (data, or an exception instance
    to raise), else from the fixed `exc` / `data`. `calls` records each query.
    """

    def __init__(self, data=None, exc=None, responder=None, count=None):
        self.data, self.exc, self.responder, self.count = data, exc, responder, count
        self.calls: list[Call] = []

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params=None):
        q = _Query(self, f"rpc:{name}")
        q._ops.append(("rpc", (name, params), {}))
        return q

    def _respond(self, call: Call):
        if self.responder is not None:
            out = self.responder(call)
            if isinstance(out, BaseException):
                raise out
            return types.SimpleNamespace(data=out, count=self.count)
        if self.exc is not None:
            raise self.exc
        return types.SimpleNamespace(data=self.data, count=self.count)


def client(data=None, exc=None, responder=None, count=None) -> FakeClient:
    return FakeClient(data=data, exc=exc, responder=responder, count=count)


# ── time ─────────────────────────────────────────────────────────────────────

class FakeClock:
    """A monotonic clock that only moves when something sleeps or the test advances it."""

    def __init__(self, start: float = 1000.0):
        self.now = float(start)
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def as_time_module(self):
        """A stand-in for the `time` module: sleep, monotonic and time."""
        return types.SimpleNamespace(sleep=self.sleep, monotonic=self.monotonic, time=self.monotonic)


# ── Anthropic SDK errors ─────────────────────────────────────────────────────

def sdk_error(cls, message: str):
    """A real SDK exception instance built without an HTTP response.

    The constructors want a live httpx response, so the instance is created
    uninitialised and given the attributes classifier._error_kind reads --
    testing through the real classes matters, because _error_kind's point is
    its isinstance checks.
    """
    exc = cls.__new__(cls)
    exc.message = message
    exc.args = (message,)
    return exc


# ── environment and patching ─────────────────────────────────────────────────

@contextlib.contextmanager
def env_cleared():
    """Blank the five secret env vars AND the module attributes already read from them.

    config, db and classifier copy their values at import time, so blanking
    os.environ alone would not reach them.
    """
    saved_env = {k: os.environ.get(k) for k in SECRET_ENV}
    saved_attrs = []
    for k in SECRET_ENV:
        os.environ[k] = ""
    for modname in ("config", "db", "classifier", "notifier"):
        mod = sys.modules.get(modname)
        if mod is None:
            continue
        for k in SECRET_ENV:
            if k in vars(mod):
                saved_attrs.append((mod, k, getattr(mod, k)))
                setattr(mod, k, "")
    try:
        yield
    finally:
        for mod, k, v in saved_attrs:
            setattr(mod, k, v)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_MISSING = object()


@contextlib.contextmanager
def patched(obj, **attrs):
    """Temporarily set attributes on a module or object; restored on exit."""
    saved = {k: getattr(obj, k, _MISSING) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield obj
    finally:
        for k, v in saved.items():
            if v is _MISSING:
                delattr(obj, k)
            else:
                setattr(obj, k, v)


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def text(self) -> str:
        return "\n".join(r.getMessage() for r in self.records)

    def messages(self) -> list[str]:
        return [r.getMessage() for r in self.records]


@contextlib.contextmanager
def captured_logs():
    """Collect every log record emitted inside the block (all loggers, DEBUG and up)."""
    handler = _ListHandler()
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


@contextlib.contextmanager
def captured_stdout():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


def raiser(label: str):
    """A callable that fails the test loudly if anything calls it."""
    def _raise(*_a, **_k):
        raise AssertionError(f"{label} must not be called here")
    return _raise


class Forbidden(types.ModuleType):
    """A module stand-in whose every attribute access raises: proves a code path never touches it."""

    def __init__(self, name: str):
        super().__init__(name)
        object.__setattr__(self, "touched", [])

    def __getattribute__(self, attr):
        if attr in ("__name__", "__class__", "__dict__", "touched", "__spec__", "__repr__"):
            return object.__getattribute__(self, attr)
        object.__getattribute__(self, "touched").append(attr)
        raise AssertionError(f"{object.__getattribute__(self, '__name__')}.{attr} used in a path that must not touch it")


# ── a fake pipeline for main.py ──────────────────────────────────────────────
# Recording stand-ins for every collaborator main.py reaches through a module
# attribute. `Pipeline.install(main)` swaps them in (plus a FakeClock and a
# deterministic random.uniform that returns the lower bound) and restores the
# originals on exit. Every call lands in `pipeline.log` as (name, detail).

class FakeDb:
    def __init__(self, pipeline, real_db):
        self._p = pipeline
        self.QuotaExceeded = real_db.QuotaExceeded
        self.DbUnavailable = real_db.DbUnavailable
        self.RUN_LOCK_MINUTES = real_db.RUN_LOCK_MINUTES
        self.stored_ids: set = set()
        self.stored_nks: set = set()
        self.rows: dict = {}                 # id -> {"id", "tier", "status"} for get_job_row
        self.pending: list = []              # rows fetch_pending_jobs returns
        self.state: dict = {}                # bot_state
        self.start_run_result = 7            # an id, None (locked) or an exception instance
        self.insert_ok = True
        self.update_ok = True
        self.quota_on_insert = None          # raise QuotaExceeded once this many inserts have landed
        self.count_pending = 0
        self.inserted: list = []
        self.updated: list = []
        self.finished: list = []

    def _rec(self, name, detail=None):
        self._p.log.append(("db." + name, detail))

    def find_known_candidates(self, jobs):
        jobs = list(jobs)
        self._rec("find_known_candidates", [j["id"] for j in jobs])
        ids = {j["id"] for j in jobs if j["id"] in self.stored_ids}
        nks = {j["norm_key"] for j in jobs if j.get("norm_key") in self.stored_nks}
        return ids, nks

    def get_job_row(self, job_id):
        self._rec("get_job_row", job_id)
        return self.rows.get(job_id)

    def insert_job(self, job):
        self._rec("insert_job", job["id"])
        if self.quota_on_insert is not None and len(self.inserted) >= self.quota_on_insert:
            raise self.QuotaExceeded("quota spent (test)")
        if self.insert_ok:
            self.inserted.append(dict(job))
        return self.insert_ok

    def update_job_classification(self, job_id, tier, reason, salary=None):
        self._rec("update_job_classification", job_id)
        self.updated.append({"id": job_id, "tier": tier, "reason": reason, "salary": salary})
        return self.update_ok

    def fetch_pending_jobs(self, limit):
        self._rec("fetch_pending_jobs", limit)
        return [dict(r) for r in self.pending[:limit]]

    def count_pending_jobs(self):
        self._rec("count_pending_jobs")
        return self.count_pending

    def get_state(self, key):
        self._rec("get_state", key)
        return self.state.get(key)

    def set_state(self, key, value):
        self._rec("set_state", key)
        self.state[key] = value

    def clear_state(self, key):
        self._rec("clear_state", key)
        self.state.pop(key, None)

    def start_run(self):
        self._rec("start_run")
        if isinstance(self.start_run_result, BaseException):
            raise self.start_run_result
        return self.start_run_result

    def finish_run(self, run_id, **stats):
        self._rec("finish_run", run_id)
        self.finished.append((run_id, stats))


class FakeClassifier:
    def __init__(self, pipeline):
        self._p = pipeline
        self.seen: list = []                 # the job dicts classify() received
        self.verdict = lambda job: {"tier": "APPLY", "reason": "fit"}
        self.cost_s = 0.0                    # clock seconds each call takes

    def classify(self, job):
        self._p.log.append(("classifier.classify", job["id"]))
        self.seen.append(dict(job))
        self._p.clock.advance(self.cost_s)
        return dict(self.verdict(job))


class FakeNotifier:
    def __init__(self, pipeline):
        self._p = pipeline
        self.jobs: list = []
        self.alerts: list = []               # (message, title, priority)
        self.push_ok = True                  # what push_job answers (False: ntfy rejected the ping)

    def push_job(self, job):
        self._p.log.append(("notifier.push_job", job["id"]))
        self.jobs.append(dict(job))
        return self.push_ok

    def push_owner_alert(self, message, *, title="Brice pipeline alert", priority="urgent", tags="warning,robot"):
        self._p.log.append(("notifier.push_owner_alert", title))
        self.alerts.append((message, title, priority))
        return True


class FakeLinkedIn:
    """`pages(term, start)` returns a list of jobs, "rate_limited", or another error string."""

    def __init__(self, pipeline):
        self._p = pipeline
        self.requests: list = []             # (clock time, term, location, start)
        self.described: list = []
        self.pages = lambda term, start: []
        self.cost_s = 0.0

    def fetch_listings(self, keyword, location, lookback_seconds=0, start=0):
        self._p.log.append(("linkedin.fetch_listings", (keyword, start)))
        self.requests.append((self._p.clock.now, keyword, location, start))
        self._p.clock.advance(self.cost_s)
        out = self.pages(keyword, start)
        if isinstance(out, str):
            return [], out
        return [dict(j) for j in out], None

    def fetch_description(self, job_id):
        self._p.log.append(("linkedin.fetch_description", job_id))
        self.described.append(job_id)
        return "LinkedIn description. " * 20, None, None, False, None


class FakeAtsPass:
    def __init__(self, pipeline):
        self._p = pipeline
        self.candidates: list = []
        self.stats = {"boards": 26, "boards_with_listings": 26, "listings": 1000, "dropped_by": {}}
        self.workday_fetches: list = []

    def collect_ats_candidates(self):
        self._p.log.append(("ats_pass.collect_ats_candidates", None))
        return [dict(j) for j in self.candidates], dict(self.stats, kept=len(self.candidates))

    def fetch_workday_description(self, url):
        self._p.log.append(("ats_pass.fetch_workday_description", url))
        self.workday_fetches.append(url)
        return "Workday description. " * 20


class FakeJobright:
    """`lists[name]` = {"url", "status", "etag", "rows", "unparsed", "jobs", "dropped_by", "dropped_samples",
    "problems"}."""

    STALE_RULE = "posted more than 10 days ago"      # jobright.STALE_RULE's text

    def __init__(self, pipeline):
        self._p = pipeline
        self.lists: dict = {}
        self.fetches: list = []              # (url, etag sent)
        self._current = None

    def fetch_readme(self, url, etag=None):
        self._p.log.append(("jobright.fetch_readme", url))
        self.fetches.append((url, etag))
        self._current = next((n for n, info in self.lists.items() if info.get("url") == url), None)
        info = self.lists.get(self._current, {})
        return info.get("status", 0), "README", info.get("etag")

    def parse_readme(self, text, list_name, today):
        info = self.lists.get(list_name, {})
        return [{"row": i} for i in range(info.get("rows", 0))], info.get("unparsed", 0)

    def rows_to_jobs(self, rows, list_name, *, support_only, today, samples=None):
        info = self.lists.get(list_name, {})
        if samples is not None:
            for rule, titles in info.get("dropped_samples", {}).items():
                samples.setdefault(rule, []).extend(titles)
        return [dict(j) for j in info.get("jobs", [])], Counter(info.get("dropped_by", {}))

    def canary_problems(self, status, parsed_rows, unparsed_link_rows):
        return list(self.lists.get(self._current, {}).get("problems", []))


class Pipeline:
    """Every fake plus the clock. `with pipeline.install(main): ...`"""

    def __init__(self, real_db):
        self.log: list = []
        self.clock = FakeClock()
        self.db = FakeDb(self, real_db)
        self.classifier = FakeClassifier(self)
        self.notifier = FakeNotifier(self)
        self.linkedin = FakeLinkedIn(self)
        self.ats_pass = FakeAtsPass(self)
        self.jobright = FakeJobright(self)
        self.random = types.SimpleNamespace(uniform=lambda a, b: a)

    def calls(self, prefix: str) -> list:
        return [(name, detail) for name, detail in self.log if name.startswith(prefix)]

    @contextlib.contextmanager
    def install(self, main_module):
        with patched(main_module, db=self.db, classifier=self.classifier, notifier=self.notifier,
                     linkedin=self.linkedin, ats_pass=self.ats_pass, jobright=self.jobright,
                     time=self.clock.as_time_module(), _monotonic=self.clock.monotonic, random=self.random):
            yield self


def li_job(n: int, title: str = "Associate Network Engineer", company: str = "") -> dict:
    """A LinkedIn search card as linkedin._parse_listings returns it."""
    return {"id": str(4_000_000_000 + n), "title": title, "company": company or f"Company {n}",
            "location": "Remote - US", "url": f"https://www.linkedin.com/jobs/view/{4_000_000_000 + n}/",
            "posted_at": None, "description": None, "is_easy_apply": False}


def ats_job(n: int, title: str, company: str, *, family: str = "NETWORK_INFRA", workday: bool = True,
            description=None) -> dict:
    """An ATS candidate as ats_pass.collect_ats_candidates returns it (already through source_gate)."""
    url = (f"https://acme.wd5.myworkdayjobs.com/External/job/Austin-TX/Role_{n}" if workday
           else f"https://job-boards.greenhouse.io/acme/jobs/{n}")
    return {"id": f"ats:{n:016x}", "title": title, "company": company, "location": "Austin, TX", "url": url,
            "apply_url": url, "posted_at": None, "description": description, "source": "ats", "family": family,
            "search_term": f"ats:{company}"}


def jr_job(n: int, title: str, company: str, list_name: str, family: str = "SALES_SOLUTIONS") -> dict:
    """A jobright candidate as jobright.rows_to_jobs returns it (title-only)."""
    return {"id": f"jr:{n:016x}", "title": title, "company": company, "location": "Remote - US",
            "url": f"https://jobright.ai/jobs/info/{n:016x}", "apply_url": None, "posted_at": None,
            "description": None, "is_easy_apply": False, "logo_url": None, "source": "jobright",
            "family": family, "search_term": f"jobright:{list_name}"}

