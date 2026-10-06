import { JobList } from '@/components/JobList'
import { requirePersonaPage } from '@/lib/auth'
import { groupNearDuplicates, type Grouped } from '@/lib/dupes'
import type { Job } from '@/types/job'
import { fetchHead, reviewCutoff } from '@/lib/jobQueries'

// Must stay force-dynamic. A cached render would serve one person's jobs to
// another.
export const dynamic = 'force-dynamic'

/**
 * Drop `description` before the data crosses to the browser.
 *
 * Descriptions are up to 12,000 chars each (scraper/linkedin.py MAX_LEN) and
 * NOTHING on the client reads them: not JobTable, not JobDrawer, not the
 * jobView filters (search matches company + title only). Duplicate grouping
 * deliberately uses compact fields instead.
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

  // The first render carries what the default views need: everything you are
  // tracking (saved, applied, outcomes) and review jobs from the last
  // REVIEW_WINDOW_DAYS. Older review jobs and dismissed ones load right after,
  // from /api/jobs/backlog, so the first screen is not held hostage by months
  // of history. Before this split every open shipped ~9,300 rows (7.7 MB).
  //
  // INELIGIBLE is deliberately NOT fetched. It used to load the 500 most
  // recent for an Archive view — 0.52 MB of a 5.38 MB refresh, on every load
  // and every poll — to browse 518 of 51,151 rows with search running
  // client-side over just that slice. It looked like a way to catch a
  // wrongly-rejected job and could not be one. Auditing that pile wants a
  // query against Supabase, not a truncated browse list.
  const backlogBefore = reviewCutoff()
  const head = await fetchHead(url, key, backlogBefore, id)

  const seen = new Set<string>()
  const jobs: Job[] = head
    .filter((j) => (seen.has(j.id) ? false : (seen.add(j.id), true)))
    .sort((a, b) => new Date(b.found_at).getTime() - new Date(a.found_at).getTime())

  // Collapse high-confidence duplicate postings into one row. The same job
  // arrives from up to three sources with different ids and title wording,
  // which the scrapers' per-source norm_key cannot catch. This GROUPS rather
  // than deletes: every source stays reachable from the drawer.
  const grouped = groupNearDuplicates(jobs)

  return (
    // The table is the layout — full bleed. The old max-w-3xl centred column
    // existed for a card list and would defeat the point of a dense table.
    <JobList
      initialJobs={stripForClient(grouped)}
      personaLabel={persona.label}
      personaSub={persona.username}
      backlogBefore={backlogBefore}
    />
  )
}
