# Deploying the Brice pipeline

A full-time, entry-level job search for Brice: technical pre-sales (sales and solutions
engineering), network and infrastructure, and the adjacent IT-engineering families, anywhere
in the United States. It runs on GitHub Actions (`.github/workflows/scrape_brice.yml`), has its
own Supabase project and ntfy topic, and shows up on the shared dashboard as its own login.

This runbook was written from the code on this branch. The other two forks' `DEPLOY.md`
files describe older behaviour, so don't copy steps from them.

---

## 1. What it is

One workflow (`Job Scraper (Brice)`), scheduled for :40 past every odd hour (`'40 1-23/2 * * *'`).
Each run is one process: `scraper_brice/main.py`. Before it, the workflow writes the rubric from its
secret and checks the rubric's structure (`test_rubric_contract.py`, labels only); after it, a last
step alerts the owner if `main.py` never ran or the run was cancelled or timed out.

**Collect** (listing fields only; nothing is fetched per job, and nothing goes to Claude yet):

| Order | Source | What it reads | Filter before Claude |
|---|---|---|---|
| A | 26 company career boards (`ats_boards.py`) | Greenhouse / Ashby / Workday public APIs, via the vendored `ats_sources.py` | `title_gate.source_gate`: U.S. location, a target family or an early-career program, and the title gate |
| B | LinkedIn guest search | 22 terms × `United States`, 24 h window, `f_E=2,3`, up to 10 pages per term | `title_gate.gate`: internships, pure sales, the engineer floor (help desk / desktop support / technician), seniority, level II+, architects other than solutions architects, TAM titles without an entry marker |
| C | jobright-ai new-grad lists | 5 READMEs on `raw.githubusercontent.com` (Engineering, Sales, Software-Engineer, Consultant, and Support, where a support-type title is kept only if it is a support-*engineer* title; solutions, network and IT-systems titles are kept as on any list) | `source_gate` (family only) and a 10-day age limit. jobright.ai itself is never requested |

**Process**, in this order:

1. LinkedIn titles the gate dropped are stored as `INELIGIBLE`, reason `Pre-filtered: <rule>`.
2. The ATS, LinkedIn and jobright queues are classified under per-run caps of **100 / 180 / 100**,
   entry-marked and primary-family titles first. Each job first passes the already-stored guard.
   Then it gets its description: LinkedIn's detail page, or one Workday API call; Greenhouse and
   Ashby rows already carry one, and jobright rows have none. Then it is classified, stored, and pinged.
3. Up to 200 `PENDING` rows are retried, oldest first: jobs whose classification failed on an earlier
   run. A full run parked during a Claude outage (up to 380 rows) drains in two healthy runs.
4. jobright ETags are saved for each list that left nothing behind, so an unchanged README costs a
   304 next time.

main.py stops *starting* work after 48 minutes. The workflow timeout is 60 minutes, and the
run-lock in `scrape_runs` expires after 75. The board sweep has its own 10-minute budget, and the
LinkedIn searches have 25 minutes counted from their own start, so a slow board cannot eat the
LinkedIn pass.

Anything past a cap or the time budget is a **leftover**. It is not stored, so the next run finds it
again: ATS jobs while they are open, jobright rows while they are in the README, and LinkedIn jobs
while they are inside the 24 h window. For LinkedIn that takes one extra step: a search normally
stops after two pages whose jobs are all stored, and the leftovers sit on the pages behind those. So
a run that leaves LinkedIn jobs unstored writes `linkedin_leftover_at` to `bot_state`, and while
that marker is under 24 h old every search pages to its end. A complete run that leaves nothing
behind clears it.

**Tiers** (from the rubric): `APPLY`, `APPLY_CAVEAT`, `INELIGIBLE`. `PENDING` is a queue state,
never a verdict, and the dashboard doesn't show it.

**Pings** go to Brice's topic (`NTFY_TOPIC_BRICE`). `APPLY` is sent at high priority (it makes a
sound); `APPLY_CAVEAT` is sent at low priority (silent). A row with less than 200 characters of
description is capped at `APPLY_CAVEAT`, so it is always silent. That covers every jobright row, and
any LinkedIn or Workday row whose description fetch failed.

