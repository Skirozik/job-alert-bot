"""Deploy files -- the workflow, schema.sql, .env.brice.example and .gitignore -- checked
against the code that depends on them, plus this fork's entry in the two parity tests.

Offline and dependency-free: plain-text parsing, and git only read-only (check-ignore,
ls-files) when it is installed. What each group protects:

  * workflow: the schedule, a concurrency group no other workflow shares, a 60-minute
    timeout that the 75-minute run-lock outlasts, secrets mapped so job pings go to
    Brice's topic and infrastructure alerts to the owner's, and the profile file the
    classifier reads;
  * schema.sql: every column the dashboard selects and every column db.py reads or
    writes exists; scrape_runs' stat columns equal main.FINISH_RUN_KEYS (an unknown key
    fails finish_run and holds the run-lock for 75 minutes); tier is unconstrained text,
    so the PENDING queue state stores; RLS on every table; no existence guards (running
    it in the wrong project must fail);
  * .env.brice.example names exactly the variables config.py reads, placeholders only;
  * .gitignore keeps the rubric and .env.brice out of this public repo;
  * DEPLOY.md names the secrets the workflow reads, the dashboard variables
    web/lib/personas.ts discovers, every owner alert main.py can send, and only
    command-line flags main.py accepts.

The LinkedIn settings, Python 3.12 grammar, LF endings inside scraper_brice/ and the
requirements.txt parity that SPEC section 12.10 also lists are checked in test_config.py.

Run:  cd scraper_brice && python -X utf8 test_deploy_files.py
"""

import testkit

testkit.block_network()

import logging  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

os.environ.pop("CANDIDATE_PROFILE_PATH", None)     # CI never sets it: check the default location

import config  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
from testkit import HERE, REPO, check, client, patched, section  # noqa: E402

WORKFLOWS = REPO / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "scrape_brice.yml"
SCHEMA = HERE / "schema.sql"
ENV_EXAMPLE = REPO / ".env.brice.example"
CRON_RE = re.compile(r"^\s*-\s*cron:\s*'([^']+)'", re.M)


def read(path) -> str:
    return path.read_text(encoding="utf-8")


def cron_field(spec: str, lo: int, hi: int) -> set:
    """Expand one cron field ('*', '5', '1-23/2', '*/2', '4,14,24') to its values."""
    out = set()
    for part in spec.split(","):
        rng, _, step = part.partition("/")
        step = int(step or 1)
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a, b = (int(x) for x in rng.split("-"))
        else:
            a = int(rng)
            b = hi if step > 1 else a
        out.update(range(a, b + 1, step))
    return out


def fire_times(cron: str) -> set:
    """(hour, minute) pairs a schedule fires at. Every workflow here leaves the day fields '*'."""
    minute, hour = cron.split()[:2]
    return {(h, m) for h in cron_field(hour, 0, 23) for m in cron_field(minute, 0, 59)}


def steps(text: str) -> dict:
    """Step name -> the step's text, for the job's '- name:' steps (six-space indent)."""
    out = {}
    for part in re.split(r"^      - name: ", text, flags=re.M)[1:]:
        name, _, body = part.partition("\n")
        out[name.strip()] = body
    return out


# ── the workflow ─────────────────────────────────────────────────────────────

section("workflow: .github/workflows/scrape_brice.yml")
wf = read(WORKFLOW)
check("named 'Job Scraper (Brice)'", wf.splitlines()[0] == "name: Job Scraper (Brice)")

crons = CRON_RE.findall(wf)
check("one schedule, '40 1-23/2 * * *'", crons == ["40 1-23/2 * * *"], str(crons))
mine = fire_times(crons[0]) if crons else set()
check("...12 runs a day, odd hours at :40", mine == {(h, 40) for h in range(1, 24, 2)}, str(sorted(mine)))
clashes = []
for path in sorted(WORKFLOWS.glob("*.yml")):
    if path.name != WORKFLOW.name and any(fire_times(c) & mine for c in CRON_RE.findall(read(path))):
        clashes.append(path.name)
