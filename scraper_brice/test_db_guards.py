"""db.py guards: quota stop, fail-closed run-lock, per-candidate dedup, the
already-stored guard, PENDING helpers, redaction and key-code parity.

Offline: no network, no Supabase. The client is a recording stand-in
(testkit.FakeClient) installed by replacing db.get_client, the same seam the
main pipeline's tests use.

What is being protected (adapted from scraper/test_quota_guard.py):

  1. Every fail-open branch stays fail-open for a TRANSIENT error, but raises
     QuotaExceeded for Supabase's "restricted ... exceed_egress_quota" refusal,
     which never clears itself (one main-pipeline run re-processed 724 jobs
     under fail-open, 2026-09-12).
  2. start_run fails CLOSED: a transient error is retried once, then raises
     DbUnavailable -- never "proceed without the lock".
  3. The key code stays byte-identical to scraper/db.py.

Run:  cd scraper_brice && python -X utf8 test_db_guards.py
"""

import testkit

testkit.block_network()

import ast  # noqa: E402
import importlib.util  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from postgrest.exceptions import APIError  # noqa: E402
from postgrest.types import ReturnMethod  # noqa: E402

import db  # noqa: E402
from db import DbUnavailable, QuotaExceeded  # noqa: E402
from testkit import captured_logs, check, client, patched, raises, section  # noqa: E402

QUOTA = APIError({"message": "Service for this project is restricted due to the following "
                             "violations: exceed_egress_quota. The project owner must upgrade "
                             "their plan or remove spend caps to restore service."})
TRANSIENT = APIError({"message": "canceling statement due to statement timeout", "code": "57014"})


class _HttpError(Exception):
    """An httpx-shaped error: the status lives on .response, not in the text."""

    def __init__(self, status):
        super().__init__("HTTP error")
        self.response = types.SimpleNamespace(status_code=status)


_orig_get_client = db.get_client
_orig_time = db.time
_slept: list = []
db.time = types.SimpleNamespace(sleep=_slept.append)


def use(fake):
    db.get_client = lambda: fake
    return fake


JOBS = [{"id": "ats:aaa", "company": "Acme", "title": "Network Engineer", "norm_key": "acme|network engineer|ft"},
        {"id": "jr:0123456789abcdef", "company": "Beta", "title": "Associate Sales Engineer"}]

