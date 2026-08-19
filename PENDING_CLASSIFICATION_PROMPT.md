# Prompt for Claude Code — survive Anthropic API credit outages without losing jobs

Copy everything below this line into Claude Code (VS Code), with the repo root `LinkedIn_Job_Bot/` open.

---

## Context

This repo is a LinkedIn/ATS/GitHub internship scraper that classifies every new job with Claude Haiku (`scraper/classifier.py`, model `claude-haiku-4-5-20251001`) and pushes APPLY / APPLY_CAVEAT matches to my phone via ntfy. It runs headless on GitHub Actions: `scrape.yml` runs `scraper/main.py` every 20 min (20-min timeout), `watch_ats.yml` runs `scraper/ats_watch.py` every 15 min, `watch_github.yml` runs `scraper/github_watch.py` every 5 min. Both watchers import `process_job` from `scraper/main.py`, so all three paths share one classify/store/notify implementation. Storage is Supabase (`scraper/db.py`); the `jobs.tier` column is plain `text` with no CHECK constraint.

**Scope: the main pipeline only — `scraper/` and its workflows. Do NOT touch `scraper_beyonce/` or `scraper_hassan/`.** They are separate personas being left as-is for now. Also do not modify `web/` (reasoning below), `scraper/autofill/`, or `modal_app*.py`.

## The problem

When my Anthropic API credits run out, every `classify()` call fails for hours or days — until I notice and buy more credits. Current behavior on a failed classification, in `process_job()` (`scraper/main.py`):

```python
if result.get("failed"):
    log.warning("  Classification FAILED — not storing '%s' @ %s; the next run will retry it", ...)
    return False
```

The job is deliberately **not stored at all**. That was the correct fix for the 2026-08-04 outage (see the comment above `MAX_CLASSIFY_ATTEMPTS` in `classifier.py`: storing fallback verdicts buried 82 jobs as junk MAYBEs, 32 of which were real APPLYs, because dedup means a stored row is never looked at again). But "the next run will retry it" silently assumes the next run can *rediscover* the listing:

