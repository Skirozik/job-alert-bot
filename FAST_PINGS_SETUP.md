# Faster pings: fire the workflows with an external pinger (~5 min setup)

## Why

GitHub runs `schedule:` workflows **best-effort** and sheds load hardest at
busy minutes. This repo has measured the damage directly: runs meant to be 20
minutes apart landed **50–70 minutes apart** (2026-08-07, documented in
`scraper/config.py`), and 2026-08-06 had a ~6-hour hole with queued runs
cancelled unrun. Whatever the crons say, the *actual* cadence — and therefore
the ping time — is at GitHub's mercy.

`workflow_dispatch` events don't get that treatment: an API-triggered run
starts near-immediately. All three workflows already declare
`workflow_dispatch:`, so nothing in the repo needs to change — an external
free cron service just has to POST to GitHub on a reliable clock. The
`schedule:` blocks stay as a fallback; the concurrency groups and the in-DB
run-lock already make an occasional double-fire harmless.

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

Free account at cron-job.org (60 requests/hour is far more than needed).
Create **three cron jobs**, identical except for URL and schedule:

| Workflow          | URL to POST                                                                                     | Schedule            |
|-------------------|--------------------------------------------------------------------------------------------------|---------------------|
| Main scrape       | `https://api.github.com/repos/Skirozik/job-alert-bot/actions/workflows/scrape.yml/dispatches`      | every 15 min (`5,20,35,50`) |
| GitHub watcher    | `https://api.github.com/repos/Skirozik/job-alert-bot/actions/workflows/watch_github.yml/dispatches`| every 5 min         |
| ATS watcher       | `https://api.github.com/repos/Skirozik/job-alert-bot/actions/workflows/watch_ats.yml/dispatches`   | every 10 min (`2,12,22,32,42,52`) |

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

Repo → Actions tab: new runs should show trigger **workflow_dispatch** and
land within seconds of the pinger's schedule. After a day, eyeball the
spacing of "Job Scraper" runs — it should be a steady 15 minutes instead of
the 20-nominal / 50-real drift.

## Notes

- The main scrape moves from 20-minute-nominal to a *real* 15 minutes. If
  LinkedIn 429s ever tick up in the logs (`rate_limited` in `scrape_runs`),
  back the scrape pinger off to 20 — punctual-20 is still far better than
  drifting-50.
- Cost: $0. The API calls are ~150/day against a 5,000/hour PAT limit, and
  Actions minutes are free on public repos.
- If this repo ever goes **private**, Actions minutes start metering — revisit
  cadence then.
- If the pinger dies entirely, the offset `schedule:` crons keep everything
  alive at GitHub's best-effort pace — same behavior as before this setup.
