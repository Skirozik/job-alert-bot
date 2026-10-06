import { NextResponse } from 'next/server'
import { requirePersonaApi } from '@/lib/auth'
import { groupNearDuplicates } from '@/lib/dupes'
import { fetchBacklog, reviewCutoff } from '@/lib/jobQueries'

export const dynamic = 'force-dynamic'

/* The second half of the dashboard's data. page.tsx renders tracked jobs and
   review jobs found since `before`; this returns the rest -- older review jobs
   and every dismissed job, already grouped -- which JobList fetches right
   after the first paint and appends. `before` is the cutoff the page itself used, so the two halves
   meet exactly. Archived rows (scraper/archive_stale.py) load from neither. */
export async function GET(request: Request) {
  const persona = await requirePersonaApi()
  if (!persona) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const raw = new URL(request.url).searchParams.get('before')
  const parsed = raw ? Date.parse(raw) : NaN
  // A missing or garbled cutoff falls back to the server's own, so a stale
  // client still gets a complete set (an overlap with the page is harmless:
  // the client merges by id).
  const before = Number.isFinite(parsed) ? new Date(parsed).toISOString() : reviewCutoff()

  const rows = await fetchBacklog(persona.supabaseUrl, persona.serviceKey, before, persona.id)
  // Grouped HERE, like page.tsx groups the first half. Regrouping ~7k rows in
  // the browser measured 1.4-2.5 s on a desktop (mergeGroupedJobs re-groups the
  // whole list), which would freeze the page right after it appeared. The client
  // appends these groups instead. A duplicate pair straddling the cutoff shows
  // as two rows -- the price of not regrouping everything client-side.
  rows.sort((a, b) => new Date(b.found_at).getTime() - new Date(a.found_at).getTime())
  return NextResponse.json({ before, jobs: groupNearDuplicates(rows) })
}