check("...at no hour and minute any other workflow is scheduled for", not clashes, str(clashes))
check("manual runs allowed (workflow_dispatch)", re.search(r"^\s+workflow_dispatch:\s*$", wf, re.M) is not None)

groups = {p.name: re.findall(r"^\s*group:\s*(\S+)\s*$", read(p), re.M) for p in WORKFLOWS.glob("*.yml")}
check("concurrency group scrape-brice", groups.get(WORKFLOW.name) == ["scrape-brice"], str(groups.get(WORKFLOW.name)))
sharing = sorted(n for n, g in groups.items() if n != WORKFLOW.name and "scrape-brice" in g)
check("...that no other workflow uses (groups are repo-wide)", not sharing, str(sharing))
check("...and a queued run never cancels the one in progress",
      re.search(r"^\s*cancel-in-progress:\s*false\s*$", wf, re.M) is not None)

m = re.search(r"^\s*timeout-minutes:\s*(\d+)\s*$", wf, re.M)
timeout = int(m.group(1)) if m else None
check("timeout-minutes: 60", timeout == 60, str(timeout))
check(f"db.RUN_LOCK_MINUTES ({db.RUN_LOCK_MINUTES}) outlasts it, so a long run keeps its lock until it ends",
      timeout is not None and db.RUN_LOCK_MINUTES > timeout)
check("main.py stops starting work before the timeout (config.RUN_TIME_BUDGET_S)",
      timeout is not None and config.RUN_TIME_BUDGET_S < timeout * 60)
check("...and the search budget fits inside the run budget", config.SEARCH_TIME_BUDGET_S < config.RUN_TIME_BUDGET_S)

st = steps(wf)
check("steps: checkout, Python 3.12, install, profile, rubric check, run, owner alert",
      list(st) == ["Checkout", "Set up Python 3.12", "Install dependencies", "Materialize candidate profile",
                   "Check the rubric's structure", "Run scraper", "Alert the owner if the scraper did not run"],
      str(list(st)))
check("Python 3.12 (the grammar test_config.py parses every module against)",
      "python-version: '3.12'" in st.get("Set up Python 3.12", ""))
REQ = "scraper_brice/requirements.txt"
check("pip cache and install both use scraper_brice/requirements.txt",
      f"cache-dependency-path: {REQ}" in st.get("Set up Python 3.12", "")
      and f"pip install -r {REQ}" in st.get("Install dependencies", "") and (REPO / REQ).is_file())

profile_step = st.get("Materialize candidate profile", "")
profile_name = config.CANDIDATE_PROFILE_PATH.name
check("the rubric comes from secrets.BRICE_PROFILE_MD, passed through env:",
      re.search(r"^\s+PROFILE:\s*\$\{\{\s*secrets\.BRICE_PROFILE_MD\s*\}\}\s*$", profile_step, re.M) is not None)
check(f"...written to {profile_name}, the file config.CANDIDATE_PROFILE_PATH names",
      f"printf '%s' \"$PROFILE\" > {profile_name}" in profile_step)
check("...at the repo root, where config looks by default (the step has no working-directory)",
      "working-directory" not in profile_step
      and config.CANDIDATE_PROFILE_PATH.parent.resolve() == REPO.resolve())
check("...and the job fails when the secret is empty (test -s ... || exit 1)",
      f"test -s {profile_name}" in profile_step and "exit 1" in profile_step)
check("...without printing the rubric (the log shows its byte count only)",
      profile_step.count("$PROFILE") == 1 and not re.search(r"\b(?:cat|head|tail|less|more)\b", profile_step))

run_step = st.get("Run scraper", "")
check("runs from scraper_brice/", re.search(r"^\s+working-directory:\s*scraper_brice\s*$", run_step, re.M) is not None)
check("runs 'python main.py' -- a real run, no --dry-run",
      re.search(r"^\s+run:\s*python main\.py\s*$", run_step, re.M) is not None)