try:
    # ── 1. the detector ─────────────────────────────────────────────────────
    section("_raise_if_quota")
    check("the real 402 body raises QuotaExceeded", raises(lambda: db._raise_if_quota(QUOTA), QuotaExceeded) is True)
    check("a 402 exposed only as response.status_code raises",
          raises(lambda: db._raise_if_quota(_HttpError(402)), QuotaExceeded) is True)
    check("a transient statement timeout does NOT raise", db._raise_if_quota(TRANSIENT) is None)
    check("a 500 with no quota prose does NOT raise", db._raise_if_quota(_HttpError(500)) is None)
    check("a plain exception does NOT raise", db._raise_if_quota(RuntimeError("connection reset")) is None)
    try:
        db._raise_if_quota(QUOTA)
    except QuotaExceeded as e:
        check("the message names the cause and chains the original",
              "quota" in str(e).lower() and e.__cause__ is QUOTA)

    # ── 2. start_run: the run-lock ──────────────────────────────────────────
    section("start_run: quota raises, anything else fails CLOSED")
    _slept.clear()
    fake = use(client(exc=QUOTA))
    check("quota -> QuotaExceeded", raises(db.start_run, QuotaExceeded) is True)
    check("...on the first attempt, with no retry sleep", len(fake.calls) == 1 and _slept == [],
          f"{len(fake.calls)} calls, slept {_slept}")

    _slept.clear()
    fake = use(client(exc=TRANSIENT))
    check("transient twice -> DbUnavailable, not -1", raises(db.start_run, DbUnavailable) is True)
    check("...after exactly one retry, 10 s apart", len(fake.calls) == 2 and _slept == [10],
          f"{len(fake.calls)} calls, slept {_slept}")

    def blip_then_ok():
        state = {"n": 0}

        def respond(call):
            state["n"] += 1
            if state["n"] == 1:
                return TRANSIENT
            return [] if call.names[0] == "select" else [{"id": 77}]
        return respond

    _slept.clear()
    use(client(responder=blip_then_ok()))
    check("a single blip does not cost the run (retry succeeds -> id)", db.start_run() == 77)

    fake = use(client(responder=lambda call: [{"id": 5}] if call.names[0] == "select" else AssertionError("no insert")))
    check("an unfinished run inside the window -> None (skip this run)", db.start_run() is None)
    sel = fake.calls[0]
    gte = sel.op("gte")
    cutoff = datetime.fromisoformat(gte[0][1]) if gte else None
    expected = datetime.now(timezone.utc) - timedelta(minutes=75)
    check("the lock window is 75 minutes (gte started_at cutoff = now - 75 min)",
          gte is not None and gte[0][0] == "started_at" and abs((cutoff - expected).total_seconds()) < 60,
          f"gte={gte}")
    check("...and only unfinished runs hold it (finished_at is null)",
          sel.op("is_") == (("finished_at", "null"), {}))
    check("RUN_LOCK_MINUTES is 75", db.RUN_LOCK_MINUTES == 75)

    use(client(responder=lambda call: [] if call.names[0] == "select" else [{"id": 42}]))
    check("healthy, no active run -> the new run id", db.start_run() == 42)
    use(client(responder=lambda call: [] if call.names[0] == "select" else []))
    check("an insert that returns no row -> -1 (runs, but no stats row)", db.start_run() == -1)

    with patched(db, SUPABASE_URL="https://abcdefghijkl.supabase.co", SUPABASE_SERVICE_KEY="sb_secret_TESTKEY"):
        use(client(exc=RuntimeError("POST https://abcdefghijkl.supabase.co/rest/v1/scrape_runs failed "
                                    "(apikey sb_secret_TESTKEY)")))
        try:
            db.start_run()
            msg = "did not raise"
        except DbUnavailable as e:
            msg = str(e)
        check("DbUnavailable's message is redacted (no URL, host or key)",
              "abcdefghijkl" not in msg and "sb_secret_TESTKEY" not in msg and "***" in msg, msg)

    # ── 3. per-candidate dedup ──────────────────────────────────────────────
    section("find_known_candidates")
    use(client(exc=QUOTA))
    check("quota -> raises", raises(lambda: db.find_known_candidates(JOBS), QuotaExceeded) is True)
    use(client(exc=TRANSIENT))
    check("transient -> fails open (nothing known)", db.find_known_candidates(JOBS) == (set(), set()))

    fake = use(client(data=[]))
    db.find_known_candidates([])
    check("an empty batch makes no query", fake.calls == [])

    many = [{"id": f"li{i}", "company": "Acme", "title": f"Network Engineer {i}"} for i in range(250)]
    fake = use(client(data=[]))
    db.find_known_candidates(many)
    id_batches = [c.op("in_")[0][1] for c in fake.calls if c.op("in_")[0][0] == "id"]
    key_batches = [c.op("in_")[0][1] for c in fake.calls if c.op("in_")[0][0] == "norm_key"]
    check("250 candidates -> 3 id lookups and 3 key lookups of <= 100",
          [len(b) for b in id_batches] == [100, 100, 50] and [len(b) for b in key_batches] == [100, 100, 50],
          f"{[len(b) for b in id_batches]} / {[len(b) for b in key_batches]}")
    check("...each lookup selects only id,norm_key", all(c.op("select") == (("id,norm_key",), {}) for c in fake.calls))

    fake = use(client(data=[]))
    db.find_known_candidates(JOBS)
    keys = [v for c in fake.calls if c.op("in_")[0][0] == "norm_key" for v in c.op("in_")[0][1]]
    check("a row without norm_key is keyed with make_norm_key (a jr: row gets |ft)",
          "beta|associate sales engineer|ft" in keys, str(keys))
    check("a precomputed norm_key is used as given", "acme|network engineer|ft" in keys)

    fake = use(client(responder=lambda call: [{"id": "ats:aaa", "norm_key": "acme|network engineer|ft"}]
                      if call.op("in_")[0][0] == "id" else [{"id": "old1", "norm_key": "beta|associate sales engineer|ft"}]))
    ids, nks = db.find_known_candidates(JOBS)
    check("stored ids and keys come back from both lookups",
          ids == {"ats:aaa", "old1"} and nks == {"acme|network engineer|ft", "beta|associate sales engineer|ft"},
          f"{ids} {nks}")

    # ── 4. the already-stored guard ─────────────────────────────────────────
    section("get_job_row")
    use(client(exc=QUOTA))
    check("quota -> raises, instead of 'as if new'", raises(lambda: db.get_job_row("ats:aaa"), QuotaExceeded) is True)
    use(client(exc=TRANSIENT))
    check("transient -> None (proceed as if new)", db.get_job_row("ats:aaa") is None)
    fake = use(client(data=[{"id": "ats:aaa", "tier": "APPLY", "status": "applied"}]))
    row = db.get_job_row("ats:aaa")
    check("a stored row comes back with tier and status", row == {"id": "ats:aaa", "tier": "APPLY", "status": "applied"})
    check("...selecting only id,tier,status by primary key",
          fake.calls[0].op("select") == (("id,tier,status",), {}) and fake.calls[0].op("eq") == (("id", "ats:aaa"), {}))
    fake = use(client(data=[]))
    check("an absent row -> None", db.get_job_row("nope") is None)
    fake = use(client(data=[]))
    check("an empty id -> None without a query", db.get_job_row("") is None and fake.calls == [])

    # ── 5. writes ───────────────────────────────────────────────────────────
    section("insert_job")
    use(client(exc=QUOTA))
    check("quota -> raises, instead of False + next job",
          raises(lambda: db.insert_job({"id": "ats:aaa", "tier": "APPLY"}), QuotaExceeded) is True)
    use(client(exc=TRANSIENT))
    check("transient -> False", db.insert_job({"id": "ats:aaa", "tier": "APPLY"}) is False)
    fake = use(client(data=[]))
    ok = db.insert_job({"id": "jr:0123456789abcdef", "title": "Associate Network Engineer", "company": "Initech",
                        "location": "Remote", "url": "https://jobright.ai/jobs/info/0123456789abcdef",
                        "tier": "APPLY_CAVEAT", "reason": "r", "suggested_resume": "General",
                        "target_key": "x", "source": "jobright", "family": "NETWORK_INFRA", "_order": 3})
    up = fake.calls[0].op("upsert")
    payload, kw = up[0][0], up[1]
    check("success -> True", ok is True)
    check("...upsert(on_conflict='id', ignore_duplicates=True, returning=minimal)",
          kw == {"on_conflict": "id", "ignore_duplicates": True, "returning": ReturnMethod.minimal}, str(kw))
    check("...payload has exactly the 15 jobs columns (no suggested_resume / target_key / helper keys)",
          set(payload) == {"id", "title", "company", "location", "url", "search_term", "description", "logo_url",
                           "norm_key", "tier", "reason", "posted_at", "apply_url", "is_easy_apply", "salary"},
          str(sorted(payload)))
    check("...norm_key computed with make_norm_key (full-time |ft)",
          payload["norm_key"] == "initech|associate network engineer|ft", payload["norm_key"])
    with captured_logs() as logs:
        use(client(data=[]))
        db.insert_job({"id": "ats:bbb", "title": "Associate Network Engineer", "tier": "INELIGIBLE"})
        db.update_job_classification("ats:bbb", "APPLY_CAVEAT", "asks 2+ years")
    check("the store and promote log lines name the id, never the tier (the Actions log is public)",
          "DB: stored ats:bbb" in logs.text() and "DB: promoted ats:bbb" in logs.text()
          and "INELIGIBLE" not in logs.text() and "APPLY" not in logs.text(), logs.text())

    section("PENDING helpers")
    use(client(exc=QUOTA))
    check("fetch_pending_jobs: quota -> raises", raises(lambda: db.fetch_pending_jobs(40), QuotaExceeded) is True)
    check("count_pending_jobs: quota -> raises", raises(db.count_pending_jobs, QuotaExceeded) is True)
    check("update_job_classification: quota -> raises",
          raises(lambda: db.update_job_classification("ats:aaa", "APPLY", "fits"), QuotaExceeded) is True)
    use(client(exc=TRANSIENT))
    check("fetch_pending_jobs: transient -> []", db.fetch_pending_jobs(40) == [])
    check("count_pending_jobs: transient -> 0", db.count_pending_jobs() == 0)
    check("update_job_classification: transient -> False",
          db.update_job_classification("ats:aaa", "APPLY", "fits") is False)

    fake = use(client(data=[{"id": "p1"}]))
    rows = db.fetch_pending_jobs(40)
    c = fake.calls[0]
    check("fetch_pending_jobs selects the narrow column list, tier PENDING, oldest first, limited",
          rows == [{"id": "p1"}]
          and c.op("select") == (("id,title,company,location,url,description,salary,status",), {})
          and c.op("eq") == (("tier", "PENDING"), {}) and c.op("order") == (("found_at",), {"desc": False})
          and c.op("limit") == ((40,), {}), str(c.ops))
    fake = use(client(data=[], count=7))
    check("count_pending_jobs returns the exact count", db.count_pending_jobs() == 7)

    fake = use(client(data=[]))
    check("update_job_classification: success -> True", db.update_job_classification("p1", "APPLY", "fits") is True)
    upd = fake.calls[0].op("update")
    check("...a real UPDATE of tier+reason only (no suggested_resume), returning=minimal, by id",
          upd == (({"tier": "APPLY", "reason": "fits"},), {"returning": ReturnMethod.minimal})
          and fake.calls[0].op("eq") == (("id", "p1"), {}), str(upd))
    fake = use(client(data=[]))
    db.update_job_classification("p1", "APPLY_CAVEAT", "asks 2+ years", salary="$80,000/yr")
    check("...salary is written only when given", fake.calls[0].op("update")[0][0].get("salary") == "$80,000/yr")

    section("bot_state and finish_run never raise")
    fake = use(client(data=[]))
    db.set_state("alert_at:x", "2026-09-30T00:00:00+00:00")
    check("set_state upserts on key with returning=minimal",
          fake.calls[0].op("upsert") == (({"key": "alert_at:x", "value": "2026-09-30T00:00:00+00:00"},),
                                         {"on_conflict": "key", "returning": ReturnMethod.minimal}))
    use(client(exc=QUOTA))
    check("set_state on a quota error: no raise", raises(lambda: db.set_state("k", "v"), Exception) == "did not raise")
    check("clear_state on a quota error: no raise", raises(lambda: db.clear_state("k"), Exception) == "did not raise")
    check("get_state on a quota error: None, no raise", db.get_state("k") is None)
    use(client(data=[{"value": "etag-1"}]))
    check("get_state returns the stored value", db.get_state("jobright_etag:Sales") == "etag-1")
    use(client(exc=QUOTA))
    check("finish_run on a quota error: no raise (it runs in a finally)",
          raises(lambda: db.finish_run(9, total_raw=1), Exception) == "did not raise")
    fake = use(client(data=[]))
    db.finish_run(9, total_raw=3, leftover=0)
    upd = fake.calls[0].op("update")
    check("finish_run updates stats with returning=minimal, by id",
          upd[1] == {"returning": ReturnMethod.minimal} and upd[0][0]["total_raw"] == 3
          and "finished_at" in upd[0][0] and fake.calls[0].op("eq") == (("id", 9), {}))
    fake = use(client(data=[]))
    db.finish_run(None, total_raw=1)
    db.finish_run(-1, total_raw=1)
    check("finish_run without a real run id makes no query", fake.calls == [])

    # ── 6. redaction ────────────────────────────────────────────────────────
    section("_redact: no Supabase URL, host or key in any log line")
    with patched(db, SUPABASE_URL="https://abcdefghijkl.supabase.co", SUPABASE_SERVICE_KEY="sb_secret_TESTKEY"):
        red = db._redact("GET https://abcdefghijkl.supabase.co/rest/v1/jobs 500 (abcdefghijkl.supabase.co, "
                         "key sb_secret_TESTKEY)")
        check("URL, host and key are all replaced", "abcdefghijkl" not in red and "sb_secret_TESTKEY" not in red, red)
        leaky = RuntimeError("POST https://abcdefghijkl.supabase.co/rest/v1/jobs refused for sb_secret_TESTKEY")
        with captured_logs() as logs:
            use(client(exc=leaky))
            db.insert_job({"id": "x1", "tier": "APPLY"})
            db.find_known_candidates(JOBS)
            db.get_job_row("x1")
            db.fetch_pending_jobs(1)
            db.update_job_classification("x1", "APPLY", "r")
            db.get_state("k")
            db.set_state("k", "v")
            db.clear_state("k")
            db.finish_run(3, total_raw=0)
        text = logs.text()
        check("every error log line in db.py is redacted",
              "abcdefghijkl" not in text and "sb_secret_TESTKEY" not in text and text.count("***") >= 9,
              f"{text.count('***')} redactions")
    check("with nothing configured, _redact is the identity", db._redact("plain text") == "plain text")
