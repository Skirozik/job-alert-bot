# Prompt for Claude Code — make the dashboard load fast

Copy everything below this line into Claude Code with the repo root open.

---

## Context

`web/` is the Next.js 14 (App Router) dashboard on Vercel: a password-gated, multi-persona job table reading three Supabase projects server-side (`lib/personas.ts`), rendered by one server component (`app/page.tsx`) that passes the full dataset into a client component tree (`JobList` → `JobTable`/`JobDrawer`). Scope: `web/` only, plus one optional SQL snippet run by hand in Supabase. Do not touch `scraper*/`, workflows, or `modal_app*.py`.

Two constraints are load-bearing and MUST NOT change (both were bought with a 6-hour debugging session — see `BUG_POSTMORTEM.md`):

- `app/page.tsx` stays `force-dynamic`, and every Supabase `fetch` keeps `cache: 'no-store'`. A cached render would serve one persona's jobs to another, and Next's data cache once served stale rows through the Supabase client.
- `middleware.ts` keeps exempting `/api/` — narrowing it is exactly the bug that silently killed every status write.

## The problem, located precisely

The dashboard's slowness is a payload problem, not a rendering problem. The client is already well-built — `JobTable` virtualizes its rows (`components/JobTable.tsx`, "visible slice pure arithmetic"), sorting/filtering are memoized, images go through `next/image`.

What's actually slow:

1. **Every page load ships megabytes of text the browser never displays.** `app/page.tsx` fetches with `select=*`, which includes `description` — up to **12,000 chars per row** (`scraper/linkedin.py` MAX_LEN; a live check once found 54.5% of stored descriptions maxing the old 4,000 cap, so these are not short). Grep the client: **no component or client lib reads `job.description`** — not `JobTable`, not `JobDrawer` (it renders title/company/reason/salary/links only), not the `jobView.ts` filters (search matches company+title only). The ONLY consumer is `lib/dupes.ts`, which shingles descriptions **server-side** during `groupNearDuplicates()`. So the descriptions are needed on the server and pure dead weight past it — yet they're serialized into the RSC payload AND the hydration data for every row, including every nested `duplicates[]` job. A comment in `JobList.tsx` records **2,262 rows mounted** on a real load; at a few KB of description each, that's easily 5–10 MB carried twice.
2. **That whole payload re-ships every 60 seconds.** `JobList.tsx` polls `router.refresh()` on a 60s interval plus every window focus — each one re-runs the full server fetch and re-streams the full RSC payload.
3. **First paint waits for everything.** There is no `app/loading.tsx`, so the user stares at a blank page for the entire Supabase roundtrip + render. (One real ineligible-bucket count in the code comments: 48,863 rows — these tables are not small.)
4. **`fetchPaged` walks pages serially** (`app/page.tsx`): 1000-row pages, each ~several MB with descriptions, one after another, before render can start.

## Step 0 — measure before touching anything