- LinkedIn jobs are only rediscoverable while they're inside `LOOKBACK_SECONDS = 21600` (6 h, `scraper/config.py`) **and** still ranked in the first 10 pages for one of my search terms. A credit outage longer than ~6 hours loses those jobs **permanently** — they were seen, their descriptions were even fetched, and then they evaporated.
- ATS and `gh:` jobs survive longer (they're re-listed on every poll while the posting is live), but every failing run burns 3 retry attempts × exponential backoff (~10–20 s) *per job*, over and over, for days.

## The goal

1. A job whose classification fails is **parked** in Supabase as `tier = 'PENDING'` — with everything already fetched (description, apply_url, salary, logo) preserved — instead of being dropped.
2. Every scheduled `main.py` run automatically retries parked jobs first. Once I've bought credits, the backlog drains itself within a run or two and each promoted APPLY/APPLY_CAVEAT job sends its normal ntfy push at that moment. No manual step, no new workflow.
3. When parking starts, I get ONE urgent ntfy canary telling me the classifier is down (throttled to at most one every 6 h while the outage lasts — not one every 20 minutes).
4. When credits fail, the run **fails fast**: after the first definitive billing/auth error, no more API calls are attempted that run — every remaining job parks immediately instead of burning 3 × backoff each.

`PENDING` is a queue state, not a verdict. The 2026-08-04 invariant stays sacred: **never store a model verdict that the model didn't actually produce.** A parked row must be impossible to confuse with a classified one, and must be guaranteed to get a real classification later.

## Read these before writing any code

`scraper/classifier.py`, `scraper/main.py`, `scraper/db.py`, `scraper/notifier.py`, `scraper/config.py`, `scraper/github_watch.py`, `scraper/ats_watch.py`, `scraper/digest.py`, `scraper/reclassify_skips.py` (the house pattern for "re-classify stored rows and update in place"), `README.md`, and — read-only, for the safety argument — `web/app/page.tsx`.

---

## Implementation

### 1. `scraper/classifier.py` — error taxonomy + circuit breaker

- Add a module-level breaker, e.g. `_API_HARD_DOWN: Optional[str] = None` (holds the failure kind once tripped).
- Extract error classification into a small pure function so it's unit-testable without constructing real SDK exceptions:

  ```python
  def _error_kind(exc: Exception) -> str:
      # "billing"   -> anthropic.BadRequestError whose message contains "credit balance"
      #                (Anthropic returns HTTP 400 "Your credit balance is too low..." when
      #                credits run out). Match case-insensitively on the substring; don't
      #                pin the full message text.
      # "auth"      -> anthropic.AuthenticationError / anthropic.PermissionDeniedError
      # "transient" -> everything else (rate limit, overloaded, connection, 5xx)
  ```

  Use `isinstance` checks against the `anthropic` SDK exception classes (repo pins `anthropic>=0.40.0` — verify the class names against the installed version before assuming).
- In `classify()`:
  - At the top: if `_API_HARD_DOWN` is set, return `_failed(...)` immediately — zero API calls, zero sleeps.
  - In the `except` block: compute `kind = _error_kind(exc)`. If `kind` is `"billing"` or `"auth"`, set the breaker and return `_failed(...)` **immediately — do not spend the remaining retry attempts**; retrying a billing error is pure wasted time. Only `"transient"` keeps the existing 3-attempt/backoff loop.
- `_failed()` grows a `failed_kind` key (`"billing" | "auth" | "transient" | "malformed"` — use `"malformed"` for the existing "no tool_use block" path). Keep the existing contract exactly: `failed: True`, placeholder tier, callers must check `failed` before reading `tier`.
- Update the big comment above `MAX_CLASSIFY_ATTEMPTS` and the module docstring: the "not stored at all / next run rediscovers it" story is being replaced by parking, and stale load-bearing comments in this repo are landmines. Rewrite them to describe the new design and keep the 2026-08-04 postmortem reference.
- The breaker is per-process; every scheduled run is a fresh process, so it can never stick past a run. No reset logic needed.

### 2. `scraper/db.py` — pending helpers

- `fetch_pending_jobs(limit: int) -> list[dict]` — `select("*").eq("tier", "PENDING").order("found_at", desc=False).limit(limit)`, oldest first (oldest are closest to application deadlines). Return `[]` on exception (log it), never raise — a DB blip must not kill the run.
- `update_job_classification(job_id: str, tier: str, reason: str, suggested_resume: str, salary: Optional[str]) -> bool` — `.update({...}).eq("id", job_id)`, following the exact pattern in `reclassify_skips.py`. Include `salary` in the payload **only when a non-empty value is passed**. Never touch `status`, `found_at`, `description`, `norm_key`, or `search_term`. Return True/False like `insert_job`.
- **Why an update function is required:** `insert_job()` upserts with `ignore_duplicates=True` (`ON CONFLICT DO NOTHING`) — calling it again for an existing row silently changes nothing. Promotion from PENDING must go through a real UPDATE. Add a one-line comment on `insert_job` noting this asymmetry.
- No schema migration is needed: `jobs.tier` is unconstrained `text`, and `jobs_tier_idx` already exists for the pending lookup.

### 3. `scraper/main.py` — park instead of drop

In `process_job()`, replace the early-return block with parking:

```python
if result.get("failed"):
    job["tier"] = "PENDING"
    job["reason"] = "Awaiting classification — Claude API unavailable when this job was found"
    job["suggested_resume"] = "General"   # placeholder; overwritten at promotion
    insert_job(job)
    log.warning("  Classification FAILED (%s) — parked as PENDING for automatic retry",
                result.get("failed_kind"))
    return False
```

- Storing the row (with the description that was already fetched a few lines up) puts it in the dedup index, which is now a **feature**: no re-fetching, no re-discovery dependency, and the retry path reads it back from the DB. The description survives even if the LinkedIn posting later expires.
- Never push a notification for a PENDING park.
- `process_job` is imported by `ats_watch.py` and `github_watch.py`, so both fast paths get parking for free — do not add any retry logic to those two files (they must stay fast), and do not break the existing `from main import process_job, _is_senior_role, _is_new_grad_role, _is_non_internship_title` imports.
- `process_job` (or its caller) needs to surface "I parked one, and with what kind" to `run()` for the canary — a small module-level counter dict or an extended return is fine; keep it simple and keep the bool-ish contract the watchers rely on (they only truth-test the return for "notified").

### 4. `scraper/main.py` — automatic retry pass in `run()`

Add a `retry_pending()` step inside `run()`, **after** the run-lock is acquired and **before** the LinkedIn search loop (inside the existing `try`, so `finish_run()` in the `finally` still executes):

- Fetch up to `RETRY_PENDING_MAX = 40` pending rows. Rationale for 40: the scrape itself takes ~50–70 s of a 20-minute Actions timeout; 40 classifications at ~2–3 s each adds ≤ 2–3 min, and a multi-day backlog still drains at ~120 jobs/hour across scheduled runs.
- For each row, build the job dict from the stored columns (`classify()` needs `id`, `title`, `company`, `location`, `description`; the `gh:` id prefix must survive so `_never_skip_github_sourced` still applies) and call `classify(job)`.
  - **Success** → `update_job_classification(...)`; pass salary only if the stored row has none and the result produced one. If the new tier is APPLY or APPLY_CAVEAT **and the update succeeded**, call `push_job()` with the merged row — same store-first-then-notify ordering as `process_job`, and the push the user would have gotten at discovery time now fires at promotion time.
  - **Failed with `failed_kind` in ("billing", "auth")** → the breaker just tripped; break out of the retry loop entirely. Everything left simply stays PENDING for the next run. (While credits are out, this costs exactly one failed API call per 20-min run.)
  - **Failed with any other kind** → skip this row (leave it PENDING), continue with the next, but break after 2 consecutive such failures. The skip-and-continue matters: a single poison row (e.g. a response that never yields a tool_use block) sits at the head of the oldest-first queue forever and must not block the drain behind it.
- Log a summary line: `Pending retry: N attempted, X promoted (Y notified), Z still pending`.
- **Do NOT add new keys to the `finish_run(**stats)` call.** `scrape_runs` has exactly `total_raw / new_jobs / notified / rate_limited`; an unknown column makes the whole UPDATE fail inside `finish_run`, the row stays unfinished, and `start_run()`'s 20-minute lock then blocks the next scheduled run. Log-only for the new counters. (Promoted-job pushes MAY be added into the existing `notified` count — that column means "pushes sent this run".)

### 5. Canary alerts (`notifier.push_canary`), throttled via `bot_state`

- **Down alert:** at the end of `run()`, if ≥1 job was parked this run for kind `"billing"` or `"auth"`, send one `push_canary` — but first check a `bot_state` row (key e.g. `classifier_down_alert_at`, ISO timestamp; same read/upsert pattern as `digest.py`'s `last_digest_sent_at`) and only send if there's no timestamp or it's older than 6 hours, then upsert the new timestamp. Message shape:
  `"Claude classifier is DOWN (billing) — parked 12 job(s) this run, 37 waiting. They'll classify automatically once the API is back. Top up: console.anthropic.com"`
  Parking for purely transient kinds should not fire this alert (a one-off network blip self-heals in 20 minutes and isn't worth a 3 a.m. push).
- **Recovery note:** in `retry_pending()`, if ≥1 job was promoted AND the `classifier_down_alert_at` marker exists, send one `push_canary` (e.g. `"Classifier recovered — 37 parked job(s) classified, 9 APPLY pushed."`) and delete/clear the marker so (a) the next outage alerts fresh and (b) subsequent drain runs don't re-announce. The `bot_state` table already exists (see README).
- `push_canary` degrades to a no-op without `NTFY_TOPIC` — already handled, don't duplicate the guard.

### Why no `web/` changes are needed (leave it alone, but verify this claim)

`web/app/page.tsx` fetches exactly three sets: `status=neq.new`, `status=eq.new&tier=in.(APPLY,APPLY_CAVEAT)`, and `status=eq.new&tier=eq.INELIGIBLE`. A PENDING row is always `status='new'`, so it matches none of them — parked jobs are simply invisible to the dashboard, which is the safe behavior (nothing mislabeled, nothing actionable-looking). The `Tier` union in `web/types/job.ts` therefore never receives the new value. Confirm this reasoning against the actual queries before finishing, and mention it in your summary.

### Docs

Update `README.md`'s "How it works" flow block (add the parking + retry-pending steps) and fix the schema comment `-- APPLY | MAYBE | SKIP`, which is two vocabularies stale (current tiers: APPLY / APPLY_CAVEAT / INELIGIBLE, now plus PENDING as a queue state). Keep the public-repo logging rule everywhere: never log `reason` text or profile details — job ids only.

---

## Edge cases to handle (or consciously accept)

1. **Outage dies mid-run:** jobs earlier in the same run classified fine; later ones park. Fine by construction — parking is per-job.
2. **DB write fails during parking:** `insert_job` returns False → the job is lost exactly as today's behavior loses it (rediscovery might still save it). Acceptable; just log it. Don't build a retry-of-the-park.
3. **Description was never fetchable:** stored `description` is NULL → retry classifies on title/company/location, which `classify()` already supports via its "(not available…)" prompt branch. Do not re-fetch from LinkedIn during retry (keeps the retry pass API-only and fast).
4. **Digest window:** `digest.py` selects APPLY/APPLY_CAVEAT by `found_at >= last-send watermark`. A job parked for longer than one digest period will miss the email when promoted, because promotion deliberately does NOT touch `found_at` (it means "first seen" and drives dashboard ordering/date filters — never mutate it). Accepted trade-off: the ntfy push at promotion is the primary channel. Note it in a comment; do not "fix" it.
5. **A human never sees PENDING rows** (dashboard blindness above), so nothing can change their `status` while parked; but write `update_job_classification` to not touch `status` anyway, mirroring `reclassify_skips.py`.
6. **Overrides at retry time:** promotion goes through the full `classify()` pipeline, so all deterministic overrides (full-time, school-specific, advanced-degree, non-US, gh-never-skip) apply identically to a job classified 3 days late. No special-casing.
7. **`github_watch.py` records the tracker commit SHA as seen *before* processing** — previously, a classification failure there could only be healed by the next full `main.py` scan re-reading the README. With parking, that window closes too. No change needed in `github_watch.py`; just don't regress it.

## Tests — `scraper/test_pending_queue.py`

Match the repo's existing plain-script test style (see `test_school_override.py` / `test_current_classifications.py`): a runnable `python test_pending_queue.py`, plain asserts, no pytest dependency, **no network and no real keys** — stub by module-attribute assignment (`main.classify = fake`, `main.insert_job = fake`, etc.).

Cover at minimum:

1. `_error_kind()` mapping: billing (BadRequestError + "credit balance" in message, any casing), auth, transient. Use the real SDK classes if cheaply constructible; otherwise subclass/instantiate minimal fakes and `isinstance`-test through the seam you designed.
2. Breaker: after one billing failure, a second `classify()` call returns failed **without** any client call (assert the stubbed client was called exactly once) and without sleeping.
3. Billing error does not burn the 3-attempt retry loop (client called once, no backoff sleeps).
4. `process_job` on failed classification: inserts with `tier == "PENDING"`, preserves the fetched description, sends no push, returns False.
5. `retry_pending` happy path: pending rows + stubbed classify success → `update_job_classification` called with the new tier, push fired for APPLY, no push for INELIGIBLE, salary passed only when the row lacked one.
6. `retry_pending` billing failure on first row → loop stops, zero updates, zero pushes, rows untouched.
7. Poison row (kind `"malformed"`) → skipped, loop continues to the next row; loop stops after 2 consecutive failures.
8. Canary throttle: parked-with-billing + no marker → canary sent + marker written; marker fresh (< 6 h) → no second canary. Recovery: promotion with marker present → recovery canary + marker cleared.

Then a live smoke test I can run myself (document it in your summary, don't automate it): set `ANTHROPIC_API_KEY` to garbage in `.env`, run `python main.py` from `scraper/` → expect PENDING rows + one down-canary (kind auth); restore the key, run again → expect promotions + pushes + recovery canary. Warn me that this writes to the real Supabase and sends real ntfy pushes.

## Acceptance checklist

- [ ] Credit exhaustion mid-outage: every discovered job ends up as a PENDING row with its fetched description; zero jobs dropped; run completes well inside the 20-min timeout (breaker = at most one failed API call per job-batch, no 3× backoff per job).
- [ ] After credits return: within one scheduled run, up to 40 parked jobs get real classifications; APPLY/APPLY_CAVEAT push to ntfy at promotion; backlog fully drains over subsequent runs with no human action.
- [ ] Exactly one down-canary per ~6 h of outage; exactly one recovery canary per outage.
- [ ] No stored fake verdicts anywhere — `_failed()` contract intact, PENDING never renders in the dashboard's actionable views (verified against `web/app/page.tsx` queries).
- [ ] `finish_run()` still receives only its four existing stat keys; run-lock behavior unchanged; `ats_watch.py` / `github_watch.py` untouched except for behavior inherited through `process_job`.
- [ ] `scraper_beyonce/`, `scraper_hassan/`, `web/`, `modal_app*.py` have zero diffs.
- [ ] All comments/docstrings that described "failed jobs are not stored" are rewritten to describe parking (they are load-bearing in this repo); README flow updated.
- [ ] `python test_pending_queue.py` passes offline.

Work through this in order (classifier → db → process_job → retry_pending → canaries → tests → docs), and show me a diff summary per file when you're done.
