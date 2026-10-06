// Server-side job queries shared by the page render and the backlog endpoint.
// NODE RUNTIME ONLY: these carry a persona's service key.
import type { Job } from '@/types/job'

if (typeof window !== 'undefined') {
  throw new Error('lib/jobQueries.ts was pulled into a client bundle — it uses service keys')
}

// PostgREST caps every response at 1000 rows. A query without an explicit
// limit does NOT error when it exceeds that — it silently returns the first
// 1000, so the shortfall is invisible from the response alone.
//
// This bit us: once the tracked set (applied/saved/dismissed) grew past 1000,
// 130 applied jobs stopped appearing on the dashboard. They were never
// deleted; ordering is found_at.desc, so the OLDEST tracked rows were the ones
// silently dropped, and the history looked like it had been trimmed.
//
// Any query that can legitimately exceed 1000 rows must page through.
const PAGE = 1000

/**
 * How far back the first render reaches for jobs still awaiting review. Older
 * review rows, and every dismissed row, arrive a moment later from
 * /api/jobs/backlog. Measured 2026-10-06 on the main persona: the full set was
 * ~9,300 rows and a 7.7 MB page; the recent window plus active tracking is
 * about a quarter of that, and it is what the default view shows anyway.
 */
export const REVIEW_WINDOW_DAYS = 14

/**
 * The columns the dashboard actually renders — everything EXCEPT description.
 *
 * description is up to 12,000 chars and is 90% of the compressed bytes leaving
 * Supabase: 4.38 MB of a 4.86 MB refresh. Nothing displays it. Duplicate
 * grouping uses canonical application targets plus compact company/title/
 * location guards, so descriptions stay out of both the query and payload.
 *
 * norm_key, search_term and posted_at are omitted too — nothing reads them.
 *
 * NOT a single fixed list, because the personas' schemas differ: only the
 * original has suggested_resume (scraper_beyonce/schema.sql and
 * scraper_hassan/schema.sql omit it). PostgREST answers a request for a
 * missing column with a hard 400, not a silent omission — verified — and
 * fetchJobs returns [] on a failed response, so one wrong column name would
 * render an entire persona's dashboard empty. Hence the fallback below rather
 * than a list that has to be kept in sync by hand.
 */
export const COLS_BASE = 'id,title,company,location,url,tier,reason,status,found_at,apply_url,is_easy_apply,salary,logo_url'
export const COLS_FULL = `${COLS_BASE},suggested_resume`

/** Swap `select=*` for the explicit list, leaving every other param alone. */
export function slimSelect(query: string, cols: string): string {
  return query.replace(/(^|&)select=\*/, `$1select=${cols}`)
}

async function fetchPagedSerial(url: string, key: string, query: string, personaId: string): Promise<Job[]> {
  const all: Job[] = []
  for (let offset = 0; ; offset += PAGE) {
    const page = await fetchJobs(url, key, `${query}&limit=${PAGE}&offset=${offset}`, personaId)
    all.push(...page)
    if (page.length < PAGE) return all
    // Runaway guard: 50k rows is far beyond any real persona's tracked set.
    if (all.length >= 50_000) return all
  }
}

/**
 * The row-selecting parts of a query, minus anything about shape or paging.
 *
 * fetchCount builds its own `select=id&limit=1`, so handing it a query that
 * already carries `select=*` and `order=...` would send duplicate params —
 * PostgREST would pick one, and a count request that quietly returns full rows
 * is exactly the kind of failure this file has been bitten by before.
 */
function filtersOnly(query: string): string {
  return query
    .split('&')
    .filter((p) => !/^(select|order|limit|offset)=/.test(p))
    .join('&')
}

/**
 * Every page at once, instead of one after another.
 *
 * The serial version could not start page 2 until page 1 had fully arrived, so
 * a three-page bucket cost three sequential Supabase roundtrips before render
 * could begin. Asking for the count first turns that into one count request
 * plus N concurrent pages.
 *
 * Completeness semantics are unchanged, and they are load-bearing: PostgREST
 * caps a response at 1000 rows WITHOUT erroring, which is how 130 applied jobs
 * silently vanished from this dashboard once (see the PAGE comment above).
 *
 *  - Pages are concatenated IN ORDER, so the result stays found_at.desc.
 *  - A short page is tolerated rather than treated as the end, because here
 *    the pages are requested up front rather than discovered by walking.
 *  - If the count request fails we fall back to the serial walk instead of
 *    guessing how many pages exist. Guessing is what loses rows.
 */