env_map = dict(re.findall(r"^\s+([A-Z_]+):\s*\$\{\{\s*secrets\.([A-Z_]+)\s*\}\}\s*$", run_step, re.M))
check("env: Brice's database, the shared Claude key, pings to Brice's topic, alerts to the owner's",
      env_map == {"SUPABASE_URL": "SUPABASE_URL_BRICE", "SUPABASE_SERVICE_KEY": "SUPABASE_SERVICE_KEY_BRICE",
                  "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY", "NTFY_TOPIC": "NTFY_TOPIC_BRICE",
                  "OWNER_NTFY_TOPIC": "NTFY_TOPIC"}, str(env_map))
secrets_used = set(re.findall(r"secrets\.([A-Z0-9_]+)", wf))
check("the workflow reads only Brice's four secrets and the two shared ones (no other persona's)",
      secrets_used == {"BRICE_PROFILE_MD", "SUPABASE_URL_BRICE", "SUPABASE_SERVICE_KEY_BRICE", "NTFY_TOPIC_BRICE",
                       "ANTHROPIC_API_KEY", "NTFY_TOPIC"}, str(sorted(secrets_used)))
config_env = set(re.findall(r'os\.environ\.get\(\s*"([A-Z_]+)"', read(HERE / "config.py")))
check("it sets every variable config.py reads, except the local-only CANDIDATE_PROFILE_PATH",
      set(env_map) == config_env - {"CANDIDATE_PROFILE_PATH"} == set(testkit.SECRET_ENV), str(sorted(config_env)))
readers = sorted(p.name for p in HERE.glob("*.py")
                 if not p.name.startswith("test") and p.name not in ("config.py", "run_tests.py")
                 and re.search(r"os\.environ|os\.getenv", read(p)))
check("...and no module but config.py reads the environment (so that list is complete)", not readers, str(readers))

rubric_step = st.get("Check the rubric's structure", "")
check("the rubric's structure is checked before the scraper runs: test_rubric_contract.py from scraper_brice/",
      re.search(r"^\s+working-directory:\s*scraper_brice\s*$", rubric_step, re.M) is not None
      and re.search(r"^\s+run:\s*python -X utf8 test_rubric_contract\.py\s*$", rubric_step, re.M) is not None)
check(f"...against the materialized ../{profile_name}, with no secret in reach",
      re.search(rf"^\s+CANDIDATE_PROFILE_PATH:\s*\.\./{re.escape(profile_name)}\s*$", rubric_step, re.M) is not None
      and "secrets." not in rubric_step)
check("...a check that prints labels only (its own docstring's promise; the Actions log is public)",
      "It prints check labels and counts, NEVER rubric text." in read(HERE / "test_rubric_contract.py"))
check("the scraper step has id 'scraper' (the alert step reads its outcome)",
      re.search(r"^\s+id:\s*scraper\s*$", run_step, re.M) is not None)
alert_step = st.get("Alert the owner if the scraper did not run", "")
check("the last step alerts when the scraper was skipped or cancelled (main.py alerts on its own failures)",
      "if: ${{ always() && steps.scraper.outcome != 'success' && steps.scraper.outcome != 'failure' }}" in alert_step)
alert_env = dict(re.findall(r"^\s+([A-Z_]+):\s*\$\{\{\s*secrets\.([A-Z_]+)\s*\}\}\s*$", alert_step, re.M))
alert_run = alert_step.split("run: |", 1)[-1]
check("...to the owner's topic only, passed through env:, never Brice's",
      alert_env == {"OWNER_NTFY_TOPIC": "NTFY_TOPIC"} and "NTFY_TOPIC_BRICE" not in alert_step, str(alert_env))
check("...with no ${{ }} inside the script, and curl's reply (it echoes the topic) sent to /dev/null",
      "${{" not in alert_run and '"https://ntfy.sh/$OWNER_NTFY_TOPIC" > /dev/null' in alert_run)


# ── schema.sql ───────────────────────────────────────────────────────────────

section("schema.sql: the tables, as the dashboard and the pipeline use them")
raw = read(SCHEMA)
sql = "\n".join(line.split("--", 1)[0] for line in raw.splitlines())      # comments stripped


