# Debugging a Silent Data Persistence Bug in a Full-Stack Next.js App

## The Problem

I built a job application tracker dashboard — a Next.js frontend backed by Supabase (PostgreSQL). Users can mark jobs as Applied, Dismissed, or Saved. The issue: no matter how many times I clicked those buttons, refreshing the page reverted everything back. It looked like nothing was saving.

I spent roughly 6 hours on this.

---

## Initial Assumptions (All Wrong)

My first instinct was that the database was broken or down. I checked Supabase — it was up, responding fine. I also suspected the API route itself was crashing, but there were no visible errors in the UI. The buttons showed a green success toast every time I clicked them, which made me think the save was working and something else was resetting the data.

That led me to suspect the scraper. The app runs a Python scraper every 30 minutes that pulls new jobs from LinkedIn and writes them to the database. My theory was that the scraper was overwriting the `status` column on each run. I dug into the upsert logic — it was using `ON CONFLICT DO NOTHING`, meaning it would skip rows that already existed. So the scraper wasn't touching existing statuses.

I was stuck. The database was up, the API appeared to succeed, and the scraper wasn't the culprit.

---

## The Breakthrough — Testing the API Directly

Instead of relying on what the browser was showing me, I tested the API endpoint directly with `curl`:

```bash
curl -s -o /dev/null -w "%{http_code} %{redirect_url}" \
  -X PATCH "https://my-app.vercel.app/api/jobs/123/status" \
  -H "Content-Type: application/json" \
  -d '{"status":"applied"}'
```

Output:
```
307 https://my-app.vercel.app/login
```

The API was returning a **307 redirect to the login page** — not running at all.

---

## Bug 1 — Auth Middleware Silently Killing Every Write

The dashboard has password protection. I had a Next.js middleware that checked for an auth cookie on every request. The middleware was supposed to exempt the auth route, but the exemption was too narrow:

```typescript
// What it was
if (pathname.startsWith('/login') || pathname.startsWith('/api/auth')) {
  return NextResponse.next()
}
// Everything else — including /api/jobs/[id]/status — got redirected to /login
```

So every PATCH to the status endpoint hit the middleware, got a 307 redirect to `/login`, and the browser followed it. The login page returned `200 OK` with HTML. The `fetch()` call in the frontend saw a 200 and treated it as success — firing the success toast. But the actual database write never happened.

**The fix was one line:**

```typescript
if (pathname.startsWith('/login') || pathname.startsWith('/api/')) {
  return NextResponse.next()
}
```

After deploying this, direct API calls started returning real responses. But there was still a second problem.

---

## Bug 2 — Page Serving Stale Data on Every Refresh

Even after fixing the writes, the applied job count on the page was still wrong — showing 7 when the database had 10. I'd hard-refresh and get the same stale number.

I queried the database directly and confirmed it had the right data:

```bash
curl "https://<supabase-url>/rest/v1/jobs?select=id,status&status=eq.applied" \
  -H "apikey: <service_key>" | count
# → 10
```

The database was correct. The page wasn't loading it fresh.

The page was marked `force-dynamic` to opt out of Next.js caching, but the Supabase JS client's internal `fetch` calls were still being captured by Next.js's data cache underneath. The `force-dynamic` flag doesn't always propagate to third-party clients.

**The fix:** replaced the Supabase JS client call with a raw `fetch()` and explicit `cache: 'no-store'`:

```typescript
const res = await fetch(
  `${url}/rest/v1/jobs?select=*&order=found_at.desc&limit=500`,
  {
    headers: { apikey: key, Authorization: `Bearer ${key}` },
    cache: 'no-store',
  }
)
```

After this deployed, the page loaded live data on every refresh.

---

## Why It Took So Long

Both bugs together created a false picture:

- **Bug 1** made every save silently fail but show a success toast — so I believed saves were working
- **Bug 2** made the page show stale data on refresh — which looked like confirmation that saves weren't persisting

Neither bug produced a visible error. The failure looked like a database or scraper problem when the database was fine the entire time. The key turning point was bypassing the browser entirely and testing the API directly with `curl`, which immediately revealed the 307 redirect that the browser had been silently following.

---

## Key Takeaways

- **Test the API independently of the UI.** The browser was hiding the redirect and making the failure invisible. A single `curl` call exposed it immediately.
- **Abstractions can hide caching behavior.** `force-dynamic` in Next.js didn't prevent the Supabase client from caching. When behavior is unexpected, go one layer lower — raw `fetch` with explicit options is unambiguous.
- **When multiple things seem broken, look for a single root cause.** What looked like a database bug, a scraper bug, and a frontend bug was actually two small configuration mistakes compounding each other.