**Owner alerts**: every infrastructure alert goes to the owner's topic (repository secret
`NTFY_TOPIC`), never to Brice's. That covers the classifier being down, zero LinkedIn results,
empty boards, a broken jobright list, the Supabase quota, crashes, and preflight failures.

---

## 2. Owner checklist, in order

### 2.1 Supabase project

1. **Free a slot: pause Beyonce's project.** The free plan allows two *active* free projects
   per account, counted across every organization where you are Owner or Admin. Three projects
   already exist, and paused ones don't count toward the limit. Pause hers from its project settings
   (Settings → General → Pause project). Her pipeline is already disabled; keep `Job Scraper
   (Beyonce)` disabled while her project is paused, because a run against a paused project fails.
   Her dashboard login shows nothing until the project is restored from the Supabase dashboard.
2. **Create `brice-job-alerts`** (US East) under the account whose slot you just freed, close to
   go-live: an idle free project pauses after 7 days.
   - **Keep it out of main's organization.** Usage quotas such as the free plan's 5 GB of egress are
     per organization, and main's organization used all of it on 2026-09-12; after that, every
     request got HTTP 402.
   - If Beyonce's project lived in that organization, create a new free organization for this
     project instead.
3. **SQL Editor → New query** → paste all of `scraper_brice/schema.sql` → Run. Expect "Success. No
   rows returned".
   - If you get `relation "jobs" already exists`, **stop**: this is the wrong project. The bare
     `create table` (no `if not exists`) is there to catch exactly that.
4. **RLS**: `schema.sql` enables row-level security on all three tables, with no policies.
   - In Table Editor, confirm that `jobs`, `scrape_runs` and `bot_state` exist and each shows RLS
     enabled. If one doesn't, enable it there; no policy is needed.
   - The scraper and the dashboard use the secret key, which bypasses RLS. The publishable key
     reads nothing.
5. **Settings → Data API**: copy the **Project URL** and strip any `/rest/v1/` suffix. The client
   appends that path itself, so a URL that already has it sends every request to
   `/rest/v1/rest/v1/…`, and each one 404s. The dashboard shows up empty in that case.
6. **Settings → API Keys**: copy the **secret** key (`sb_secret_…`), not the publishable one.

### 2.2 ntfy topic

- Pick a topic with a random part: characters `[-_A-Za-z0-9]` only, at most 64 of them, and
  different from every other persona's. For example, a short word followed by the output of
  `openssl rand -hex 10`.
- The topic name *is* the password: anyone who knows it can read the pings, and ntfy.sh keeps
  each message for 12 hours. Every ping carries the classifier's reason, which can mention his own
  details (a GPA floor, a start date). A topic made only of guessable words, such as anything that
  appears in this public repo, can be found and read, so the random part is not optional. Keep the
  name only in the secret and in his ntfy app.
- Subscribe to it in the ntfy app on Brice's phone.
- Send one test push by hand before the first run. ntfy auto-creates topics, so a mistyped topic
  still returns HTTP 200 and looks like success in the logs.

### 2.3 Repository secrets

GitHub → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|---|---|
| `SUPABASE_URL_BRICE` | the base Project URL from 2.1 step 5 |
| `SUPABASE_SERVICE_KEY_BRICE` | the `sb_secret_…` key from 2.1 step 6 |
| `NTFY_TOPIC_BRICE` | the topic from 2.2 |
| `BRICE_PROFILE_MD` | the whole rubric file, uploaded as shown below |
| `ANTHROPIC_API_KEY` | already set (shared by every pipeline) |
| `NTFY_TOPIC` | already set: the **owner's** topic, which receives this pipeline's infrastructure alerts |

Upload the rubric from wherever it lives outside the repo:

```
gh secret set BRICE_PROFILE_MD -R Skirozik/job-alert-bot < /path/to/Brice_Candidate_Profile_and_Filters.md
```

- Secrets are capped at 48 KB.
- Before uploading, check the rubric's structure locally. This prints check labels only, never
  rubric text:

  ```
  cd scraper_brice && CANDIDATE_PROFILE_PATH=/path/to/Brice_Candidate_Profile_and_Filters.md python -X utf8 test_rubric_contract.py
  ```
- **Re-upload the secret after every rubric edit.** Otherwise the deployed classifier silently keeps
  the old rubric.