def parse_tables(text: str) -> dict:
    """table -> {column: definition}, in declaration order."""
    tables = {}
    for name, body in re.findall(r"create\s+table\s+(\w+)\s*\(([\s\S]*?)\);", text, re.I):
        cols = {}
        for line in body.splitlines():
            mm = re.match(r"^\s+([a-z_]+)\s+(.*?),?\s*$", line)
            if mm:
                cols[mm.group(1)] = mm.group(2).strip()
        tables[name.lower()] = cols
    return tables


tables = parse_tables(sql)
jobs = tables.get("jobs", {})
runs = tables.get("scrape_runs", {})
check("three tables: jobs, scrape_runs, bot_state", list(tables) == ["jobs", "scrape_runs", "bot_state"],
      str(list(tables)))
hassan_jobs = parse_tables("\n".join(l.split("--", 1)[0] for l in read(REPO / "scraper_hassan" / "schema.sql")
                                     .splitlines())).get("jobs", {})
check("jobs has the same 17 columns and types as scraper_hassan/schema.sql", len(jobs) == 17 and jobs == hassan_jobs,
      f"{len(jobs)} columns; differs: {sorted(set(jobs.items()) ^ set(hassan_jobs.items()))}")

first = re.search(r"create table jobs", raw, re.I)
line_start = raw.rfind("\n", 0, first.start()) + 1 if first else 0
check("the first 'create table jobs' is the statement, not a comment",
      first is not None and not raw[line_start:first.start()].lstrip().startswith("--"))
mm = re.search(r"create table jobs\s*\(([\s\S]*?)\);", raw, re.I)
node_view = {x.group(1) for line in (mm.group(1).splitlines() if mm else []) if (x := re.match(r"^\s+([a-z_]+)\s", line))}
check("selectCols.test.mjs's own parser finds those 17 columns (it silently skips a file it cannot parse)",
      node_view == set(jobs), str(sorted(node_view ^ set(jobs))))

for rel in ("web/app/page.tsx", "web/app/api/jobs/updates/route.ts"):
    found = re.search(r"const COLS_BASE = '([^']+)'", read(REPO / rel))
    wanted = found.group(1).split(",") if found else []
    missing = [c for c in wanted if c not in jobs]
    check(f"every COLS_BASE column in {rel} exists (one missing column 400s and empties the dashboard)",
          bool(wanted) and not missing, f"missing: {missing}")
check("...as do the columns the dashboard filters, orders and writes on (status, tier, found_at, id, norm_key)",
      {"status", "tier", "found_at", "id", "norm_key"} <= set(jobs))

page = read(REPO / "web" / "app" / "page.tsx")
updates_route = read(REPO / "web" / "app" / "api" / "jobs" / "updates" / "route.ts")
status_route = read(REPO / "web" / "app" / "api" / "jobs" / "[id]" / "status" / "route.ts")
resume_route = read(REPO / "web" / "app" / "api" / "jobs" / "[id]" / "resume" / "route.ts")
check("no suggested_resume: both job queries retry with COLS_BASE after the 400",
      "suggested_resume" not in jobs and "query.replace(',suggested_resume', '')" in page
      and re.search(r"fetchUpdates\([^)]*COLS_BASE\)", updates_route) is not None)
check("no target_key: the status route keeps the clicked rows when its sibling read fails",
      "target_key" not in jobs and "if (!seedRes.ok) return fail(" in status_route)
check("no set_job_group_status function: status clicks fall back to a verified PATCH (PGRST202)",
      "PGRST202" in status_route and "legacyVerifiedUpdate(url, key, ids, status)" in status_route)
check("no resume_builds table: the resume drawer treats the 404 as 'not migrated'",
      "if (res.status === 404) return NextResponse.json({ build: null, migrated: false })" in resume_route)

check("status defaults to 'new' (insert_job never sends it; To apply lists status=eq.new)",
      jobs.get("status") == "text default 'new'", str(jobs.get("status")))
check("found_at defaults to now() (insert_job never sends it; the dashboard orders by it)",
      jobs.get("found_at") == "timestamptz default now()", str(jobs.get("found_at")))
