# Job alert bot

I wanted to be one of the first people to apply when an internship opens. Big companies post on their own careers sites first, and a role can take 6 to 48 hours to reach LinkedIn. So this bot reads those sites directly, rates every new posting against my profile, and sends the good ones to my phone within minutes. It runs on GitHub Actions, so my laptop doesn't have to be on.

## Where it looks

| Source | How often | What it reads |
|---|---|---|
| 104 company job boards | every 5 min | Greenhouse (63), Workday (22), Ashby (15), Lever (2) and SmartRecruiters (2) |
| Meta, Apple and D. E. Shaw | every 5 min | their careers sitemaps |
| Apple and Google | every 5 min | the U.S. internship lists on jobs.apple.com and google.com/about/careers |
| GitHub internship lists | every 2 min | SimplifyJobs, speedyapply and a few others, postings up to 7 days old |
| LinkedIn | every 5 min, 15 min and 1 hr | the first results page, the full search, and every results page, for 10 search terms in the U.S. and Atlanta |

LinkedIn is read through its public job search, without logging in.

## What happens to a new posting

1. Every source checks the database first. The same role found on LinkedIn, a company board and a GitHub list shares one key, so it's rated and pushed once.
2. A title filter drops senior, new-grad and non-internship titles before anything costs money.
3. The bot fetches the description and an LLM (through Anthropic's API) rates it APPLY, APPLY_CAVEAT or INELIGIBLE against my profile, with a reason. The profile lives in a GitHub secret, and so does the model name.
4. The row is stored before anything is sent. APPLY and APPLY_CAVEAT go to my phone through [ntfy](https://ntfy.sh), and each push is claimed in the database first, so three workflows running at the same time can't send it twice.
5. If the API is down, the job is parked as PENDING with its description, and later runs retry it.

## How I know it still works

A watcher that breaks without an error looks the same as a slow week. These are the checks that tell them apart.

- Canary pushes go out when LinkedIn returns zero results, when the Apple or Google list can't be read twice in a row, or when the classifier goes down.
- Apple and Google each get an hourly read of all their U.S. jobs, where something new shows up most days. A long stretch with nothing new sends an alarm.
- Every Monday at 9 AM Eastern, a drill takes one real Apple posting and one real Google posting through the whole path, and both pushes have to come back.
- When the free Supabase quota runs out, the run stops. Otherwise the bot would re-fetch and re-rate every job it already has.
- 22 offline test files run on every push, some of them against saved copies of real careers pages in `fixtures/`.

## Timing

GitHub's own cron is best effort. Schedules set to 5, 10 and 20 minutes came in at 29, 32 and 40 minute median gaps when I measured them. So an external pinger ([cron-job.org](https://cron-job.org)) calls the workflow-dispatch API on the real schedule, and the cron stays as a fallback. The numbers and the setup are in [FAST_PINGS_SETUP.md](FAST_PINGS_SETUP.md).

## The rest

- An email digest of new matches goes out twice a day through [Resend](https://resend.com), and review jobs nobody touched in 45 days get archived.
- `web/` is a password-protected Next.js dashboard on Vercel. Applied, Save and Dismiss sync live across devices, and one deployment serves several people, each reading their own database.
- Forks of the pipeline run for three other people (`scraper_user_a`, `scraper_user_b`, `scraper_user_c`), each with its own Supabase project, rubric and push topic.

## Running your own

1. Make a free [Supabase](https://supabase.com) project. In the SQL Editor, run [schema.sql](schema.sql), then every file in `migrations/` in name order.
2. Install the [ntfy app](https://ntfy.sh) and subscribe to a topic name nobody could guess.
3. Write your profile and filters in plain English. That file is the classifier's whole rubric.
4. Add these repository secrets (Settings, then Secrets and variables, then Actions).

| Secret | What it is |
|---|---|
| `SUPABASE_URL` | Supabase, Settings, API, Project URL |
| `SUPABASE_SERVICE_KEY` | the service_role key from the same page |
| `ANTHROPIC_API_KEY` | from console.anthropic.com |
| `CLASSIFIER_MODEL` | the id of the model the classifier calls |
| `CANDIDATE_PROFILE_MD` | the whole profile file from step 3 |
| `NTFY_TOPIC` | the topic from step 2 |
| `RESEND_API_KEY`, `RESEND_FROM`, `ALERT_EMAIL` | for the email digest |

5. Turn on Actions and run Job Scraper once by hand. For the real 5-minute cadence, set up the pinger in [FAST_PINGS_SETUP.md](FAST_PINGS_SETUP.md).
6. For the dashboard, deploy `web/` to Vercel with `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY` and `DASHBOARD_PASSWORD`. [web/.env.local.example](web/.env.local.example) shows how to add more people.

To run it locally, copy `.env.example` to `.env`, fill it in, and put your profile at the repo root as `Candidate_Profile_and_Filters.md` (git ignores it).

```bash
cd scraper
pip install -r requirements.txt
python main.py
```

## Layout

```
scraper/                  the main pipeline
  main.py                 LinkedIn search, classify, store, push
  ats_watch.py            the 104 company boards, plus the Apple and Google lists
  github_watch.py         the GitHub internship lists
  linkedin_watch.py       LinkedIn's first results page, every 5 min
  classifier.py           the LLM classifier
  apple_watch.py          Apple's alarms, shadow watch and drill
  google_watch.py         Google's alarms, shadow watch and drill
  digest.py               the twice-a-day email
  test_*.py               offline tests
scraper_user_a/, _b/, _c/ the other three people's pipelines
web/                      the Next.js dashboard
migrations/               database changes, in order
fixtures/                 saved copies of real careers pages for the tests
.github/workflows/        every schedule, the weekly drill and the test run
```
