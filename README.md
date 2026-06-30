# LinkedIn Internship Job Alert Bot

Scrapes LinkedIn every 30 min, classifies each new internship against your candidate profile using Claude Haiku, and pushes APPLY/MAYBE matches to your phone (ntfy.sh) and email (Resend). Runs entirely on GitHub Actions — your PC never needs to be on.

---

## How it works

```
GitHub Actions (every 30 min)
  └─ scraper/main.py
       ├─ 10 LinkedIn searches (5 terms × 2 locations, f_E=1 internship filter)
       ├─ Canary: 0 results across all searches → push "may be blocked" alert
       ├─ Dedup against Supabase (by job ID + normalized company/role key)
       ├─ Fetch job description for each new listing
       ├─ Claude Haiku classifies: APPLY / MAYBE / SKIP
       ├─ ntfy.sh push for APPLY and MAYBE
       └─ All results stored in Supabase (SKIP stored silently, never re-classified)
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
  norm_key         text,
  tier             text,                    -- APPLY | MAYBE | SKIP
  reason           text,
  suggested_resume text,
  status           text default 'new',      -- new | saved | applied | dismissed
  posted_at        timestamptz,
  found_at         timestamptz default now()
);

create index jobs_norm_key_idx on jobs (norm_key);
create index jobs_tier_idx on jobs (tier);
create index jobs_found_at_idx on jobs (found_at desc);
```

3. Go to Settings → API → copy **Project URL** and **service_role** key (not anon key)

### 2. Set up ntfy.sh on your phone

1. Install the [ntfy app](https://ntfy.sh) on iOS or Android
2. Pick a private topic name — something hard to guess, e.g. `ifiok-jobs-x7k2m9`
3. In the app: tap ＋ → subscribe to your topic
4. You'll receive a push whenever a new APPLY or MAYBE job is found

### 3. Get an Anthropic API key

Go to [console.anthropic.com](https://console.anthropic.com) → API Keys → Create key.
The classifier uses `claude-haiku-4-5-20251001` — cost is ~$0.0001 per job classified.

### 4. (Phase 2) Get a Resend API key

Go to [resend.com](https://resend.com) → API Keys. Free tier = 3,000 emails/month.
You'll also need to verify a sender domain (or use their onboarding domain for testing).

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
| `RESEND_API_KEY` | resend.com → API Keys (Phase 2, can leave blank for now) |
| `RESEND_FROM` | Your verified sender email (Phase 2) |
| `ALERT_EMAIL` | simeonchere@gmail.com |

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

---

## Testing the canary alert

To verify the zero-results alert works, temporarily edit `config.py` and change `SEARCH_TERMS` to `["xyzzy-no-such-job-12345"]`, run the scraper, and confirm you receive an ⚠️ push. Then revert.

---

## Future upgrades

### Railway (for truly real-time polling, ~5 min intervals)
GitHub Actions cron has up to 60 min drift during peak hours. For faster alerts:
1. Sign up at [railway.app](https://railway.app) (free $5/month credit)
2. Create a new project → Deploy from GitHub repo
3. Set the same environment variables in Railway's dashboard
4. Add a `Procfile` to the repo root: `worker: cd scraper && python main.py`
5. Use Railway's cron scheduler: `*/5 * * * *`

The scraper code is identical — only the scheduler changes.

### Phase 2 — Email digest
Uncomment the Resend calls in `notifier.py` (stub is ready). Sends a daily digest of APPLY/MAYBE jobs at 8am and 6pm.

### Phase 3 — Web dashboard
A Next.js dashboard on Vercel reads from the same Supabase table. Job cards show tier badge, classifier reason, suggested resume, and Apply/Save/Dismiss buttons. Real-time updates via Supabase subscriptions.

### Phase 4 — Additional sources
`LinkedIn_Searches.md` lists additional free sources:
- **Jobicy** (remote roles, free RSS/API)
- **Adzuna** (free API, US coverage)
- **GitHub repos**: `SimplifyJobs/Summer2026-Internships`, `speedyapply/2026-SWE-College-Jobs`, `speedyapply/2026-AI-College-Jobs`

---

## File structure

```
LinkedIn_Job_Bot/
├── scraper/
│   ├── main.py                          # entry point
│   ├── linkedin.py                      # LinkedIn guest API fetch + parse
│   ├── classifier.py                    # Claude Haiku classifier
│   ├── notifier.py                      # ntfy.sh push notifications
│   ├── db.py                            # Supabase client + dedup + insert
│   ├── config.py                        # search terms, locations, env vars
│   └── requirements.txt
├── .github/
│   └── workflows/
│       └── scrape.yml                   # GitHub Actions cron (every 30 min)
├── Candidate_Profile_and_Filters.md     # your profile — classifier reads this
├── LinkedIn_Searches.md                 # search terms reference
├── .env.example                         # template — copy to .env for local dev
├── .gitignore
└── README.md
```