export async function fetchPaged(url: string, key: string, query: string, personaId: string): Promise<Job[]> {
  const total = await fetchCount(url, key, filtersOnly(query), personaId)
  if (total === null) return fetchPagedSerial(url, key, query, personaId)
  if (total === 0) return []

  const capped = Math.min(total, 50_000)     // same runaway guard as the serial path
  const pages = Math.ceil(capped / PAGE)
  if (pages <= 1) {
    return fetchJobs(url, key, `${query}&limit=${PAGE}&offset=0`, personaId)
  }

  const results = await Promise.all(
    Array.from({ length: pages }, (_, i) =>
      fetchJobs(url, key, `${query}&limit=${PAGE}&offset=${i * PAGE}`, personaId)
    )
  )
  return results.flat()
}

/**
 * Exact row count for a filter, without downloading the rows.
 *
 * Used by fetchPaged to learn how many pages to request up front, so they can
 * be fetched concurrently rather than discovered by walking. Returns null when
 * the server declines to count, which is a valid response and must not be
 * mistaken for zero — fetchPaged falls back to the serial walk on null.
 */
async function fetchCount(url: string, key: string, filter: string, personaId: string): Promise<number | null> {
  try {
    const res = await fetch(`${url}/rest/v1/jobs?select=id&limit=1&${filter}`, {
      headers: {
        apikey: key,
        Authorization: `Bearer ${key}`,
        Prefer: 'count=exact',
      },
      cache: 'no-store',
    })
    if (!res.ok) return null
    // "0-0/48863" — the total is after the slash. "*" means the server declined
    // to count, which is a valid response and must not render as a number.
    const total = (res.headers.get('content-range') || '').split('/')[1]
    return total && total !== '*' ? Number(total) : null
  } catch (err) {
    console.error(`[jobs] count failed for persona "${personaId}":`, err)
    return null
  }
}

/**
 * One request. `retryOnMissingColumn` handles the persona-schema difference:
 * only the original project has suggested_resume, and PostgREST answers a
 * request for a missing column with a 400 rather than omitting it. On that
 * specific failure the query is retried with the base column list, so a
 * persona whose schema lacks the column degrades to a missing Resume badge —
 * which types/job.ts already models as optional — instead of an empty
 * dashboard.
 */
async function fetchJobs(
  url: string, key: string, query: string, personaId: string,
  retryOnMissingColumn = true,
): Promise<Job[]> {
  const res = await fetch(`${url}/rest/v1/jobs?${query}`, {
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
    },
    cache: 'no-store',
  })
  if (!res.ok) {
    if (retryOnMissingColumn && res.status === 400 && query.includes(',suggested_resume')) {
      return fetchJobs(url, key, query.replace(',suggested_resume', ''), personaId, false)
    }
    // Previously this returned [] silently. With one known-good credential
    // that was survivable; with several, a wrong service key or URL renders an
    // indistinguishable "No jobs here" empty state. Log it.
    console.error(
      `[jobs] fetch failed for persona "${personaId}": HTTP ${res.status} ${res.statusText} — ${(
        await res.text().catch(() => '')
      ).slice(0, 300)}`
    )
    return []
  }
  return res.json()
}

/** The ISO cutoff splitting the first render from the backlog. */
export function reviewCutoff(now = Date.now()): string {
  return new Date(now - REVIEW_WINDOW_DAYS * 86_400_000).toISOString()
}

/**
 * What the first render needs: every job you have acted on and are still
 * tracking (saved, applied and later outcomes), plus jobs awaiting review that
 * were found on or after `cutoff`. Dismissed and archived rows are excluded
 * here; dismissed ones come with the backlog, archived ones never load.
 */
export function fetchHead(url: string, key: string, cutoff: string, personaId: string) {
  return Promise.all([
    fetchPaged(url, key, slimSelect('select=*&status=not.in.(new,dismissed,archived)&order=found_at.desc', COLS_FULL), personaId),
    fetchPaged(url, key, slimSelect(`select=*&status=eq.new&tier=in.(APPLY,APPLY_CAVEAT)&found_at=gte.${cutoff}&order=found_at.desc`, COLS_FULL), personaId),
  ]).then(([tracked, review]) => [...tracked, ...review])
}

/**
 * Everything the first render left out, except archived rows: review jobs
 * found before `cutoff`, and every dismissed job. Together with fetchHead this
 * is exactly the set the dashboard loaded in one go before the split.
 */
export function fetchBacklog(url: string, key: string, cutoff: string, personaId: string) {
  return Promise.all([
    fetchPaged(url, key, slimSelect(`select=*&status=eq.new&tier=in.(APPLY,APPLY_CAVEAT)&found_at=lt.${cutoff}&order=found_at.desc`, COLS_FULL), personaId),
    fetchPaged(url, key, slimSelect('select=*&status=eq.dismissed&order=found_at.desc', COLS_FULL), personaId),
  ]).then(([review, dismissed]) => [...review, ...dismissed])
}