**Set all four before the merge.** Once `scrape_brice.yml` is on master, its schedule starts on its
own at the next odd hour :40:

- Without `BRICE_PROFILE_MD`, every run fails at the profile step: a red run, no ping to Brice, and a
  `Brice: workflow failed` alert to the owner. A rubric that fails the structure check stops the run
  the same way.
- With the profile set but another secret missing, every run fails preflight and sends the owner a
  `Brice: run did not start` alert. That alert is not throttled, so it comes once per run.

### 2.4 Vercel (Brice's dashboard login)

The dashboard finds people from environment variables, so no code changes are needed. Two Vercel
projects deploy from master. Set these in the one the friends log into:

| Variable | Value |
|---|---|
| `PERSONA_BRICE_PASSWORD` | a new password, e.g. from `openssl rand -base64 24` |
| `PERSONA_BRICE_SUPABASE_URL` | the base Project URL (no `/rest/v1`) |
| `PERSONA_BRICE_SERVICE_KEY` | the `sb_secret_…` key |
| `PERSONA_BRICE_LABEL` | `Brice` |
| `PERSONA_BRICE_USERNAME` | optional; his login name defaults to `brice` |

- The ID must be upper-case (`PERSONA_brice_…` is never discovered) and at most 32 characters.
- If `PERSONA_IDS` is set, append `brice` to it, or he stays hidden.
- Leave `SESSION_SECRET` as it is.
- Environment changes apply only to *new* deployments. Redeploy, or set the variables before the
  merge, whose push deploys both projects.

### 2.5 Merge, then the first run

1. Merge `feat/brice-persona` into master. A manual run (`workflow_dispatch`) only works once the
   workflow is on master.
2. Actions → **Job Scraper (Brice)** → Run workflow (or `gh workflow run "Job Scraper (Brice)"`,
   then `gh run watch`). Run it on Actions, not locally. A real run writes to Brice's database,
   pings his phone, and sends LinkedIn requests from the machine it runs on.
3. Check:
   - the log ends with a `Run summary:` line;
   - the run's `scrape_runs` row has `finished_at` set (SQL in section 6);
   - a ping reached Brice's phone;
   - his dashboard login shows the queue.

### 2.6 Tell Brice

Send him:

- the dashboard URL, his username and password;
- the ntfy topic;
- what the tiers mean: `APPLY` makes a sound and is a clean fit. `APPLY_CAVEAT` is silent: worth
  applying, with one reservation named in the ping.

---

## 3. Running it locally

1. Copy `.env.brice.example` (repo root) to `.env.brice`; it is gitignored. Set
   `CANDIDATE_PROFILE_PATH` to the rubric's path outside the repo.
2. Dry run: no secrets needed. It never touches the database, Claude or ntfy:

   ```
   cd scraper_brice
   python -X utf8 main.py --dry-run --no-linkedin
   ```

   On 2026-09-30, after the review fixes, it finished in 99 s:
   - ATS: 17,653 listings from 26/26 boards (about 700 requests), 256 kept by the gate.
   - jobright: 73,361 README rows (0 unparsed), 248 kept, 239 after in-run dedup.
   - It printed per-source counts, drop counts by rule, and sample titles.

   Other flags, valid only with `--dry-run`: `--no-ats`, `--no-jobright`, `--sample N`, and
   `--linkedin-pages N` / `--linkedin-terms N`. Without `--no-linkedin`, the dry run sends LinkedIn
   requests from your IP, at least 5 s apart.
3. Offline tests: no network, no secrets. Run each line from the repo root:

   ```
   cd scraper_brice && python -X utf8 run_tests.py
   cd scraper && python -X utf8 -B test_norm_key.py              # key code identical to main's
   cd web && node lib/__tests__/selectCols.test.mjs              # dashboard columns exist in schema.sql
   ```

   `test_rubric_contract.py` SKIPs unless `CANDIDATE_PROFILE_PATH` points at the rubric.
4. Never run `python main.py` without `--dry-run` from a laptop.

---

## 4. The first run

- **Nothing is seeded silently.** The database starts empty, so every open listing is new, and
  every `APPLY` / `APPLY_CAVEAT` the run stores is pinged. Tens of `APPLY` pings with sound are
  possible on day one. jobright rows are title-only, so they arrive silently.
