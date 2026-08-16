import { JobList } from '@/components/JobList'
import { requirePersonaPage } from '@/lib/auth'
import { groupNearDuplicates } from '@/lib/dupes'
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

async function fetchPaged(url: string, key: string, query: string, personaId: string): Promise<Job[]> {
  const all: Job[] = []
  for (let offset = 0; ; offset += PAGE) {
    const page = await fetchJobs(url, key, `${query}&limit=${PAGE}&offset=${offset}`, personaId)
    all.push(...page)
    if (page.length < PAGE) return all
    // Runaway guard: 50k rows is far beyond any real persona's tracked set.
    if (all.length >= 50_000) return all
  }
}

async function fetchJobs(url: string, key: string, query: string, personaId: string): Promise<Job[]> {
  const res = await fetch(`${url}/rest/v1/jobs?${query}`, {
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
    },
    cache: 'no-store',
  })
  if (!res.ok) {
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

export default async function HomePage() {
  const persona = await requirePersonaPage()
  const { supabaseUrl: url, serviceKey: key, id } = persona

  // Only the low-priority SKIP backlog is capped by recency — everything
  // else must load in full regardless of how many jobs pile up over time:
  //   - status != 'new' (applied/saved/dismissed): jobs you've acted on
  //   - tier APPLY/APPLY_CAVEAT + status = 'new': jobs awaiting your decision
  //   - tier INELIGIBLE + status = 'new': hard-blocked, fine to trim by age
  const [tracked, activeReview, skipRecent] = await Promise.all([
    fetchPaged(url, key, 'select=*&status=neq.new&order=found_at.desc', id),
    fetchPaged(url, key, 'select=*&status=eq.new&tier=in.(APPLY,APPLY_CAVEAT)&order=found_at.desc', id),
    fetchJobs(url, key, 'select=*&status=eq.new&tier=eq.INELIGIBLE&order=found_at.desc&limit=500', id),
  ])

  const seen = new Set<string>()
  const jobs: Job[] = [...tracked, ...activeReview, ...skipRecent]
    .filter((j) => (seen.has(j.id) ? false : (seen.add(j.id), true)))
    .sort((a, b) => new Date(b.found_at).getTime() - new Date(a.found_at).getTime())

  // Collapse near-identical postings into one card. The same job arrives from
  // up to three sources with different title wording, which the scrapers'
  // exact norm_key dedup cannot catch. This GROUPS rather than hides — see
  // lib/dupes.ts for why auto-merging was backtested and rejected.
  const grouped = groupNearDuplicates(jobs)

  return (
    <main className="min-h-screen bg-gray-950 text-white">
      <div className="max-w-3xl mx-auto px-4 py-10">
        <JobList initialJobs={grouped} personaLabel={persona.label} />
      </div>
    </main>
  )
}
