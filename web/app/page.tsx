import { JobList } from '@/components/JobList'
import { requirePersonaPage } from '@/lib/auth'
import { groupNearDuplicates, type Grouped } from '@/lib/dupes'
import type { Job } from '@/types/job'

// Must stay force-dynamic. A cached render would serve one person's jobs to
// another.
export const dynamic = 'force-dynamic'

// PostgREST caps every response at 1000 rows. A query without an explicit
// limit does NOT error when it exceeds that — it silently returns the first
// 1000, so the shortfall is invisible from the response alone.
//
// This bit us: once the tracked set (applied/saved/dismissed) grew past 1000,
// 130 applied jobs stopped appearing on the dashboard. They were never
// deleted; ordering is found_at.desc, so the OLDEST tracked rows were the ones
// silently dropped, and the history looked like it had been trimmed.
//
// Any query that can legitimately exceed 1000 rows must page through. The
// INELIGIBLE query is exempt because its limit=500 is a deliberate cap, not an
// accident.
const PAGE = 1000

/**
 * The columns the dashboard actually renders — everything EXCEPT description.
 *
 * description is up to 12,000 chars and is 90% of the compressed bytes leaving
 * Supabase: 4.38 MB of a 4.86 MB refresh. Nothing displays it. Its only
 * consumer was containment() in lib/dupes.ts, which grouped near-identical
 * postings; that grouping is deliberately switched off until it can be
 * precomputed rather than recalculated from full text on every single load.
 * The visible cost is that a job posted to two sources now shows as two rows.
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
const COLS_BASE = 'id,title,company,location,url,tier,reason,status,found_at,apply_url,is_easy_apply,salary,logo_url'
const COLS_FULL = `${COLS_BASE},suggested_resume`

/** Swap `select=*` for the explicit list, leaving every other param alone. */
function slimSelect(query: string, cols: string): string {
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

async function fetchPaged(url: string, key: string, query: string, personaId: string): Promise<Job[]> {
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

/**
 * Drop `description` before the data crosses to the browser.
 *
 * Descriptions are up to 12,000 chars each (scraper/linkedin.py MAX_LEN) and
 * NOTHING on the client reads them: not JobTable, not JobDrawer, not the
 * jobView filters (search matches company + title only). The sole consumer is
 * groupNearDuplicates in lib/dupes.ts, which shingles them SERVER-side and has
 * already run by the time this is called.
 *
 * Measured on this data: a 300-row slice is 1,739,434 B with descriptions and
 * 276,390 B without — 6.3x. That weight was being serialised twice, once into
 * the RSC payload and again into the hydration data, for every row AND every
 * nested duplicates[] entry. It also re-shipped on every 60s router.refresh().
 *
 * Sets null rather than deleting the key: `description` is already
 * `string | null` in types/job.ts, so the Job type stays untouched and the
 * "columns must degrade safely across personas" rule there still holds.
 *
 * Returns new objects — never mutates the grouped array, which the server
 * still holds a reference to.
 */
function stripForClient(grouped: Grouped[]): Grouped[] {
  return grouped.map((j) => ({
    ...j,
    description: null,
    duplicates: j.duplicates?.map((d: Job) => ({ ...d, description: null })),
  }))
}

export default async function HomePage() {
  const persona = await requirePersonaPage()
  const { supabaseUrl: url, serviceKey: key, id } = persona

  // Two buckets, both loaded in full:
  //   - status != 'new' (applied/saved/dismissed): jobs you've acted on
  //   - tier APPLY/APPLY_CAVEAT + status = 'new': jobs awaiting your decision
  //
  // INELIGIBLE is deliberately NOT fetched. It used to load the 500 most
  // recent for an Archive view — 0.52 MB of a 5.38 MB refresh, on every load
  // and every poll — to browse 518 of 51,151 rows with search running
  // client-side over just that slice. It looked like a way to catch a
  // wrongly-rejected job and could not be one. Auditing that pile wants a
  // query against Supabase, not a truncated browse list.
  const [tracked, activeReview] = await Promise.all([
    fetchPaged(url, key, slimSelect('select=*&status=neq.new&order=found_at.desc', COLS_FULL), id),
    fetchPaged(url, key, slimSelect('select=*&status=eq.new&tier=in.(APPLY,APPLY_CAVEAT)&order=found_at.desc', COLS_FULL), id),
  ])

  const seen = new Set<string>()
  const jobs: Job[] = [...tracked, ...activeReview]
    .filter((j) => (seen.has(j.id) ? false : (seen.add(j.id), true)))
    .sort((a, b) => new Date(b.found_at).getTime() - new Date(a.found_at).getTime())

  // Collapse near-identical postings into one card. The same job arrives from
  // up to three sources with different title wording, which the scrapers'
  // exact norm_key dedup cannot catch. This GROUPS rather than hides — see
  // lib/dupes.ts for why auto-merging was backtested and rejected.
  const grouped = groupNearDuplicates(jobs)

  return (
    // The table is the layout — full bleed. The old max-w-3xl centred column
    // existed for a card list and would defeat the point of a dense table.
    <JobList
      initialJobs={stripForClient(grouped)}
      personaLabel={persona.label}
      personaSub={persona.username}
    />
  )
}