- **The caps bound each run: 100 ATS, 180 LinkedIn, 100 jobright classifications**, plus up to 200
  PENDING retries.
  - The 2026-09-30 dry run found 256 ATS and 239 jobright candidates. At 100 per run, each backlog
    drains in about three runs.
  - LinkedIn's first-day volume was not measured, so expect its first run to hit the 180 cap.
  - LinkedIn leftovers come back only while they are inside the 24 h window. Until the backlog is
    gone, each run pages every search to its end (up to 220 search requests, about 15 minutes).
- **Cost** (estimates): about 1,200–1,300 one-time classifications, roughly $4–7 at about
  $0.003–0.006 each. After that, about $0.45–1.70 a day.
- **ntfy** allows a burst of 60 messages, then one every 5 s per IP. The notifier retries a 429
  once after 6 s. Each ping follows a Claude call, so bursts stay small.

---

## 5. What a healthy run looks like

The log lines are part of the interface; keep them stable. A healthy run looks like this:

```
=== Brice pipeline starting — 22 terms x 1 locations, 5 jobright lists ===
ATS sweep: 17653 listings from 26/26 boards in 86 s | kept 256 (SALES_SOLUTIONS 123, SECURITY 49, ...)
ATS: 17653 listings from 26/26 boards | kept by the gate 256 | new 3
Searching: 'associate sales engineer' in United States
  p0 (start=0): 10 listings, 4 new
  p1 (start=10): 10 listings, 0 new
  p2 (start=20): 10 listings, 0 new
  All duplicates in DB — stopping pagination
...
Total raw: 1480 | New: 52 | Rate limited: 0/22 searches
jobright Engineering: HTTP 200 | 7727 rows (0 unparsed, 7727 in window) | kept 203 | new 6
jobright Sales: not modified since the last complete read
...
Processing: '<title>' @ <company> [<id>]
  Pre-filter SKIP (seniority/leadership title)
Processing: '<title>' @ <company> [<id>]
  Description: 4213 chars
  -> classified | id=<id>
DB: stored <id>
...
Run summary: ats cand 3 | linkedin new 52 (claude 38, pre-filtered 14) | jobright cand 9 | classified 50 | parked 0 | pushed 7 | leftover 0
```

The numbers above are illustrative, except the ATS and jobright lines, which come from the dry run.
In steady state, expect roughly 25–50 LinkedIn classifications per run, 1–2 from ATS, and 8–10 from
jobright (estimates).

The log never contains a classifier reason, the rubric, a topic, or a Supabase URL or key. It does
show the company and title of every job processed (Actions logs are public), so it never shows a
job's tier or whether it was pinged: titles next to their verdicts would let a reader work out
private rubric rules. Look a job up by its id on the dashboard or with the SQL in section 6; the
run summary has the totals.

---

## 6. Read-only SQL (Supabase → SQL Editor, Brice's project)

Times are UTC.

```sql
-- Tiers found today
select tier, count(*) from jobs
where found_at >= date_trunc('day', now())
group by tier order by count(*) desc;

-- Tiers by source, last 24 h
select case when search_term like 'ats:%' then 'ats'
            when search_term like 'jobright:%' then 'jobright'
            else 'linkedin' end as source,
       tier, count(*)
from jobs where found_at > now() - interval '1 day'
group by 1, 2 order by 1, 2;

-- Yield per search term / board / list, last 7 days (pre-filtered rows excluded)
select search_term,
       count(*) filter (where tier in ('APPLY','APPLY_CAVEAT')) as actionable,
       count(*) as total
from jobs
where found_at > now() - interval '7 days' and reason not like 'Pre-filtered:%'
group by 1 order by total desc;

-- Pre-filter audit: drops per gate rule, last 7 days
select substring(reason from 'Pre-filtered: (.*)') as rule, count(*)
from jobs
where reason like 'Pre-filtered:%' and found_at > now() - interval '7 days'
group by 1 order by 2 desc;

-- ...and the titles one rule dropped (swap in any rule name from the query above)
select title, company, search_term from jobs
where reason = 'Pre-filtered: seniority/leadership title'
order by found_at desc limit 40;

-- PENDING backlog (jobs parked while Claude was unavailable)
select count(*) as pending, min(found_at) as oldest from jobs where tier = 'PENDING';

-- Last 10 runs (finished_at null = killed by the timeout, or still running)
select id, started_at, finished_at - started_at as took, total_raw, new_jobs, ats_candidates,
       jobright_candidates, classified, failed, leftover, notified, rate_limited
from scrape_runs order by id desc limit 10;

-- Alert throttles, jobright ETags and the LinkedIn leftover marker (linkedin_leftover_at)
select key, value from bot_state order by key;
```

