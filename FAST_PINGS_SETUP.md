# Faster pings: fire the workflows with an external pinger (~5 min setup)

## Why

GitHub runs `schedule:` workflows **best-effort** and defers them heavily. This
is not a guess — it is measured, over 200 runs (2026-08-13 → 08-20):

| Workflow          | Cron asks for | GitHub actually delivers | Miss |
|-------------------|---------------|--------------------------|------|
| `watch_github.yml`| 5 min         | **29.1 min** median      | 5.8x |
| `watch_ats.yml`   | 10 min        | **32.4 min** median      | 3.2x |
| `scrape.yml`      | 20 min        | **40.3 min** median      | 2x   |

Offsetting the cron minutes off the herd (2026-08-18) did **not** help: median
gap was 39.2 min before and 40.3 min after, 72% vs 73% of gaps over 30 minutes.
GitHub defers regardless of which minute you request.

Measured run durations were `watch_github` **0.6 min**, `watch_ats` **3.0 min**,
and `scrape` 1.5-4.8 min. After punctual external dispatches were enabled, a
second bottleneck became visible: ATS and LinkedIn were launched on the same
minute and competed for the old global DB lock. Recent LinkedIn runs repeatedly
exited without searching. The source-lock migration below is therefore part of
the fast-ping setup, not an optional tuning step.

`workflow_dispatch` events don't get that treatment: an API-triggered run starts
within seconds. All workflows declare `workflow_dispatch:`, so
nothing in the repo needs to change — an external free cron service just has to
POST to GitHub on a reliable clock. The `schedule:` blocks stay as a fallback;
the concurrency groups and the in-DB run-lock make an occasional double-fire
harmless.

## Step 1 — create a fine-grained PAT (2 min)

1. GitHub → Settings → Developer settings → **Fine-grained personal access
   tokens** → Generate new token.
2. Name: `job-alert-pinger`. Expiration: 366 days (max) — put the renewal
   date in your calendar; an expired token turns into silent 401s.
3. Repository access: **Only select repositories** → `Skirozik/job-alert-bot`.
4. Permissions → Repository permissions → **Actions: Read and write**.
   Nothing else.
5. Generate, copy the token. It is used only inside the cron service — never
   commit it anywhere.

## Step 2 — create the pinger jobs on cron-job.org (3 min)

Free account at cron-job.org. Create **four cron jobs**, identical except for
URL and schedule. The URL uses the workflow's **filename**, not its display name.

| Workflow          | URL to POST                                                                                        | Schedule       |
|-------------------|----------------------------------------------------------------------------------------------------|----------------|
| GitHub watcher    | `https://api.github.com/repos/Skirozik/job-alert-bot/actions/workflows/watch_github.yml/dispatches` | every **2 min**  |
| ATS watcher       | `https://api.github.com/repos/Skirozik/job-alert-bot/actions/workflows/watch_ats.yml/dispatches`    | every **5 min**  |
| LinkedIn fast path| `https://api.github.com/repos/Skirozik/job-alert-bot/actions/workflows/watch_linkedin.yml/dispatches` | every **5 min** |
| Main scrape       | `https://api.github.com/repos/Skirozik/job-alert-bot/actions/workflows/scrape.yml/dispatches`       | every **15 min** |

These are deliberately *faster than GitHub's own 5-minute schedule floor* on the
fast watchers — an external pinger is the only way to get there. It is safe
because the runs are short: a 0.6-min `watch_github` run cannot stack against a
2-minute ping, and the ATS sweep is parallelized.

For each job:

- Method: **POST**
- Request body: `{"ref":"master"}`  ← the repo's default branch
- Headers:
  - `Authorization: Bearer <YOUR_PAT>`
  - `Accept: application/vnd.github+json`
  - `Content-Type: application/json`
  - `User-Agent: job-alert-pinger` (GitHub's API rejects requests with no UA)
- In the job's notification settings, enable **email on failure** — that is
  what tells you the PAT expired.

A successful dispatch returns **HTTP 204** with an empty body. Use cron-job.org's
"test run" button once per job to confirm before saving.

Prefer Cloudflare? A Workers cron trigger doing the same `fetch()` POST works
identically on the free tier — same URL/headers/body, schedule in `wrangler.toml`.

## Step 3 — verify (1 min)

Repo → Actions tab: new runs should show trigger **workflow_dispatch** and land
within seconds of the pinger's schedule.

    gh run list --workflow=watch_github.yml --limit 20 --json event,createdAt

Most rows should read `workflow_dispatch`. Interleaved `schedule` rows are the
fallback still firing underneath, which is fine.

Expected end-to-end, from a job appearing to the phone buzzing — average ping
wait (interval / 2) + ~0.2 min dispatch + measured run duration:

| Path           | Before   | After        |
|----------------|----------|--------------|
| GitHub tracker | ~30 min  | **~1.8 min** |
| Company ATS    | ~35 min  | **~5.5 min** |
| LinkedIn       | ~43 min  | **~3-4 min typical** |

The fast LinkedIn path is shallow by design. The full 15-minute sweep remains
the coverage backstop for jobs LinkedIn ranks below page zero.

## Notes

- **On the LinkedIn row, be honest:** this halves *our* contribution to the
  delay. LinkedIn's own indexing lag is **not measurable** from stored data,
  because its `posted_at` comes from a date-only `<time datetime>` attribute
  (every value is `T00:00:00`). Any `found_at - posted_at` figure for a
  LinkedIn row measures hours-past-midnight, not delay — do not quote it. ATS
  rows carry real timestamps and are the ones to measure against.
- If LinkedIn 429s tick up in the logs (`rate_limited` in `scrape_runs`), back
  the scrape pinger off to 20 min — punctual-20 still beats drifting-40.
- Apply `migrations/20260822_source_specific_scrape_runs.sql` before enabling
  the LinkedIn fast pinger. Until then the code safely falls back to the legacy
  global lock, which means ATS can still make a LinkedIn pass skip.
- `start_run()`'s check-then-insert is not atomic (`scraper/db.py`). Shared
  workflow concurrency groups serialize the full and fast LinkedIn workflows;
  each other source also has its own workflow concurrency guard.
- Cost: $0. ~1,400 dispatches/day against a 5,000/hour PAT limit, and Actions
  minutes are free on public repos.
- If this repo ever goes **private**, Actions minutes start metering — revisit
  cadence then.
- If the pinger dies entirely, the `schedule:` crons keep everything alive at
  GitHub's best-effort pace — same behavior as before this setup. That is also
  the failure mode of an expired PAT, and it is **silent**, which is why the
  failure emails in Step 2 matter.
