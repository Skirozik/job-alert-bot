# Personal Job-Alert Bot — Build Spec
*Hand this to Claude Code along with `Candidate_Profile_and_Filters.md` and `LinkedIn_Searches.md`.*

> **For the AI building this:** Build **Phase 1 only** first and get it working end to end before anything else. When a decision is ambiguous, ask. Prefer the simplest thing that works. The decisions below are made on purpose, follow them.

---

## 1. What it is
A personal job-alert bot that watches LinkedIn (and a couple of free job feeds) for **new internships that match my profile**, filters out the noise, and pushes the good ones to my **phone and email** in near-real-time. Goal: replace the manual daily internship sweep entirely. It should never alert me about the same role twice, and never alert me about roles that are clearly not a fit.

**Core principle:** high signal. I'd rather get 5 great matches a day than 50 raw listings. The filter is as important as the scraper.

---

## 2. Decisions already made (do not relitigate)
- **Sources:** LinkedIn job searches converted to RSS via **RSS.app** (paste a LinkedIn search URL, get a live feed), plus the **Jobicy** free API/RSS (remote roles), plus optionally **Adzuna API** (free tier). See `LinkedIn_Searches.md` for the exact searches. Stay ToS-friendly: consume **RSS/API feeds, not authenticated LinkedIn scraping.**
- **Polling:** every **10 minutes** (RSS.app webhooks if available, otherwise a scheduled poll). 5-15 min is the practical window.
- **Dedup:** persist every job I've already been alerted about; never alert twice. (See §5.)
- **Fit filter:** only surface roles that pass my triage rubric (see `Candidate_Profile_and_Filters.md`). Auto-drop the SKIP patterns.
- **Smart classification:** for each new role, call the **Anthropic Claude API** with my profile + the listing to label it APPLY / MAYBE / SKIP and write a one-line "why it matched." This is the brain of the filter.
- **Phone alerts:** **ntfy.sh** (free, unlimited, simple HTTP POST, native iOS app). Telegram Bot API as an optional second channel.
- **Email:** a once-or-twice-daily **digest** via **Resend** (3,000/mo free tier), not one email per job.
- **Hosting:** a lightweight always-on poller on **Railway, Render, or Fly.io** free tier (or a cron job). No Heroku (free tier gone).
- **Cost target: $0/month.**

---

## 3. Tech stack
- **Language:** Python.
- **Feeds:** `feedparser` for RSS (RSS.app + Jobicy); `requests` for any JSON APIs (Adzuna/Jobicy).
- **Classifier:** Anthropic Claude API (`anthropic` SDK), small/cheap model for the fit-scoring call.
- **Storage:** SQLite (or a flat JSON/CSV) for `seen_jobs`. SQLite preferred.
- **Phone push:** `requests` POST to `ntfy.sh/<my-secret-topic>`.
- **Email:** Resend API (`requests` or their SDK).
- **Scheduler:** APScheduler in-process, or the host's cron, every 10 min.

---

## 4. Flow (per poll)
1. Pull every configured source (each LinkedIn-search RSS feed + Jobicy + any API).
2. Parse each listing into `{company, role, location, url, pay?, posted, source, raw_text}`.
3. **Normalize + dedup** against `seen_jobs` (see §5). Drop anything already seen.
4. For each genuinely new listing, run the **fit classifier** (Claude): returns `tier` (APPLY / MAYBE / SKIP) + `reason` + `suggested_resume`.
5. Drop SKIP. For APPLY and MAYBE:
   - Send an **ntfy push** immediately (format in §6).
   - Add it to the **email digest** queue.
6. Insert every processed listing into `seen_jobs` (even SKIPs, so they're not re-classified).
7. On a schedule (e.g. 8am and 6pm), send the **email digest** of that period's APPLY/MAYBE roles, then clear the queue.

---

## 5. Dedup (this is what makes it usable — get it right)
A role often appears under slightly different company/title strings across sources. Before treating a listing as new:
- **Normalize the company:** lowercase, strip suffixes/notations like `Inc`, `LLC`, `Ltd`, `Corp`, `Company`, `Industries`, `Technologies`, `Labs`, `Group`, `Electronics`, `Digital`, `'s`, and punctuation.
- **Normalize the role:** lowercase, strip season/year tags (`Fall 2026`, `Summer 2026`, `2027`), strip `Intern/Internship/Co-op` variants to a common token, collapse whitespace.
- **Dedup key:** `normalized_company + normalized_role`. Also keep a set of seen **URLs/job-ids** as a second key.
- Match new listings against **all** previously seen rows, not just recent ones.
- Keep a `dead_list` of companies/roles I've manually rejected (scams, bad fits) so they're never re-surfaced.

## 6. ntfy notification format
```
Title:  🟢 <Company> — <Role>
Body:   <Location> · <pay if known>
        Why: <one-line reason from classifier>
        Resume: <suggested variant>
Click:  <apply URL>   (ntfy "click" action opens the link)
Tags:   green_circle for APPLY, yellow_circle for MAYBE
```

## 7. Data model (SQLite)
**seen_jobs**: id, company, role, normalized_key, url, location, pay, source, tier, reason, suggested_resume, first_seen, notified (bool)
**config** (or a `config.yaml`): list of feed URLs, ntfy topic, resend key, schedule, my profile path.

---

## 8. Out of scope for Phase 1 (do NOT build yet)
- Email digest (Phase 2). Phase 1 = phone push only.
- Auto-applying to jobs. **Never auto-apply.** This bot only finds and alerts; I apply myself.
- A web dashboard, multi-user, anything fancy.

## 9. Build order
**Phase 1 (build first):** one LinkedIn-search RSS feed → parse → dedup against a SQLite table → classify with Claude → ntfy push for APPLY/MAYBE. Prove the loop end to end with one search.
**Phase 2:** add all the searches in `LinkedIn_Searches.md` + Jobicy; add the Resend email digest (8am/6pm).
**Phase 3:** tune the classifier prompt, add the dead-list, add suggested-resume mapping, deploy to Railway/Render on a 10-min schedule.

## 10. Compliance / safety
- Use **RSS/API feeds**, not logged-in LinkedIn scraping. Respect rate limits, add delays.
- **Never auto-apply or auto-message.** Alerts only.
- Keep the ntfy topic name secret (anyone with the topic can read your pushes).
- Keep API keys in environment variables, never committed.
