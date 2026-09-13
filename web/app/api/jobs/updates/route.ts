import { NextResponse } from 'next/server'
import { requirePersonaApi } from '@/lib/auth'
import type { Job } from '@/types/job'

export const dynamic = 'force-dynamic'

/* The cheap poll. Every 15 seconds the dashboard asks for rows FIRST SEEN
   since its cursor (found_at, stamped at insert), from EVERY source, and
   merges them client-side. It began as a LinkedIn-only fast path (1414b5e);
   the id filter that kept ATS and GitHub-tracker rows out was the feature's
   original remit, not a constraint — mergeGroupedJobs never cared where a
   row came from. Widening it is what lets the FULL refresh in JobList.tsx
   run every 30 minutes instead of every 5: a full refresh re-downloads
   ~6,300 rows (~4 MB raw) and was the bulk of the egress that put the
   project over its 5.5 GB cap on 2026-09-12; an empty delta response is ~40
   bytes.

   found_at CANNOT SEE A PROMOTION. A row parked as PENDING and promoted to
   APPLY by a later run keeps its original found_at (update_job_classification
   deliberately never touches it), so it is invisible to this query forever —
   found 2026-09-13 in review. That class travels through queueCount instead:
   every response carries the server-side count of actionable rows (a head
   request, zero row bytes), and the client answers a count it cannot explain
   with one full refresh. A promotion changes the count, so it surfaces within
   one poll rather than waiting for the 30-minute tick. */
const COLS_BASE = 'id,title,company,location,url,tier,reason,status,found_at,apply_url,is_easy_apply,salary,logo_url'
const COLS_FULL = `${COLS_BASE},suggested_resume`
// Must be at least JobList.tsx's FULL_POLL_MS. The delta covers a tab that was
// hidden for less than one full-refresh interval; the focus listener covers a
// longer absence with a full refresh. A shorter lookback here would leave a
// gap between the two where rows found while hidden appear only at the next
// tick, up to 30 minutes later.
const MAX_LOOKBACK_MS = 30 * 60_000
const LIMIT = 100

async function fetchUpdates(
  url: string,
  key: string,
  after: string,
  columns: string,
): Promise<{ response: Response, jobs: Job[] | null }> {
  const query = new URLSearchParams({
    select: columns,
    status: 'eq.new',
    tier: 'in.(APPLY,APPLY_CAVEAT)',
    found_at: `gt.${after}`,
    order: 'found_at.asc',
    limit: String(LIMIT),
  })
  const response = await fetch(`${url}/rest/v1/jobs?${query}`, {
    headers: { apikey: key, Authorization: `Bearer ${key}` },
    cache: 'no-store',
  })
  return { response, jobs: response.ok ? await response.json() : null }
}

export async function GET(request: Request) {
  const persona = await requirePersonaApi()
  if (!persona) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const raw = new URL(request.url).searchParams.get('after')
  const parsed = raw ? new Date(raw).getTime() : NaN
  if (!Number.isFinite(parsed)) {
    return NextResponse.json({ error: 'Invalid after timestamp' }, { status: 400 })
  }

  // A backgrounded tab can be days old. The full refresh reconciles that
  // case; cap this endpoint to the delta it is designed for so waking a phone
  // never downloads a second full dashboard.
  const after = new Date(Math.max(parsed, Date.now() - MAX_LOOKBACK_MS)).toISOString()
  // Capture before the query. A row committed while the query is running has a
  // later found_at and is guaranteed to be eligible on the next poll.
  const checkedAt = new Date().toISOString()

  let result = await fetchUpdates(persona.supabaseUrl, persona.serviceKey, after, COLS_FULL)
  if (!result.response.ok && result.response.status === 400) {
    // Beyonce/Hassan schemas omit suggested_resume; preserve the same graceful
    // fallback used by the full dashboard query.
    result = await fetchUpdates(persona.supabaseUrl, persona.serviceKey, after, COLS_BASE)
  }
  if (!result.response.ok || !result.jobs) {
    const detail = await result.response.text().catch(() => '')
    console.error(`[updates] persona=${persona.id} HTTP ${result.response.status}: ${detail.slice(0, 300)}`)
    return NextResponse.json({ error: 'Could not fetch updates' }, { status: 502 })
  }

  // A full page means there may be more. The client answers that with one
  // full refresh rather than paging here: it is rare (a burst of 100+ new
  // rows inside one lookback window), and the full refresh is the
  // reconciliation path anyway.
  const truncated = result.jobs.length >= LIMIT

  // The reconcile signal: how many actionable rows the server holds right
  // now. Zero row bytes (Range 0-0, count in a header). Omitted on failure —
  // the client just skips the check that poll.
  let queueCount: number | null = null
  try {
    const countRes = await fetch(
      `${persona.supabaseUrl}/rest/v1/jobs?status=eq.new&tier=in.(APPLY,APPLY_CAVEAT)&select=id&limit=1`,
      {
        headers: {
          apikey: persona.serviceKey, Authorization: `Bearer ${persona.serviceKey}`,
          Prefer: 'count=exact', Range: '0-0', 'Range-Unit': 'items',
        },
        cache: 'no-store',
      }
    )
    const total = Number(countRes.headers.get('content-range')?.split('/')[1])
    if (countRes.ok || countRes.status === 206) queueCount = Number.isFinite(total) ? total : null
  } catch { /* skip the check this poll */ }

  return NextResponse.json({ checkedAt, jobs: result.jobs, truncated, queueCount })
}
