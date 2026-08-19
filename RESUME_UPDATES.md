# Resume Updates — LinkedIn Job Bot

Covering the last 14 days (Jul 13 – Jul 27, 2026): 4 commits plus a substantial body of
uncommitted work. Every claim below was checked against the actual source files, not just
commit messages. Numbers are attributed to their source — see
[Verification status of the numbers](#verification-status-of-the-numbers) before using any
of them in an interview.

---

## New user-facing features

### Direct-ATS fast-path watcher for 50 target companies
*Commit `5e255de` — [scraper/ats_config.py](scraper/ats_config.py),
[scraper/ats_sources.py](scraper/ats_sources.py), [scraper/ats_watch.py](scraper/ats_watch.py)*

**What it does:** Polls 50 hand-picked companies' own ATS job-board APIs directly — the same
public JSON endpoints their careers pages call — so a new posting is caught the moment it goes
live on the company's board, instead of waiting for LinkedIn to syndicate it into the scrape
the bot otherwise depends on.

**Tech:** Python, `requests`, Modal serverless cron (3-minute interval), Supabase (Postgres) for
dedup and the shared run-lock. Four ATS integrations written against each vendor's public
listing API: Greenhouse (`boards-api.greenhouse.io`), Lever (`api.lever.co`), Ashby
(`api.ashbyhq.com`), SmartRecruiters (`api.smartrecruiters.com`).

**Design decisions worth naming in an interview:**
- Greenhouse/Lever/Ashby return the full job description *in the listing call*, so
  [scraper/main.py:130-137](scraper/main.py#L130-L137) short-circuits the per-job detail fetch for
  `ats:`-prefixed ids — one HTTP request per company instead of one per job. The LinkedIn and
  GitHub-tracker paths both still need a second request per job.
- Every fetcher is best-effort: a bad token, ATS outage, or network error is logged and returns
  `[]`, so one broken company entry never blocks the other 49
  ([ats_sources.py:82-84](scraper/ats_sources.py#L82-L84)).
- Reuses the existing `start_run`/`finish_run` Supabase run-lock, so the three scan paths
  (LinkedIn main, GitHub tracker, ATS) can't double-process the same job.
- A full company board dump contains every level and role, so the watcher re-applies the main
  scan's senior / new-grad / non-internship-title pre-filters before spending a Claude call
  ([ats_watch.py:70-91](scraper/ats_watch.py#L70-L91)).

**Measured outcome (from the commit's own dry run against the live DB, notifications
suppressed):** 14,294 raw listings across the 50 companies → 36 internship-titled postings
survived pre-filtering and were classified → 3 came back APPLY/MAYBE, including a SpaceX Fall
2026 SWE internship/co-op that the LinkedIn-only pipeline had not caught.

**Coverage detail, stated accurately:** the config is 38 Greenhouse, 10 Ashby, 2 SmartRecruiters,
**0 Lever**. The Lever fetcher is implemented and working but no configured company currently
uses Lever — say "four ATS integrations, three currently exercised," not "50 companies across
four platforms."

### "⚡ Direct" source badge on the dashboard
*Commit `5cc907b` — [web/components/JobCard.tsx](web/components/JobCard.tsx)*

**What it does:** Jobs caught by the ATS fast path (identified by their `ats:` id prefix) get an
indigo accent border, a ring, and a "⚡ Direct" badge, so it's visible at a glance which postings
came straight from a company's ATS ahead of LinkedIn versus the LinkedIn/GitHub-tracker sources.

**Tech:** Next.js, React, TypeScript, Tailwind. ~15 lines; small but genuinely user-facing.

---

## Architecture / infrastructure changes

### Second-persona pipeline (`scraper_beyonce/`) — built, **not deployed**
*Uncommitted / untracked — see [Not shipped](#not-shipped--do-not-claim-as-live)*

**What it does:** A complete parallel instance of the bot for a different candidate and a
fundamentally different search — Atlanta-only hourly/administrative healthcare and front-desk
roles instead of a nationwide competitive SWE-internship search.

**Tech:** Python, Playwright-free LinkedIn scrape path, Claude Haiku (`claude-haiku-4-5`)
classifier with a cached system prompt and forced tool-call output, Supabase, ntfy.sh push,
standalone Modal app on a 2-hour cron.

**The architecturally interesting parts:**
- **The title pre-filter is inverted.** The original SKIPs anything that *doesn't* look like an
  internship; this one SKIPs anything that *does*
  ([scraper_beyonce/main.py:87-94](scraper_beyonce/main.py#L87-L94)). Same engine, opposite
  polarity — a good demonstration that the pipeline generalizes beyond its original rubric.
- **Deliberate blast-radius isolation.** A standalone `modal_app_beyonce.py` rather than a second
  function inside the existing `modal_app.py`, with the reasoning written into the file: a shared
  app means one `modal deploy` redeploys both, so a syntax error in the new pipeline could break
  deploys of the live one ([modal_app_beyonce.py:3-9](modal_app_beyonce.py#L3-L9)). Separate
  Supabase project, separate ntfy topic, separate Modal secret.
- **Deliberate subtraction.** The fork drops all four deterministic classifier overrides from the
  original (`_apply_full_time_override`, `_apply_school_specific_override`,
  `_apply_advanced_degree_override`, `_never_skip_github_sourced`) because each encoded the
  original candidate's specific situation, and adds no new ones on day one — the stated plan is to
  add overrides only after live monitoring shows real misses, which is how the original's were
  earned ([scraper_beyonce/classifier.py:10-16](scraper_beyonce/classifier.py#L10-L16)).
- **Test suite written before any production data exists:** 15 synthetic fixture postings covering
  every branch of the inverted rubric, calling `classify()` directly so there is zero chance of a
  DB write or notification
  ([scraper_beyonce/test_classifications.py](scraper_beyonce/test_classifications.py)).
  **Currently 15/15 passing** (run Jul 28, 2026 against `claude-haiku-4-5`).

### Simplify Copilot–assisted autofill path — **built, uncommitted**
*Untracked, plus a modification to [scraper/autofill/browser.py](scraper/autofill/browser.py)*

**What it does:** An alternative application-autofill path that drives the Simplify Copilot Chrome
extension's own "Autofill this page" button, instead of the repo's own per-platform field fillers.
It works on whatever ATS platforms Simplify supports (broader than the hand-written fillers) and
inherits Simplify's resume parsing and AI question-answering.

**Tech:** Python, Playwright persistent Chrome context, shadow-DOM traversal.

**Notable engineering:**
- `launch_browser(allow_extensions=True)` strips Playwright's default `--disable-extensions` and
  `--disable-component-extensions-with-background-pages` flags so a real installed extension
  actually loads — off by default, since the native fillers don't want an extension racing them
  ([browser.py:30-54](scraper/autofill/browser.py#L30-L54)).
- Reaches the button inside Simplify's own open shadow root (`simplify-jobs-shadow-root`) via
  Playwright locators, which pierce open shadow DOM automatically. The module is explicit that it
  reuses none of Simplify's code and only drives the UI as a human would
  ([simplify_assist.py:5-15](scraper/autofill/simplify_assist.py#L5-L15)).
- **[advance.py](scraper/autofill/advance.py) is the genuinely careful piece** and is shared by
  both the native and Simplify fill paths. It clicks Next/Continue but *never* Submit/Apply/Finish;
  a button matching both patterns (e.g. "Continue to Submit") is treated as a submit button, full
  stop. After clicking it self-verifies the form actually advanced (URL changed, or the exact
  clicked button is gone) and returns `False` rather than retrying or guessing, handing control
  back to the human ([advance.py:22-46](scraper/autofill/advance.py#L22-L46)).
- The orchestrator never submits: it caps at 10 steps, stops on a visible CAPTCHA, leaves the
  browser open, and only marks the job `applied` in Supabase after the human confirms at a prompt
  ([autofill_simplify.py:103-112](scraper/autofill/autofill_simplify.py#L103-L112)).

### Data-integrity backfill tool
*Commit `42c9dd8` — [scraper/backfill_norm_keys.py](scraper/backfill_norm_keys.py)*

Covered under bug fixes below, but architecturally it's the pattern worth naming: `norm_key` is a
denormalized fingerprint computed once at insert time, so changing the normalization logic
silently strands every pre-existing row. The tool recomputes and reconciles them, is dry-run by
default (`--apply` to write), paginates 1,000 rows at a time, and touches only the `norm_key`
column — never `tier`/`status`/`reason` — so it cannot revert a job you marked applied or
reclassify anything.

---

## Bug fixes

### Stale dedup fingerprints silently letting duplicate jobs through
*Commit `42c9dd8`*

**The bug:** An earlier commit (`eaf58de`) reworked `norm_company()`/`norm_role()` for better
cross-source dedup. Because `norm_key` is computed at insert time and never revisited, every row
written before that change kept its old fingerprint forever — so an exact repost (same company and
title, new LinkedIn id) could fail to match the original and slip into the active queue as new.

**How it was found:** a real case — a GE Appliances co-op reposted 9 days later under a new id with
an identical title, where the stored `norm_key` on the older, already-applied row didn't match what
the current code computes. Git history confirmed `eaf58de` landed between the two postings.

**Outcome (from the commit's recorded backfill run):** 10 of 15,779 jobs had a stale `norm_key`, all
predating `eaf58de`, all corrected. The run also caught an unrelated false-positive collision where
two different Mujin US roles had landed on the same key.

### Pay-transparency boilerplate wrongly SKIPping big-tech internships
*Commit `afbf9be` — [Candidate_Profile_and_Filters.md](Candidate_Profile_and_Filters.md)*

**The bug:** A genuine 12–14 week Google SWE Intern posting was classified SKIP. Its
legally-mandated WA pay-transparency paragraph contained the phrase "this full-time position" and
an annualized $98K–$131K range — and the classifier rubric treated both as hard SKIP triggers. That
paragraph is copy-pasted verbatim across every Google posting regardless of employment type, and
six-figure annualized intern comp is normal at that tier of employer.

**The fix:** A carve-out in the rubric that distinguishes a standalone compensation-disclosure block
(recognizable by "in accordance with [state] law," "pay transparency," "base salary range,"
"individual pay is determined by") from language in the actual role narrative describing employment
terms. Only the latter triggers SKIP. It also explicitly instructs the classifier *not* to reason
"this salary seems too high to be a real internship," and lists the real full-time tells instead
(no duration/term language anywhere, career-ladder/leveling language, explicitly open-ended role).

**Outcome (per the commit message):** the Google posting reclassifies as APPLY, with the existing
regression suite showing no unrelated changes.

**Worth understanding before you discuss this one:** the fix is prompt/rubric engineering — a
Markdown file fed to the classifier as a cached system prompt — not a code change. That's a
legitimate and increasingly common form of LLM-application debugging, but describe it accurately;
`git show afbf9be` is a 2-line Markdown diff.

---

## ⚠️ Not shipped — do not claim as live

**1. The entire `scraper_beyonce/` pipeline is untracked and has never run against real
infrastructure.** This is the largest single body of work in the window and the easiest thing to
overclaim. Concretely:
- Not committed — every file is untracked; `modal_app_beyonce.py`, `.env.beyonce.example`, and
  `Beyonce_Candidate_Profile_and_Filters.md` too.
- `.env.beyonce` exists but `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, and `NTFY_TOPIC` are all
  **empty** (only `ANTHROPIC_API_KEY` is populated). It therefore cannot have completed a run,
  stored a job, or sent a notification.
- The Supabase project the schema targets doesn't appear to exist yet;
  [scraper_beyonce/schema.sql](scraper_beyonce/schema.sql) has not been applied anywhere.
- The `job-alert-secrets-beyonce` Modal secret and a `modal deploy modal_app_beyonce.py` are both
  prerequisites that the empty env vars suggest haven't happened.
- The fixture suite **has** now been run (Jul 28, 2026): 15/15 passing. That validates the
  classification rubric only — it calls `classify()` directly and touches no infrastructure, so it
  is not evidence the pipeline runs end-to-end.

  **Safe framing:** "designed and implemented a second pipeline instance with an inverted
  classification rubric and isolated infrastructure; pending deployment." Not "runs," "monitors,"
  or "sends alerts."

**2. The Simplify-assisted autofill path is uncommitted and has real fragility.** It's untracked
(three new modules plus a working-tree change to `browser.py`). It requires Simplify to be manually
installed and logged into the automation Chrome profile — the code does not install or configure it.
It matches the extension's button by the literal string `"Autofill this page"`, so a Simplify UI
change breaks it. And `wait_for_fill_to_settle()` is a fixed 3–4.5s sleep, not an event-based wait —
[the code says so itself](scraper/autofill/simplify_assist.py#L54-L60), because React-controlled
inputs don't reliably fire observable mutations. Describe this as a working prototype.

**3. ATS coverage is 50 companies, not "everything."** Six companies verified live during research
(Unity, Zoom, Etsy, GoodRx, Tempus, CrowdStrike) were deliberately left out because they're on
Workday, whose POST-based listing API isn't implemented
([ats_config.py:14-18](scraper/ats_config.py#L14-L18)). Workday support is a known gap, not done.

**4. The two SmartRecruiters companies (Canva, ServiceNow) get degraded classification.** That
endpoint returns no description, so those jobs are classified on title/company/location only
([ats_sources.py:136-138](scraper/ats_sources.py#L136-L138)). Fine, documented, but it means "full
description fetched in the listing call" is true for 48 of 50 companies, not all 50.

**5. The ATS watcher's live deployment status can't be confirmed from this repo.** The 3-minute cron
is defined in [modal_app.py:49-59](modal_app.py#L49-L59), but it only takes effect after a
`modal deploy modal_app.py`, and nothing in the working tree records whether that ran. The 14,294 →
36 → 3 numbers come from a *local* dry run with notifications suppressed, not from production. Worth
confirming before you say it's running in production.

**6. README and BuildSpec were not updated** for the ATS watcher. (Neither documents `github_watch`
either, so the docs lag both fast paths — a consistent gap rather than a new one.)

**7. Repo hygiene:** six ATS-verification scratch dumps are sitting untracked and un-ignored in the
repo root — `onepw.json` (1.9 MB), `riot_jobs.json`, `roblox_jobs.json`, `epic_jobs.json`, `s1.json`,
`wiz.json` (~2.9 MB total). They're raw job-board JSON, not credentials — but they should be deleted
or added to `.gitignore` before the next `git add`.

---

## Verification status of the numbers

| Claim | Source | Independently verified here |
|---|---|---|
| 50 companies; 38 Greenhouse / 10 Ashby / 2 SmartRecruiters / 0 Lever | `ats_config.py`, incl. its own `assert len == 50` | ✅ Yes, counted from source |
| 14,294 raw listings → 36 classified → 3 APPLY/MAYBE | Commit `5e255de` message, local dry run | ❌ Author's recorded run; no log in repo |
| SpaceX Fall 2026 internship found that LinkedIn missed | Commit `5e255de` message | ❌ Author's recorded run |
| 10 of 15,779 jobs had stale `norm_key` | Commit `42c9dd8` message | ❌ Author's recorded run; script defaults to dry run |
| Google posting now classifies APPLY | Commit `afbf9be` message | ❌ Author's recorded run |
| Beyoncé fixture suite 15/15 passing | Run here Jul 28, 2026 | ✅ Yes, executed directly |
| Beyoncé pre-filter no longer drops `Executive Assistant` / `Staff Assistant` / `Lead Patient Access Rep` | Fix applied Jul 28, 2026; probed before and after | ✅ Yes, verified both directions |
| LinkedIn syndication lag of 6–48+ hours | ATS vendors' own documentation, cited in `ats_watch.py` / `ats_sources.py` comments | ❌ **Vendor-documented, not measured in this repo** |

The last row is the one most likely to be challenged. The honest version is "bypasses a
vendor-documented 6–48 hour syndication lag," not "reduced job-alert latency from 48 hours to 3
minutes" — this repo contains no before/after latency measurement comparing the same posting caught
by both paths. If you want that number, it's measurable: compare `found_at` for `ats:`-prefixed rows
against the LinkedIn-sourced row for the same `norm_key`.
