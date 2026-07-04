# LinkedIn Internship Job Alert Bot

Scrapes LinkedIn (plus a few GitHub-tracked internship lists) every 20 min, classifies each new internship against your candidate profile using Claude Haiku, and pushes APPLY/MAYBE matches to your phone (ntfy.sh), a twice-daily email digest (Resend), and a password-protected web dashboard. Runs entirely on GitHub Actions — your PC never needs to be on.

---

## How it works

```
GitHub Actions (every 20 min)
  └─ scraper/main.py
       ├─ Run-lock: skip if another scheduler's run is still in progress (scrape_runs table)
       ├─ Load dedup index once (all known job ids + norm_keys, one bulk query)
       ├─ 10 LinkedIn searches (5 terms × 2 locations, f_E=1 internship filter), with
       │   retry/backoff on rate limiting
       ├─ Canary: 0 LinkedIn results across all searches → push "may be blocked" alert
       ├─ Supplementary fetch from tracked GitHub internship-list repos (no rate limiting)
       ├─ Fetch job description for each new LinkedIn listing
       ├─ Claude Haiku classifies: APPLY / MAYBE / SKIP (prompt-cached, structured tool output)
       ├─ Store first, then ntfy.sh push for APPLY and MAYBE (only once stored)
       └─ Record run stats (raw/new/notified/rate-limited counts) in scrape_runs

GitHub Actions (8am & 6pm ET)
  └─ scraper/digest.py → emails a digest of APPLY/MAYBE jobs found since the last digest

web/ (Next.js, deploy anywhere — e.g. Vercel)
  └─ Password-protected dashboard reading the same Supabase table, with live updates
```

---

## Setup (one-time, ~15 minutes)

### 1. Create a Supabase project