finally:
    db.get_client = _orig_get_client
    db.time = _orig_time


# ── 7. parity with the main pipeline ─────────────────────────────────────────
section("the key code is byte-identical to scraper/db.py")

FUNCS = ("norm_company", "_norm_role_parts", "norm_role", "make_norm_key")
CONSTS = ("_COMPANY_NOISE", "_INTERN_WORDS", "FULL_TIME_KEY_SUFFIX")


def segments(path):
    src = path.read_text(encoding="utf-8")
    out = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCS:
            out[node.name] = ast.get_source_segment(src, node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in CONSTS:
                    out[t.id] = ast.get_source_segment(src, node)
    return out


mine = segments(testkit.HERE / "db.py")
main = segments(testkit.REPO / "scraper" / "db.py")
for name in FUNCS + CONSTS:
    check(f"{name} is byte-identical (docstring and comments included)",
          name in mine and mine.get(name) == main.get(name))
check("load_dedup_index is gone (no full-table download)", not hasattr(db, "load_dedup_index"))
check("no job_norm_key: there are no gh: tracker rows here", not hasattr(db, "job_norm_key"))

section("import constraint: db.py loads against a config with only the two Supabase names")
ALLOWED_IMPORTS = {"re", "logging", "time", "datetime", "typing", "urllib.parse", "postgrest.types", "supabase",
                   "config"}
tree = ast.parse((testkit.HERE / "db.py").read_text(encoding="utf-8"))
imported = set()
for node in tree.body:
    if isinstance(node, ast.Import):
        imported |= {a.name for a in node.names}
    elif isinstance(node, ast.ImportFrom):
        imported.add(node.module)
check("module-level imports are stdlib, supabase, postgrest and config only", imported <= ALLOWED_IMPORTS,
      str(sorted(imported - ALLOWED_IMPORTS)))
config_imports = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "config"]
check("...and config supplies only SUPABASE_URL / SUPABASE_SERVICE_KEY",
      [sorted(a.name for a in n.names) for n in config_imports] == [["SUPABASE_SERVICE_KEY", "SUPABASE_URL"]])

fake_config = types.ModuleType("config")
fake_config.SUPABASE_URL = ""
fake_config.SUPABASE_SERVICE_KEY = ""
real_config = sys.modules.get("config")
sys.modules["config"] = fake_config
try:
    spec = importlib.util.spec_from_file_location("brice_db_isolated", testkit.HERE / "db.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    loaded = mod.make_norm_key("Acme", "Network Engineer") == "acme|network engineer|ft"
except Exception as exc:  # noqa: BLE001
    loaded = False
    print("        ", type(exc).__name__, exc)
finally:
    if real_config is not None:
        sys.modules["config"] = real_config
check("exec of db.py with the minimal config succeeds (as scraper/test_norm_key.py loads it)", loaded)

sys.exit(testkit.finish())