check("id is the primary key (insert_job upserts on_conflict=id)", jobs.get("id") == "text primary key")
check("tier is plain text, so PENDING (the retry-queue state) stores beside the three verdicts",
      jobs.get("tier") == "text", str(jobs.get("tier")))
check("...no CHECK constraint, enum type or domain anywhere",
      not re.search(r"\bcheck\b|\bcreate\s+(?:type|domain)\b", sql, re.I))
check("no 'if not exists': running it in the wrong project fails instead of half-applying",
      not re.search(r"\bif\s+not\s+exists\b", sql, re.I))

indexes = {name.lower(): (table.lower(), " ".join(cols.split()), where.strip())
           for name, table, cols, where in re.findall(
               r"create\s+index\s+(\w+)\s+on\s+(\w+)\s*\(([^)]*)\)\s*(where[^;]*)?;", sql, re.I)}
check("norm_key and tier indexed (find_known_candidates' IN lookups; the PENDING queue)",
      indexes.get("jobs_norm_key_idx", ("",))[:2] == ("jobs", "norm_key")
      and indexes.get("jobs_tier_idx", ("",))[:2] == ("jobs", "tier"), str(indexes))
check("the dashboard's two hot-filter indexes (SITE_SPEED_PROMPT.md step 4)",
      indexes.get("jobs_status_found_idx") == ("jobs", "status, found_at desc", "")
      and indexes.get("jobs_new_tier_found_idx") == ("jobs", "tier, found_at desc", "where status = 'new'"),
      str(indexes))

rls = [t.lower() for t in re.findall(r"alter\s+table\s+(\w+)\s+enable\s+row\s+level\s+security\s*;", sql, re.I)]
check("RLS enabled on all three tables", sorted(rls) == sorted(tables), str(rls))
check("...with no policy or grant, so the publishable key reads nothing",
      not re.search(r"\bcreate\s+policy\b|\bgrant\b", sql, re.I))

stat_cols = [c for c in runs if c not in ("id", "started_at", "finished_at")]
check("scrape_runs' stat columns are exactly main.FINISH_RUN_KEYS",
      sorted(stat_cols) == sorted(main.FINISH_RUN_KEYS) and len(set(main.FINISH_RUN_KEYS)) == len(main.FINISH_RUN_KEYS),
      f"{stat_cols} vs {list(main.FINISH_RUN_KEYS)}")
check("...and main.finish_stats() hands finish_run exactly those keys",
      sorted(main.finish_stats(main.RunState())) == sorted(stat_cols))
check("scrape_runs.started_at is not null default now(); finished_at is nullable (the run-lock reads both)",
      runs.get("started_at") == "timestamptz not null default now()" and runs.get("finished_at") == "timestamptz")
check("bot_state: key (primary key: set_state upserts on_conflict=key) and value",
      tables.get("bot_state") == {"key": "text primary key", "value": "text"}, str(tables.get("bot_state")))


# ── every column db.py touches ───────────────────────────────────────────────

section("every column db.py reads or writes is declared in schema.sql")
FILTER_OPS = {"eq", "neq", "in_", "gt", "gte", "lt", "lte", "is_", "like", "ilike", "order", "contains"}


def touched_columns(call) -> set:
    cols = set()
    for name, args, kwargs in call.ops:
        if name == "select" and args:
            cols.update(c.strip() for c in str(args[0]).split(",") if c.strip() not in ("", "*"))
        elif name in FILTER_OPS and args:
            cols.add(str(args[0]))
        elif name in ("insert", "upsert", "update") and args:
            for row in args[0] if isinstance(args[0], list) else [args[0]]:
                cols.update(row)
            if kwargs.get("on_conflict"):
                cols.update(c.strip() for c in kwargs["on_conflict"].split(","))
    return cols


def answer(call):
    return [{"id": 1}] if call.table == "scrape_runs" and "insert" in call.names else []


fake = client(responder=answer)
job = {"id": "ats:0123456789abcdef", "title": "Associate Network Engineer", "company": "Acme Networks",
       "location": "Remote - US", "url": "https://example.com/jobs/1", "search_term": "ats:Acme Networks",
       "description": "A description.", "logo_url": None, "tier": "APPLY", "reason": "fit", "posted_at": None,
       "apply_url": None, "is_easy_apply": False, "salary": None}
