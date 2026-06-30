# LinkedIn Searches to Monitor
*Each search below becomes one feed/query the bot polls. Dedup handles the overlap between them, so it's fine that they catch some of the same roles.*

---

## LinkedIn URL parameter cheat sheet
- `keywords=` the search terms (URL-encoded, spaces = `%20`)
- `f_E=1` → **Internship** experience level (add this to cut non-intern noise)
- `f_TPR=r7200` → posted in the **last 2 hours** (`r` + seconds)
- `f_WT=2` → **Remote** (1=on-site, 3=hybrid; combine like `f_WT=1,2,3`)
- `geoId=103644278` → **United States**; for Atlanta set location in UI and copy the geoId (Atlanta metro ≈ `geoId=90000052`, verify)
- Guest endpoint: `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={q}&location={loc}&f_TPR=r7200&f_E=1&start=0`

> Keep `f_TPR` at 2h so cron drift doesn't make you miss anything; dedup stops repeats.

---

## Search terms (run each × each location)
```python
SEARCH_TERMS = [
    "software engineer intern",
    "iOS mobile engineer intern",
    "AI engineer intern",
    "full stack intern",
    "python developer intern",
]
LOCATIONS = ["United States", "Atlanta, GA"]
```

## Optional extra terms (add if request budget allows)
- "frontend intern" / "react developer intern"
- "swift developer intern"
- "backend developer intern"
- "applied AI intern" / "LLM intern"
- "software engineer co-op"

---

## Non-LinkedIn sources to also feed in (free, no login)
- **Jobicy API/RSS** — remote roles, daily, free, no key.
- **Adzuna API** — free tier, US coverage, same keywords.
- **Bonus (advanced):** watch commits on the curated repos — `SimplifyJobs/Summer2026-Internships`, `vanshb03/Summer2027-Internships`, `speedyapply/2026-SWE-College-Jobs`, `speedyapply/2026-AI-College-Jobs`.

## Reminder
Run every listing through the fit filter in `Candidate_Profile_and_Filters.md` before alerting. These searches cast a wide net on purpose; the classifier keeps the signal high.