Run this from `scraper/` (it reuses the scraper's Python env and `../.env`; never commit its output — it would leak the project URL):

```python
# save as scraper/_measure_site_payload.py, run: python _measure_site_payload.py, DELETE after
import json
from dotenv import dotenv_values
from supabase import create_client
cfg = dotenv_values("../.env")
c = create_client(cfg["SUPABASE_URL"], cfg["SUPABASE_SERVICE_KEY"])
def n(b): return b(c.table("jobs").select("id", count="exact").limit(1)).execute().count
print("total:", n(lambda q: q))
print("tracked status!=new:", n(lambda q: q.neq("status", "new")))
print("active new+APPLY/CAVEAT:", n(lambda q: q.eq("status", "new").in_("tier", ["APPLY", "APPLY_CAVEAT"])))
print("ineligible new:", n(lambda q: q.eq("status", "new").eq("tier", "INELIGIBLE")))
full = c.table("jobs").select("*").eq("status", "new").in_("tier", ["APPLY", "APPLY_CAVEAT"]).order("found_at", desc=True).limit(300).execute().data
slim = [{k: v for k, v in r.items() if k != "description"} for r in full]
fb, sb = len(json.dumps(full)), len(json.dumps(slim))
print(f"300-row slice: with description {fb:,} B | without {sb:,} B | {fb/max(1,sb):.1f}x")
```

Report the numbers in your summary — they are the before/after evidence for step 1.

## Step 1 — stop shipping `description` to the browser (the big one)

In `app/page.tsx`, after `groupNearDuplicates(jobs)` (which must keep seeing full descriptions — the `containment(sh(i), sh(j))` gate in `lib/dupes.ts` requires them), strip `description` from every job **and from every job inside `duplicates[]`** before passing `initialJobs` to `JobList`:

- Add a small helper (e.g. `stripForClient(grouped: Grouped[]): Grouped[]`) that maps each job to `{ ...j, description: null }` and does the same to `j.duplicates`. Setting `null` (rather than deleting the key) keeps the `Job` type in `web/types/job.ts` untouched — `description` is already `string | null`, and the RULE comment in that file (columns must degrade safely across personas) stays satisfied.
- Verify with grep before AND after that nothing under `components/` or client-side `lib/` reads `.description` (as of now, only `lib/dupes.ts` does, and it runs server-side in `page.tsx`).
- Do NOT try to avoid fetching descriptions from Supabase — the server needs them for grouping, and Supabase→Vercel is the cheap hop. The expensive hop is server→browser, which is what this removes. This also automatically makes the 60-second `router.refresh()` poll ~10x cheaper, so leave the poll exactly as it is.

## Step 2 — `app/loading.tsx`

Add a minimal skeleton (sidebar rail + toolbar bar + a few gray table rows) using the existing CSS variables from `globals.css` (`--bg`, `--bg-surface`, `--border`, etc.) so the shell paints immediately while the server fetch streams. No new dependencies, no spinner libraries.

## Step 3 — parallelize `fetchPaged`

Keep the hard-won completeness semantics (the PAGE comment in `page.tsx` records how a silent 1000-row cap once hid 130 applied jobs), but stop paying for pages serially:

- First get the exact row count for the query (same `Prefer: count=exact` header trick as `fetchCount`, or read `content-range` from a `limit=1` request).
- Then fire ALL pages concurrently with `Promise.all`, concatenating **in page order** so ordering stays `found_at.desc` end to end.
- Keep the 50,000-row runaway guard and the "a page may be partial" tolerance. If the count request fails, fall back to the current serial loop rather than guessing.

## Step 4 (optional — only if step 0 shows big buckets)

If the tracked/active buckets are into the thousands, run this once in each persona project's Supabase SQL editor (it matches the dashboard's three hot filters; harmless if oversized):

```sql
create index if not exists jobs_status_found_idx on jobs (status, found_at desc);
create index if not exists jobs_new_tier_found_idx on jobs (tier, found_at desc) where status = 'new';
```

Don't add code for this — just note it in your summary as done/not-needed based on the measurements. (The `count=exact` on the ~48k ineligible bucket is one indexed count per load; leave it exact — the sidebar's "500 of 48,863" honesty depends on it.)

## Verify

- `npm run build` passes; the existing test scripts still pass — they are plain runnable scripts, not a runner suite: `node lib/__tests__/locations.test.mjs` and `node lib/__tests__/rowHeight.test.mjs` from `web/`. Add one test file in that same direct-run style for `stripForClient` (top-level job and nested `duplicates[]` both stripped, other fields untouched, input not mutated).
- Re-run the step-0 script's 300-row comparison and put before/after bytes in your summary.
- Manually confirm: drawer still shows reason/salary/links; duplicate grouping unchanged (server still sees descriptions); counts in the sidebar unchanged; status buttons still save (middleware untouched).
- `git diff` should show changes only in `web/`.

## What NOT to do

- No `revalidate`, no ISR, no caching of persona data (privacy — see constraints above).
- Don't change `lib/dupes.ts` thresholds or logic; don't move grouping to the client.
- Don't switch the poll to websockets/realtime — `NEXT_PUBLIC_*` is build-time-inlined and single-valued, which can't work multi-tenant (comment in `JobList.tsx` explains).
- Don't introduce component libraries, data-fetching libraries, or a state manager for this.
- Never print or commit `.env` values or Supabase URLs/keys; delete `_measure_site_payload.py` when done.

Work in order 0 → 1 → 2 → 3 → (4), and give me a per-file diff summary plus the before/after payload numbers at the end.