logging.disable(logging.INFO)
try:
    with patched(db, get_client=lambda: fake):
        run_id = db.start_run()
        db.find_known_candidates([job])
        db.get_job_row(job["id"])
        db.insert_job(job)
        db.fetch_pending_jobs(5)
        db.count_pending_jobs()
        db.update_job_classification(job["id"], "APPLY", "fit", salary="$90,000/yr")
        db.get_state("alert_at:linkedin_zero")
        db.set_state("jobright_etag:Sales", 'W/"abc"')
        db.clear_state("jobright_etag:Sales")
        db.finish_run(run_id, **main.finish_stats(main.RunState()))
finally:
    logging.disable(logging.NOTSET)

undeclared = []
for call in fake.calls:
    if call.table not in tables:
        undeclared.append(f"table {call.table}")
        continue
    undeclared += [f"{call.table}.{c}" for c in sorted(touched_columns(call) - set(tables[call.table]))]
check("the calls reached all three tables", {c.table for c in fake.calls} == set(tables),
      str(sorted({c.table for c in fake.calls})))
check(f"all {len(fake.calls)} queries name only declared tables and columns", not undeclared, ", ".join(undeclared))
finish_call = next((c for c in fake.calls if c.table == "scrape_runs" and "update" in c.names), None)
check("finish_run writes finished_at plus the FINISH_RUN_KEYS stats",
      finish_call is not None
      and sorted(finish_call.op("update")[0][0]) == sorted(["finished_at", *main.FINISH_RUN_KEYS]))


# ── .env.brice.example ───────────────────────────────────────────────────────

section(".env.brice.example (repo root)")
example = read(ENV_EXAMPLE)
assigned = dict(re.findall(r"^([A-Z_]+)=(.*)$", example, re.M))
check("sets exactly the five variables config.py reads from the environment",
      set(assigned) == set(testkit.SECRET_ENV), str(sorted(assigned)))
check("CANDIDATE_PROFILE_PATH appears only as a commented-out option",
      "CANDIDATE_PROFILE_PATH" not in assigned
      and re.search(r"^# CANDIDATE_PROFILE_PATH=", example, re.M) is not None)
placeholder = re.compile(r"x{8,}|your-|\.\.\.$|changeme")
not_placeholder = sorted(k for k, v in assigned.items() if not placeholder.search(v.strip()))
check("every value is a placeholder, never a real credential (names shown, never values)",
      not not_placeholder, str(not_placeholder))
check("it is copied to .env.brice, the file main.py loads",
      "Copy this file to .env.brice" in example and '".env.brice"' in read(HERE / "main.py"))


# ── .gitignore ───────────────────────────────────────────────────────────────

section(".gitignore: nothing personal reaches this public repo")
ignore_lines = [line.strip() for line in read(REPO / ".gitignore").splitlines()]
check("lists Brice_Candidate_Profile_and_Filters.md and .env.brice",
      profile_name in ignore_lines and ".env.brice" in ignore_lines)
git = shutil.which("git")
if git:
    def ignored(path: str) -> bool:
        return subprocess.run([git, "-C", str(REPO), "check-ignore", "-q", "--no-index", path],
                              capture_output=True).returncode == 0

    check(f"git ignores {profile_name}, the file the workflow writes", ignored(profile_name))
    check("git ignores .env.brice", ignored(".env.brice"))
    drafts = [f"{profile_name}.bak", "Brice_Candidate_Profile_and_Filters.txt", "scraper_brice/private_fixtures.json",
              "brice_private/brief.md", "rubric_r2_snapshot.md", f"scraper_brice/{profile_name}"]
    check("...and the rubric's drafts, copies and private fixtures", all(ignored(d) for d in drafts),
          str([d for d in drafts if not ignored(d)]))
    check("...but not the main pipeline's tracked Candidate_Profile_and_Filters.md",
          not ignored("Candidate_Profile_and_Filters.md"))
    tracked = subprocess.run([git, "-C", str(REPO), "ls-files", "--", profile_name, ".env.brice"],
                             capture_output=True, text=True).stdout.split()
    check("...and neither file is tracked", not tracked, str(tracked))