---

## 7. Alerts and failure signatures

The owner gets these on the `NTFY_TOPIC` topic; Brice never does.

| Alert title | When | Throttle | Exit |
|---|---|---|---|
| `Brice: run did not start` | preflight: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `ANTHROPIC_API_KEY`, `NTFY_TOPIC` or the rubric file missing or empty | none | 1 |
| `Brice: Supabase unavailable` | `start_run` failed twice, 10 s apart (outage, or `schema.sql` never run). Fails closed: nothing is fetched | none | 1 |
| `Brice: Supabase quota spent` | a 402 or quota refusal, at the start or mid-run; the run stops before more work | once per run | 1 |
| `Brice: run crashed` | any other exception (`finish_run` still runs) | none | 1 |
| `Brice: classifier down` | a billing or auth failure parked jobs as PENDING. Top up at console.anthropic.com; they retry automatically | 6 h | 0 |
| `Brice: all classifications failed` | ≥ 3 attempted, 0 succeeded, and the classifier was not reported down | 6 h | 0 |
| `Brice: classifier recovered` | parked jobs classified after a down alert (priority default) | — | 0 |
| `Brice: LinkedIn returned nothing` | searches ran and returned 0 listings; ATS and jobright still ran | 6 h | 0 |
| `Brice: LinkedIn search incomplete` | the search budget skipped terms, or more than half the searches were rate limited (the message gives the ATS sweep's minutes too) | 6 h | 0 |
| `Brice: ATS boards returned nothing` | none of the 26 boards returned a listing | 24 h | 0 |
| `Brice: ATS sweep cut short` | the sweep hit its 10-minute budget and refused the rest of its requests; a board is slow or stuck | 24 h | 0 |
| `Brice: jobright <list> list` | HTTP other than 200/304, 0 parsed rows, or > 5 % of link rows unparsed (format drift) | 24 h per list | 0 |
| `Brice: pings failing` | ≥ 3 pings tried this run and ntfy accepted none (a wrong or reserved topic); the jobs are stored and are not pinged later | 6 h | 0 |
| `Brice: workflow failed` | sent by the workflow's last step, not `main.py`: the scraper never ran (install, profile or rubric check failed) or the run was cancelled or hit the 60-minute timeout | none | — |

Other signatures in the log:

- `Another run appears to be in progress (<75 min, unfinished) — skipping`: exit 0. A run killed at
  the 60-minute timeout never records `finished_at`, so its lock expires 75 minutes after it started.
- `Search time budget spent — skipping N term(s): …`: the LinkedIn loop hit its 25-minute budget
  (or the run's 48 minutes), so the last terms were skipped. The owner gets
  `Brice: LinkedIn search incomplete`.
- `Leftover <source>: N (cap|time) — next run picks them up`: normal on the first days. If it shows
  up in steady state, see section 8.
- `LinkedIn jobs were left unstored in the last 24 h — every search pages past stored results`: the
  `linkedin_leftover_at` marker is fresh, so this run pages every search to its end.
- `ATS sweep hit its 10-minute budget: …`: a board was still paging at the budget; it kept the pages
  it had.
- `ATS boards with no listings this run (an error or an empty board): …`: one board failed or is empty.
- `ntfy post failed (<type>, status=<code>)`: the ping was not delivered. The job is stored anyway,
  and the ping is not retried later. When every ping of a run fails, the owner gets
  `Brice: pings failing`.
- `OWNER_NTFY_TOPIC not set — infrastructure alerts will only be logged`: the `NTFY_TOPIC` secret is
  missing.

---

## 8. Tuning after the first week

- **Search terms.** Use the yield query in section 6. Terms under about 1 % actionable are the ones
  to cut. Edit `config.SEARCH_TERMS` together with `EXPECTED_TERMS` in `test_config.py`, which pins
  the list.
- **Boards.** In the 2026-09-30 dry run, Capital One (0 of 1,852), HD Supply (0 of 345) and Tempus
  (0 of 147) kept nothing. Micron costs about 154 requests per run and kept 8. These are the first
  candidates for removal if a week of runs agrees. Remove a board in `ats_boards.py`, and update its
  `assert len(ATS_BOARDS) == 26` together with `test_ats_pass.py`, which pins the count.
- **Rate limits.** If `Rate limited: N/22` is often above 0, set `ALL_DUP_PAGES_TO_STOP` back to 1
  (config.py).
- **Sales scope.** When Brice answers the sales-scope question, disable
  `.github/workflows/reminder.yml` either way, as its header asks. What `DROP_PURE_SALES = False`
  (config.py; `test_config.py` pins it) does on its own is narrow:
  - LinkedIn: AE/SDR/BDR and account-manager titles pass `title_gate.gate`, but none of the 22
    search terms is a sales term, so few would arrive. Add sales terms to `SEARCH_TERMS` (and
    `EXPECTED_TERMS` in `test_config.py`).
  - Company boards: sales-program titles ("Sales Development Program") pass as `PROGRAM`.
  - Plain AE/SDR/BDR titles from the boards and from jobright are still dropped. The family filter's
    `NON_TECH_ROLE` in `families.py` excludes them whatever the flag says, and jobright has no
    program pass-through. Including them needs a sales family in `families.py` and
    `title_gate.ALLOWED_FAMILIES`.
  - Then move rubric rule I-4 to `APPLY_CAVEAT` or delete it, and re-upload `BRICE_PROFILE_MD`.
- **Caps.** If leftovers persist after the backlog has drained, raise `MAX_CLASSIFY_PER_RUN`. Watch
  the run durations in `scrape_runs`, which must stay under the 48-minute budget.

---

## 9. Known limitations

- **Dedup is by `company|title`** (`norm_key`). One title posted in several cities is stored once,
  and a later posting with a stored title is never re-classified. Regional re-posts with the region
  in the title, like "SE Desk - Northeast", ping once per region. When two sources spell the company
  or the title differently (LinkedIn's "Tempus AI" against the board key "Tempus", an abbreviated
  LinkedIn title), one posting is classified, and can be pinged, once per source.
- **jobright** is a third-party feed, read from its GitHub READMEs only; jobright.ai is never
  requested, per its robots.txt. Its repos are named by year (`2026-…`). When a new year's repos
  appear, edit `JOBRIGHT_LISTS`; the canary alert fires on a 404, zero rows, or format drift.
  Pings for these rows link to jobright.ai, whose apply flow is unverified.
- **Title-only rows** can't show years-of-experience or start-date requirements, which are the most
  common reasons Brice would be rejected. That covers every jobright row, and any LinkedIn or
  Workday row whose description fetch failed. The cap keeps them at a silent `APPLY_CAVEAT`.
- **GitHub delays scheduled runs.** The forks' 2-hourly schedules actually run about 4–5 times a
  day. The 24 h LinkedIn window covers the gaps, but pings can arrive hours after a posting.
- **The ATS fetchers are vendored.** `ats_sources.py` is a copy of `scraper/ats_sources.py` and does
  not receive main's future fixes. `test_ats_pass.py` prints `NOTE: drift` when the two differ.
- **Never run `scraper/backfill_norm_keys.py` against this database** without checking first: it
  keys `gh:` tracker rows as internships. This database has none, but a backfill should prove that
  before writing.
- **Dashboard differences from the main project.** There is no `target_key` column, no
  `set_job_group_status` function and no `resume_builds` table.
  - Status clicks use the dashboard's verified, non-atomic PATCH fallback, and skip sibling
    widening.
  - The resume panel stays empty.
  - Gold stars follow the owner's company and pay rules, not Brice's.
- **Shared Anthropic key.** This is a fourth consumer on one balance. A credit outage parks jobs as
  PENDING and alerts the owner; auto-reload or the balance is the owner's call.
- **Public logs.** Actions logs show the company and title of every job processed, but not its tier,
  its reason or whether it was pinged; only the run summary's totals (classified, pushed). They never
  show the rubric or a topic. A run that classifies a single job still reveals, through those totals,
  whether it was pinged.