1. Go to [supabase.com](https://supabase.com) → New project (free tier)
2. In the SQL Editor, run this schema:

```sql
create table jobs (
  id               text primary key,
  title            text,
  company          text,
  location         text,
  url              text,
  search_term      text,
  description      text,
  logo_url         text,
  norm_key         text,
  tier             text,                    -- APPLY | MAYBE | SKIP
  reason           text,
  suggested_resume text,
  status           text default 'new',      -- new | saved | applied | dismissed
  posted_at        timestamptz,
  found_at         timestamptz default now(),
  apply_url        text,
  is_easy_apply    boolean default false,
  salary           text
);

create index jobs_norm_key_idx on jobs (norm_key);
create index jobs_tier_idx on jobs (tier);
create index jobs_found_at_idx on jobs (found_at desc);
```

3. Also run this — a small run-lock/stats table (guards against two schedulers overlapping, e.g. if you also deploy `modal_app.py`) and a generic key-value state table (used by the email digest to track its last send time). Both are optional: the scraper degrades gracefully without them, just without the lock or digest watermark.

```sql
create table scrape_runs (
  id           bigint generated always as identity primary key,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  total_raw    int,
  new_jobs     int,
  notified     int,
  rate_limited int
);

create table bot_state (
  key   text primary key,
  value text
);
```

4. Go to Settings → API → copy **Project URL**, **service_role** key, and **anon** key (the web dashboard needs the anon key; everything else uses service_role)

### 2. Set up ntfy.sh on your phone

1. Install the [ntfy app](https://ntfy.sh) on iOS or Android
2. Pick a private topic name — something hard to guess, e.g. `ifiok-jobs-x7k2m9`
3. In the app: tap ＋ → subscribe to your topic
4. You'll receive a push whenever a new APPLY or MAYBE job is found

### 3. Get an Anthropic API key

Go to [console.anthropic.com](https://console.anthropic.com) → API Keys → Create key.
The classifier uses `claude-haiku-4-5-20251001` — cost is ~$0.0001 per job classified.

### 4. Get a Resend API key (for the email digest)

Go to [resend.com](https://resend.com) → API Keys. Free tier = 3,000 emails/month.
You'll also need to verify a sender domain (or use their onboarding domain for testing).
Powers `scraper/digest.py`, which runs on its own schedule (`.github/workflows/digest.yml`)
and emails a summary of new APPLY/MAYBE jobs at ~8am and ~6pm ET.

### 5. Push to a private GitHub repo

```bash
cd LinkedIn_Job_Bot
git init
git add .
git commit -m "init job alert bot"
gh repo create job-alert-bot --private --source=. --push
```

### 6. Add GitHub repo secrets

Go to your repo → Settings → Secrets and variables → Actions → New repository secret.
Add each of these:

| Secret | Where to find it |
|--------|-----------------|
| `SUPABASE_URL` | Supabase → Settings → API → Project URL |
| `SUPABASE_SERVICE_KEY` | Supabase → Settings → API → service_role key |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |
| `NTFY_TOPIC` | The topic name you picked in step 2 |
| `RESEND_API_KEY` | resend.com → API Keys (used by the digest workflow) |
| `RESEND_FROM` | Your verified sender email |
| `ALERT_EMAIL` | simeonchere@gmail.com |

These same secrets are read by both `.github/workflows/scrape.yml` and `.github/workflows/digest.yml`.

### 7. Test the scraper locally

```bash
cd LinkedIn_Job_Bot
cp .env.example .env
# Fill in .env with your real values

cd scraper
pip install -r requirements.txt
python main.py
```

You should see logs like:
```
10:32:01 INFO === Job scraper starting — 5 terms × 2 locations ===
10:32:01 INFO Searching: 'software engineer intern' in United States
10:32:04 INFO   Got 12 listings
...
10:32:45 INFO Processing: 'iOS Engineer Intern' @ Acme Corp [3987654321]
10:32:48 INFO   Description: 2847 chars
10:32:49 INFO   → APPLY | Strong iOS/SwiftUI fit, App Store experience directly relevant | Resume: Mobile
10:32:49 INFO   Push sent: 🟢 Acme Corp — iOS Engineer Intern
```

### 8. Enable GitHub Actions

Go to your repo → Actions tab → enable workflows if prompted.
Trigger a test run: Actions → Job Scraper → Run workflow.
The digest workflow (Actions → Job Digest Email) can also be run on demand the same way.

### 9. Deploy the web dashboard (optional)

`web/` is a password-protected Next.js dashboard that reads from the same Supabase table.
Deploy it anywhere that runs Next.js (e.g. [vercel.com](https://vercel.com) → New Project →
set the root directory to `web/`), with these environment variables:

| Variable | Where to find it |
|----------|-----------------|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase → Settings → API → Project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase → Settings → API → anon/public key |
| `SUPABASE_SERVICE_KEY` | Supabase → Settings → API → service_role key |
| `DASHBOARD_PASSWORD` | Any password you choose — gates the whole dashboard |

---

## Testing the canary alert

To verify the zero-results alert works, temporarily edit `config.py` and change `SEARCH_TERMS` to `["xyzzy-no-such-job-12345"]`, run the scraper, and confirm you receive an ⚠️ push. Then revert.

---

## Implemented phases

- **Phase 2 — Email digest**: `scraper/digest.py`, on its own schedule (`.github/workflows/digest.yml`), sends a digest of APPLY/MAYBE jobs found since the last send.
- **Phase 3 — Web dashboard**: `web/` — tier badge, classifier reason, suggested resume, salary, Apply/Save/Dismiss buttons, and real-time updates (including status changes syncing across open tabs/devices).
- **Phase 4 — Additional sources**: `scraper/github_sources.py` pulls recent (≤7 day old) listings from `SimplifyJobs/Summer2026-Internships`, `speedyapply/2026-SWE-College-Jobs`, and `speedyapply/2026-AI-College-Jobs` — no rate limiting, since they're plain README fetches.

## Future upgrades

### Railway (for truly real-time polling, ~5 min intervals)
GitHub Actions cron has up to 60 min drift during peak hours. For faster alerts:
1. Sign up at [railway.app](https://railway.app) (free $5/month credit)
2. Create a new project → Deploy from GitHub repo
3. Set the same environment variables in Railway's dashboard
4. Add a `Procfile` to the repo root: `worker: cd scraper && python main.py`
5. Use Railway's cron scheduler: `*/5 * * * *`

The scraper code is identical — only the scheduler changes. The `scrape_runs` run-lock
(see setup step 1) means it's now safe to run this *alongside* the GitHub Actions
workflow rather than instead of it, if you want to experiment without fully committing.

### More sources
`LinkedIn_Searches.md` lists a couple more free sources not yet wired in:
- **Jobicy** (remote roles, free RSS/API)
- **Adzuna** (free API, US coverage)

---

## File structure

```
LinkedIn_Job_Bot/
├── scraper/
│   ├── main.py                          # entry point (scrape → classify → notify → store)
│   ├── linkedin.py                      # LinkedIn guest API fetch + parse (with retry/backoff)
│   ├── github_sources.py                # supplementary GitHub internship-list sources
│   ├── classifier.py                    # Claude Haiku classifier (cached prompt, tool output)
│   ├── notifier.py                      # ntfy.sh push notifications
│   ├── digest.py                        # Resend email digest, run on its own schedule
│   ├── db.py                            # Supabase client + dedup + insert + run-lock/stats
│   ├── config.py                        # search terms, locations, env vars
│   └── requirements.txt
├── web/                                 # password-protected Next.js dashboard
│   ├── app/                             # pages + API routes (auth, debug, job status)
│   ├── components/                     # JobList, JobCard
│   ├── lib/                             # Supabase clients + session-token helpers
│   └── middleware.ts                    # dashboard auth gate
├── .github/
│   └── workflows/
│       ├── scrape.yml                   # GitHub Actions cron (every 20 min)
│       └── digest.yml                   # GitHub Actions cron (8am & 6pm ET)
├── Candidate_Profile_and_Filters.md     # your profile — classifier reads this
├── LinkedIn_Searches.md                 # search terms reference
├── .env.example                         # template — copy to .env for local dev
├── .gitignore
└── README.md
```
