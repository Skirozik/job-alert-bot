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
