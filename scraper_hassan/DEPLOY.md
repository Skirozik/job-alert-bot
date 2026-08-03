# Deploying the Hassan pipeline

IT-support / cybersecurity **internship** search, DC metro, running on GitHub
Actions every 2 hours.

Adapted from `scraper_beyonce/DEPLOY.md`. The one structural difference: this
runs on **GitHub Actions, not Modal**. Modal's workspace is over its spend
limit, and Actions minutes are free and unmetered on a public repo.

---

## Blockers before the first run

**1. No Supabase project exists yet.** Create one, run `schema.sql`, enable RLS.

**2. No ntfy topic yet.** The topic string *is* the password — ntfy has no auth
and auto-creates topics on publish, so a typo'd topic returns HTTP 200 and looks
exactly like success in the logs. Subscribe on the phone and confirm a real push
arrives before trusting it.

**3. Repository secrets are not set.** The workflow needs five. Note that
`HASSAN_PROFILE_MD` is not optional — `classifier.py` reads the profile at
startup and hard-crashes without it.

---

## Steps

### (a) Only you can do these

1. **Supabase → New project.** Name it `hassan-job-alerts`. US East region. Save
   the generated DB password (the scraper never uses it — it authenticates with
   the secret API key — but you'll need it for any SQL client).
2. **SQL Editor → New query** → paste all of `scraper_hassan/schema.sql` → Run.
   Expect "Success. No rows returned" — that's what DDL looks like. Confirm
   `jobs`, `scrape_runs`, `bot_state` appear in Table Editor.
   If you get `relation "jobs" already exists`, **stop** — you're in the wrong
   project. The bare `create table` with no `if not exists` is the guardrail.
3. **Enable RLS** on all three tables, no policies. The scraper uses the secret
   key, which bypasses RLS, so nothing breaks. This is what keeps the dashboard's
   public anon key from reading a real person's job-search history.
4. **Settings → Data API** → copy the **Project URL**. Strip any `/rest/v1/`
   suffix — `create_client()` appends that itself, and leaving it produces
   `/rest/v1/rest/v1/...` 404s.
5. **Settings → API Keys** → copy the **secret** key (`sb_secret_...`), not the
   publishable one. The publishable key cannot bypass RLS and every write fails.
6. **Pick an ntfy topic**, e.g. `hassan-it-jobs-<random>`. Subscribe on his phone
   (or yours — decide whose phone gets these) before deploying.

### (b) Repository secrets

GitHub → repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|---|---|
| `SUPABASE_URL_HASSAN` | the base project URL from step 4 |
| `SUPABASE_SERVICE_KEY_HASSAN` | the `sb_secret_...` key from step 5 |
| `NTFY_TOPIC_HASSAN` | the topic from step 6 |
| `HASSAN_PROFILE_MD` | the **entire contents** of `Hassan_Candidate_Profile_and_Filters.md` |
| `ANTHROPIC_API_KEY` | already set — shared across all three pipelines |

`HASSAN_PROFILE_MD` exists because the profile is gitignored (it names a real
person and this remote is public), so the Actions checkout won't contain it. The
workflow writes it to disk at job start and fails loudly if the secret is empty.

**When you edit the rubric, update this secret too** — otherwise the deployed
pipeline silently keeps classifying against the old version. That's the sharpest
maintenance edge in this setup.

### (c) First run

```
gh workflow run "Job Scraper (Hassan)"
gh run watch
```
Or: Actions tab → *Job Scraper (Hassan)* → Run workflow.

Do this **manually before trusting the cron**. It performs real LinkedIn
requests, real Claude calls, real Supabase writes, and real push notifications.

---

## What a healthy first run looks like

Cold start against an empty table, all 12 terms:

```
=== Job scraper starting — 12 terms x 1 locations ===
Searching: 'help desk intern' in Washington, DC
  p0 (start=0): 10 listings, 8 new
  ...
Total raw: <N> | New: <M> | Rate limited: 0/12
  -> APPLY | <one-line reason>
=== Run complete: <M> new jobs, <K> notified ===
```

**Measured baselines, 2026-08-03 (first live runs).** Expect low yield — this
is a genuinely thin market right now, and that is the pipeline working, not
failing:

| Run | Raw | New | Reached classifier | Notified |
|---|---|---|---|---|
| DC only, 2.5h window | 56 | 21 | 0 | 0 |
| DC only, 24h window | 176 | 91 | 3 | 0 |
| DC only, 30d backfill | 404 | 285 | ~30 | **3** |
| DC + nationwide, 24h | 366 | 172 | 46 | 0 |

The 30-day backfill is the honest measure of supply: **3 actionable jobs in a
month** (Narrative Strategies, BOLAND, Textron).

Three things drive that, and only the last one will change on its own:

1. **LinkedIn's `f_E=1` filter is loose.** Under it, "network intern" returned
   *Registered Nurse - Med Surg* and *Senior Cost and Pricing Manager*. Roughly
   90% of results fail the internship-title gate. That gate is doing real work
   and costs nothing — pre-filtered jobs never reach a description fetch or a
   Claude call.
2. **IT support internships are rarely remote.** Adding `"United States"` to
   LOCATIONS raised internship-titled volume from 0 to ~23/day, but of the 46
   jobs that then reached the classifier, 20 SKIPped purely on location —
   onsite in another metro. The work involves physically handling hardware, so
   geography cannot be widened around. Nationwide is kept as cheap insurance
   (~$0.12/day) for when a genuinely remote one appears, not because it
   materially raises yield.
3. **It is August.** Summer 2027 internship postings ramp up September through
   November. Expect this feed to fill out considerably in the fall.

If yield is still near zero by late September, the lever to reach for is
broader SEARCH_TERMS (or dropping `f_E` entirely and leaning on the
pre-filter), not more geography.

Then check:
- **Supabase** → `jobs` row count equals `New`; `tier` is a mix of APPLY/MAYBE/SKIP;
  `norm_key`, `url`, `found_at` non-null. `scrape_runs` has one row with
  `finished_at` **not null**.
- **Phone** → one notification per `notified`. 🟢 APPLY is high priority, 🟡 MAYBE default.

## Failure signatures

| What you see | What it means |
|---|---|
| `HASSAN_PROFILE_MD secret is empty or unset` | Step (b) incomplete — the workflow caught it before wasting a run |
| `RuntimeError: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set` | Secrets missing or misnamed |
| `NTFY_TOPIC not set — skipping push` per job | Jobs are stored but will never be re-notified — they're already in the dedup index |
| Every reason reads `Classifier error — review manually` | Bad `ANTHROPIC_API_KEY`; those rows are now permanently stored as MAYBE |
| Urgent `Job scraper alert` push, 0 results across all searches | Canary — LinkedIn blocked the runner IP or changed its API |
| Run ends with no `=== Run complete ===`, `finished_at` NULL | Hit the 20-minute Actions timeout mid-loop |

---

## Known limitations

- **Internships only.** `f_E="1"` in `linkedin.py`. Four of his five originally
  requested titles ("it support specialist", "desktop support", "junior system
  admin", "cybersecurity analyst") are full-time phrasings that return almost
  nothing under that filter, so `config.py` rewrites them into how the internship
  versions are actually posted. If he later wants part-time IT work too, change
  `f_E` to `"1,2"` and add the full-time phrasings back — both edits are one-liners.
- **DC metro only, no nationwide-remote sweep.** `LOCATIONS = ["Washington, DC"]`.
  Remote roles surface only when the posting also targets this metro. Adding
  `"United States"` widens it but doubles request volume and spends a Haiku call
  on every out-of-area SKIP.
- **No maintenance tooling.** No backfill/recheck/reclassify scripts, same as the
  Beyonce fork. A job is classified exactly once, and SKIPs are stored precisely
  so they're never re-scored. Fixing a bad run means hand-written SQL. The
  outage sentinel is the literal `Classifier error — review manually` in `reason`.
- **No catch-up.** `LOOKBACK_SECONDS = 9000` against a 2h cron gives 30 minutes of
  overlap and nothing more. `bot_state` exists but is unused, so a failed or
  skipped run leaves a hole no later run queries.
- **Coarse alerting.** The canary fires only when **all 12** searches return zero.
  A run where 10 of 12 are rate-limited completes "successfully" with no alert.
- **The clearance rule has no regex backstop.** It's the highest-stakes filter in
  this market and currently lives entirely in the rubric prompt. Watch the first
  weeks of output for postings requiring an already-held clearance slipping
  through as APPLY; if it happens more than occasionally, add a deterministic
  override in `classifier.py` the way the original pipeline earned its four.
- **Known dedup gap, shared with the other two pipelines.** A season sitting
  mid-title survives `norm_role`'s stripping, so `"IT Support Intern (Fall 2026) -
  Reston, VA"` and `"IT Support Intern - Fall 2026 - Reston - VA"` produce
  different keys for the same job — two notifications. See the comment in
  `db.py`. Fix all three together.