else:
    print("  NOTE  git not found: the ignore rules were checked by line only")


# ── parity tests and line endings ────────────────────────────────────────────

section("the parity tests cover this fork")
m = re.search(r"for name in \(([^)]*)\):", read(REPO / "scraper" / "test_norm_key.py"))
check("scraper/test_norm_key.py compares scraper_brice/db.py's key code with main's",
      m is not None and '"scraper_brice"' in m.group(1))
m = re.search(r"const schemas = \[([^\]]*)\]", read(REPO / "web" / "lib" / "__tests__" / "selectCols.test.mjs"))
check("web/lib/__tests__/selectCols.test.mjs checks scraper_brice/schema.sql",
      m is not None and "'scraper_brice/schema.sql'" in m.group(1))

section("repo-level files are LF")
crlf = [p.name for p in (WORKFLOW, ENV_EXAMPLE) if b"\r" in p.read_bytes()]
check("the workflow and .env.brice.example have no CR", not crlf, str(crlf))


# ── DEPLOY.md and the dashboard example stay in step with the code ───────────

section("DEPLOY.md matches the code it documents")
deploy = read(HERE / "DEPLOY.md")
unnamed = sorted(s for s in secrets_used if s not in deploy)
check("it names every secret the workflow reads", not unnamed, str(unnamed))
persona_vars = set(re.findall(r"PERSONA_\$\{upper\}_([A-Z_]+)", read(REPO / "web" / "lib" / "personas.ts")))
check("web/lib/personas.ts still discovers PASSWORD / SUPABASE_URL / SERVICE_KEY / LABEL / USERNAME",
      persona_vars == {"PASSWORD", "SUPABASE_URL", "SERVICE_KEY", "LABEL", "USERNAME"}, str(sorted(persona_vars)))
check("...and DEPLOY.md names each as PERSONA_BRICE_<var>",
      all(f"PERSONA_BRICE_{v}" in deploy for v in persona_vars))
main_src = read(HERE / "main.py")
alert_titles = sorted(set(re.findall(r'title=f?"(Brice: [^"]+)"', main_src)))
undocumented = [t for t in alert_titles
                if not re.search("<[^>]+>".join(re.escape(p) for p in re.split(r"\{[^}]*\}", t)), deploy)]
check(f"it documents all {len(alert_titles)} owner-alert titles main.py can send", len(alert_titles) >= 10 and
      not undocumented, str(undocumented))
cli_flags = set(re.findall(r'add_argument\(\s*"(--[a-z-]+)"', main_src))
unknown_flags = sorted(set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", deploy)) - cli_flags)
check("every --flag it mentions is one main.py accepts", "--dry-run" in cli_flags and not unknown_flags,
      str(unknown_flags))
sql_rules = set(re.findall(r"reason = 'Pre-filtered: ([^']+)'", deploy))
gate_rules = set(re.findall(r'return "([^"]+)"', read(HERE / "title_gate.py")))
check("its pre-filter SQL example names a real title_gate rule", sql_rules and sql_rules <= gate_rules,
      str(sorted(sql_rules - gate_rules)))

section("web/.env.local.example has a placeholder block for this persona")
web_example = read(REPO / "web" / ".env.local.example")
block = dict(re.findall(r"^(PERSONA_BRICE_[A-Z_]+)=(.*)$", web_example, re.M))
check("LABEL, PASSWORD, SUPABASE_URL and SERVICE_KEY, with placeholder values only",
      block == {"PERSONA_BRICE_LABEL": "Brice", "PERSONA_BRICE_PASSWORD": "",
                "PERSONA_BRICE_SUPABASE_URL": "https://xxxx.supabase.co",
                "PERSONA_BRICE_SERVICE_KEY": "sb_secret_..."}, str(sorted(block)))
check("...and the PERSONA_IDS example lists brice", "# PERSONA_IDS=owner,beyonce,hassan,brice" in web_example)

sys.exit(testkit.finish())
