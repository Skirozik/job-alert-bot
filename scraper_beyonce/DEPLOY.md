# Deploying the Beyonce pipeline

Pre-flight audit run 2026-07-28 against the live repo, the authenticated Modal
workspace, and live LinkedIn. Six parallel auditors produced ~20 candidate
findings; each was then put to two independent skeptics (a correctness lens and
a does-it-actually-block-a-deploy lens) and only findings that survived both are
recorded here. 15 candidate findings were refuted and are deliberately absent.

Numbers below marked as measured come from real runs performed during the audit,
not estimates.

---

## Blockers to fix before deploying

**1. Modal secret `job-alert-secrets-beyonce` does not exist — the deploy hard-fails**

`modal_app_beyonce.py:29` declares `secrets=[modal.Secret.from_name("job-alert-secrets-beyonce")]`. Modal hydrates a function's secrets at deploy time (they are the first entry in the function's dependency list; the `FunctionCreate` request needs `secret.object_id`), so this is resolved before anything is published. The workspace contains exactly one secret, `job-alert-secrets` (the live pipeline's). Running `modal deploy modal_app_beyonce.py` today exits with `modal.exception.NotFoundError: Secret 'job-alert-secrets-beyonce' not found in environment 'main'`; no app is created, no schedule is registered. This was reproduced directly, with the existing `job-alert-secrets` hydrating fine as a control.

Fix: create the secret with all four keys **non-empty**, before deploying — see Deployment steps §b3. Do **not** use `--from-dotenv .env.beyonce`: that command succeeds but produces a secret where `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` and `NTFY_TOPIC` are empty strings, because that is their current state in the file. That variant deploys successfully and then fails on every 2-hour tick: `scraper_beyonce/db.py:155` calls `get_client()` *outside* `insert_job`'s try block, so `RuntimeError: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set` escapes `process_job` → `run()` (whose `try`/`finally` at main.py:188/298 has no `except`) and kills the container — after the full LinkedIn crawl and at least one paid Claude call, with no ntfy alert, since `push_canary` only fires on the `total_raw == 0` path.

Never point this app at `job-alert-secrets`: `scraper_beyonce/db.py` writes to tables literally named `jobs` / `scrape_runs`, so reusing the live secret would write Atlanta admin rows into the live SWE-internship project and push to the original persona's ntfy topic.

**2. `Beyonce_Candidate_Profile_and_Filters.md` is a third party's PII and the GitHub remote is PUBLIC**

`gh repo view` reports `{"isPrivate": false, "visibility": "PUBLIC"}` for `github.com/Skirozik/job-alert-bot`. The file is untracked and **not** gitignored (`.gitignore` covers `.env`, `.env.beyonce`, `__pycache__/`, `*.pyc`, `*.pyo`, `.python-version`, `.venv/`, `venv/`, `node_modules/`, `.next/`, `*.log`, `*.tsbuildinfo`), so the `git add .` the README prescribes would stage it. Lines 7-13 name a real private individual plus school + degree + expected graduation, certification + institution + date, current employer + start date annotated "actively trying to leave", prior employer, metro, and pay floor. Pushing that to a public repo is irreversible (history, forks, caches) and discloses to her named current employer that she is job-hunting. No prior commit of any Beyonce file exists, so the exposure would be new.

This does not block `modal deploy` itself — `add_local_file` (modal_app_beyonce.py:14-17) reads from the working tree and uploads into a private image. It blocks shipping the fileset. Fix, before any `git add`/`git push`:

```
# from repo root — appends one line, does not touch tracked content
Add-Content -Path .gitignore -Value "Beyonce_Candidate_Profile_and_Filters.md" -Encoding utf8
```

The file must stay on disk at the repo root for the deploy to work; gitignoring it costs nothing. Alternative: make the repo private (`gh repo edit --visibility private`), or strip the identifying block to a role-shaped persona with no real name, employer, or school.

## Fix-or-accept before going live

**No `timeout=` on the scheduled function — Modal's 300s default applies (`modal_app_beyonce.py:23-30`, default at `modal/app.py:799`).**

Tradeoff. Measured cold-start runs (empty dedup index, live LinkedIn, real Haiku calls) landed at **219-262s against the 300s ceiling** — 15-25% headroom. Search phase measured 57-78s across the 11 terms (one sample hit 122s when five terms saturated all five pages); 94-212 raw listings collapsing to **29-33 unique new jobs**; per-job cost ~5.3-6.2s (`fetch_description` 2.75-3.3s, dominated by the unconditional `random.uniform(2.0, 3.5)` sleep at `linkedin.py:146`; `classify` 2.2-2.6s; insert + push ~0.4s). So the workload fits today — the "9x over budget" framing is wrong. But the tail is fat: job volume swings run to run, `classify` was observed at 5.3s against a 2.4s mean, and a rate-limited detail fetch burns ~25s for a single job. At ~45+ new jobs, or a partially 429'd run, 300s is exceeded.

If it is exceeded the degradation is silent: `new_jobs` is in-memory (main.py:182) and persists only inside the loop, the canary at main.py:257 only fires on `total_raw == 0`, and the loss is biased toward the tail search terms (`prior authorization specialist`, `hotel front desk agent`). Already-processed jobs are durably stored, so this is partial delivery, not total failure, and a killed container skipping `finally: finish_run(...)` does not wedge anything — the run-lock's 20-minute stale window (db.py:118) expires long before the 2-hour tick.

Recommendation: **fix**. One line, no downside, removes the entire tail risk:

```python
@app.function(
    schedule=modal.Period(hours=2),
    timeout=1500,                       # 25 min; well under the 2h cron period
    secrets=[modal.Secret.from_name("job-alert-secrets-beyonce")],
)
```

Accepting it is defensible (the live `modal_app.py` omits `timeout` too and works), but only because the live pipeline's 20-minute cadence against a 7200s lookback makes `all_db_duplicate` short-circuit pagination almost every run. This fork's 2-hour cadence against a 9000s lookback gives only ~20% overlap, so it paginates deeper and processes more jobs per run than the live app does.

## Deployment steps

### (a) Steps only you can do — no shell involved

1. **Create the Supabase project.** Go to https://supabase.com/dashboard → **New project**. Name it something like `beyonce-job-alerts`, pick a US East region, save the generated DB password. This must be a **brand new project**, not the original persona's — the two pipelines use identical table names and are isolated only by pointing at different projects.
2. **Apply the schema.** In that project: **SQL Editor** → **New query** → paste the entire contents of `c:/Users/inyan/Claude/LinkedIn_Job_Bot/scraper_beyonce/schema.sql` → **Run**. It creates `jobs`, `scrape_runs`, `bot_state` plus three indexes. Confirm all three appear under **Table Editor**. If you see `ERROR: relation "jobs" already exists` (Postgres 42P07), you are in the wrong project — schema.sql uses bare `create table` with no `if not exists`, so this error is your safety net. Stop and switch projects.
3. **Copy the credentials.** Same project → **Project Settings** → **API**. Copy the **Project URL** (`https://<ref>.supabase.co`) → this is `SUPABASE_URL`. Under **Project API keys**, reveal and copy the **`service_role`** key → this is `SUPABASE_SERVICE_KEY`. Do **not** use the `anon` key; the writes will silently fail RLS.
4. **Choose an ntfy topic.** Pick a new, hard-to-guess string, e.g. `beyonce-atl-jobs-7f3q9k`. It must be different from the original persona's topic. The topic name *is* the password — ntfy has no auth and auto-creates topics on first publish. Install the ntfy app (iOS/Android) or open https://ntfy.sh/<your-topic>, and **subscribe to that exact string** before deploying.
5. **Get the Anthropic key.** https://console.anthropic.com → **API Keys**. Reusing the original persona's key is fine (`.env.beyonce.example:13-15`) — it is an inference credential, not scoped to a search.
6. **Decide on the PII file** (Blocker 2) before running any `git add` / `git push`.

### (b) Shell steps — **all run from repo root `c:/Users/inyan/Claude/LinkedIn_Job_Bot`** unless stated

1. Confirm Modal auth and current secret state:
```
modal profile current
modal secret list
```
2. Optional local sanity check of the classifier — **run from `scraper_beyonce/`**, uses only `ANTHROPIC_API_KEY` from `.env.beyonce`, writes nothing to any DB and sends no pushes:
```
cd scraper_beyonce
python test_classifications.py
cd ..
```
Expect `=== 15/15 passed ===` and exit code 0.

3. Create the Modal secret. **Single line, from repo root.** Replace every placeholder with the real values from §a; the four names below are exactly the set `scraper_beyonce/config.py:36-39` reads — no more, no fewer:
```
modal secret create job-alert-secrets-beyonce SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co SUPABASE_SERVICE_KEY=YOUR_SERVICE_ROLE_KEY ANTHROPIC_API_KEY=sk-ant-api03-YOUR_KEY NTFY_TOPIC=beyonce-atl-jobs-YOUR_SUFFIX
```
Do **not** use `--from-dotenv .env.beyonce` (Blocker 1). If a value contains characters PowerShell parses (`$`, backtick, space), wrap the whole `KEY=value` token in single quotes.

4. Verify the name matches `modal_app_beyonce.py:29` character for character:
```
modal secret list
```
You should now see two rows: `job-alert-secrets` and `job-alert-secrets-beyonce`.

5. Deploy:
```
modal deploy modal_app_beyonce.py
```
Expect the image build (`pip_install_from_requirements` on the 6 packages in `scraper_beyonce/requirements.txt`), then `✓ Created objects` and a deployed app named **`job-alert-scraper-beyonce`**. Confirm it is distinct from the live app:
```
modal app list
```
Both `job-alert-scraper` and `job-alert-scraper-beyonce` should be listed as deployed. The live app is untouched — separate `modal.App`, separate secret, separate deploy command.

6. **First-run smoke test.** This runs the real pipeline once, immediately: real LinkedIn requests, real Claude calls, real Supabase writes, real pushes to your phone.
```
modal run modal_app_beyonce.py
```
7. Watch it, or re-attach later:
```
modal app logs job-alert-scraper-beyonce
```

If the smoke test aborts with `Another run appears to be in progress (started <20 min ago, unfinished)`, a prior run died without clearing its lock — wait out the 20-minute window (db.py:118) or delete the `scrape_runs` row with a NULL `finished_at`.

## How to verify it actually worked

**In the Modal logs** — a healthy cold-start run (empty `jobs` table) looks like:

```
=== Job scraper starting — 11 terms x 1 locations ===
Searching: 'patient access representative' in Atlanta, GA
  p0 (start=0): 10 listings, 9 new
  ...
Total raw: 130 | New: 33 | Rate limited: 0/11
Processing: '<title>' @ <company> [<id>]
  -> APPLY | <one-line reason>
Push sent: 🟢 <company> — <title>
=== Run complete: 33 new jobs, 11 notified ===
```

Measured expectations for that first run: **`Total raw` between roughly 94 and 212**, **`New` between 29 and 33**, **`Rate limited: 0/11`**, wall-clock **~220-260s**. `notified` is a minority of `New` — every job is stored including SKIPs (main.py:27), only APPLY/MAYBE are pushed (main.py:165). Steady-state runs are much smaller: **~5-15 new jobs**, since the 2h cadence against a 2.5h lookback means ~20% of each window is already known.

**In Supabase** (Table Editor of the new project):
- `jobs` → row count equals the run's `New` count; `tier` populated with a mix of `APPLY` / `MAYBE` / `SKIP`; `norm_key`, `url`, `found_at` non-null.
- `scrape_runs` → exactly one row, `finished_at` **not null**, and `total_raw` / `new_jobs` / `notified` / `rate_limited` matching the log line. A NULL `finished_at` means the container died before the `finally` block at main.py:298 — check for the timeout issue above.

**On your phone (ntfy):** one notification per `notified` count. Title `Company - Title`; body starts `🟢 APPLY` (high priority) or `🟡 MAYBE` (default priority), then `Atlanta, GA`, then `Why: <reason>`. Tapping opens the LinkedIn posting via the `Click` header (notifier.py:47).

**Failure signatures to recognize:**

| What you see | What it means |
|---|---|
| `NotFoundError: Secret 'job-alert-secrets-beyonce' not found` at deploy | Blocker 1 — secret not created |
| `RuntimeError: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set` mid-run | Secret created with empty values (the `--from-dotenv` trap) |
| `NTFY_TOPIC not set — skipping push for job ...` repeated per job | `NTFY_TOPIC` empty in the secret; jobs are stored and will never be re-notified |
| Every push body reads `Why: Classifier error — review manually` | `ANTHROPIC_API_KEY` is bad — `classifier.py` fell back to MAYBE for every job, and those rows are now permanently stored as MAYBE |
| `scrape_runs table unavailable (...)` warning then a crash later | Supabase creds wrong; the warning interpolates the real exception text |
| An urgent `Job scraper alert` push saying 0 results across all searches | `total_raw == 0` canary — LinkedIn has blocked the runner IP or changed its API |
| Run ends without `=== Run complete ===` and `finished_at` is NULL | Container hit the 300s timeout mid-loop (see Fix-or-accept) |

Confirm the schedule registered: `modal app list` shows `job-alert-scraper-beyonce` deployed; the next automatic tick lands within 2 hours and appends a second `scrape_runs` row.

## Known limitations once live

- **Notification-only. There is no dashboard for this persona.** `web/` reads a single hard-coded Supabase project (the original persona's) with no persona routing. Your only interfaces are the ntfy pushes and the Supabase Table Editor.
- **No maintenance tooling.** The original pipeline ships `scraper/reclassify_skips.py`, `recheck_apply_maybe.py`, and six `backfill_*.py` scripts. `scraper_beyonce/` has none. A job is classified exactly once — dedup is on `id` **or** `norm_key` (`company|title`), and SKIPs are stored specifically so they are never re-scored. If a run classifies badly (e.g. during an Anthropic outage), fixing it means writing a SQL `UPDATE` by hand; the outage sentinel is the queryable literal `Classifier error — review manually` in the `reason` column.
- **Volume and cost.** 12 runs/day. Cold start ~30 new jobs; steady state ~5-15. Measured against the current profile (Haiku 4.5 at $1.00/$5.00 per 1M tokens, cache reads at 0.1x, cache writes at 1.25x): the cached prefix is **7,271 tokens**, and a steady-state `classify` call bills 593 uncached input + 7,271 cache-read + ~142 output tokens = **~$0.0020 per call**. The first call of each run writes the cache instead and costs ~$0.0104. That works out to roughly **$0.22-$0.47/day, ~$7-14/month**, plus Modal compute of a few minutes/day. The same call with caching disabled would be ~$0.0086, so the `cache_control` directive is now worth about 4x — the opposite of the situation described below when this file was written.
- **Prompt caching now engages.** `classifier.py` sets `cache_control` on the system prompt, and the profile has since grown well past the threshold, so the cache is live: two identical back-to-back calls report `cache_read_input_tokens = 7271` on the second. This file's earlier note predicted the mechanism correctly ("Editing the profile past ~4,096 tokens turns caching on silently") but got the arithmetic wrong — 4,096 is the *documented* Haiku 4.5 minimum, and caching was observed still off above it, with the real floor nearer ~5,100 tokens. **So verify empirically after any profile edit instead of counting tokens:**
  ```bash
  cd scraper_beyonce && python -B -c "import sys,pathlib; sys.path.insert(0,'.');from dotenv import load_dotenv; load_dotenv(pathlib.Path('..')/'.env.beyonce');import classifier as cl;c=cl._get_client();kw=dict(model=cl.MODEL,max_tokens=256,system=cl._system_prompt(),tools=[cl._CLASSIFY_TOOL],tool_choice={'type':'tool','name':'classify_job'});m=[{'role':'user','content':'Classify: Test Intern @ Probe, Summer 2027.'}];u1=c.messages.create(**kw,messages=m).usage; u2=c.messages.create(**kw,messages=m).usage;print('prefix_tokens=',u1.input_tokens+(u1.cache_creation_input_tokens or 0));print('cache_read_2nd_call=',u2.cache_read_input_tokens or 0);print('CACHING_WORKS' if (u2.cache_read_input_tokens or 0)>0 else 'STILL_A_NO_OP')"
  ```
  Two things can silently drop it back to a no-op: the 5-minute ephemeral TTL (a run starting more than 5 minutes after the previous one pays the 1.25x write on its first job — at the 2h cadence that is every run, which is why the per-run cost above assumes one write), and any edit to `Beyonce_Candidate_Profile_and_Filters.md`, since the profile *is* the cached prefix and one changed byte invalidates the entry.
- **Coverage has no catch-up.** `LOOKBACK_SECONDS = 9000` (2.5h) against a 2h cron gives a 30-minute overlap and nothing more. There is no watermark (`bot_state` is created but unused), so a failed or skipped run leaves a ~1.5h hole that no later run queries. Missed postings are recoverable only if the employer reposts or LinkedIn bumps `listedAt`. Note also that `modal.Period` resets its phase on redeploy, so iterating on the deploy can open a wider gap. Raising `LOOKBACK_SECONDS` to ~21600 is a free one-line hardening if you want slack.
- **Alerting is coarse.** The canary fires only when **all 11** searches return zero. A run where 9 of 11 terms are rate-limited completes "successfully" with no alert — visible only in `scrape_runs.rate_limited` and the Modal logs. `scrape_runs.notified` also counts pushes *attempted*, not delivered; an ntfy 5xx is logged at ERROR but not retried.
- **ntfy topics are public.** Anyone who guesses the topic string reads the alerts. That is inherent to ntfy's open-topic model — a wrong or typo'd topic still returns HTTP 200 and logs `Push sent`, so verify on the phone, not in the logs.
- **Search scope is fixed and narrow.** 11 admin/healthcare/hospitality terms, Atlanta only (no nationwide or remote fallback, unlike the original), `f_E="2,3"` entry/associate experience filter (spot-checked: sampled results came back Entry / Associate / Not Applicable, no Mid-Senior). LinkedIn-only — no GitHub-tracker fast path exists for these roles.
- **Deployment isolation is solid.** Distinct Modal app (`job-alert-scraper-beyonce`), distinct secret, distinct Supabase project, distinct ntfy topic, distinct entry file. `modal deploy modal_app.py` and `modal deploy modal_app_beyonce.py` never touch each other, and no GitHub Actions workflow references `scraper_beyonce/`. The one shared-fate risk is operator error at secret-creation time.