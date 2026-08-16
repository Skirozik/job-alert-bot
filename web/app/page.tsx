import { JobList } from '@/components/JobList'
import { requirePersonaPage } from '@/lib/auth'
import { groupNearDuplicates } from '@/lib/dupes'
import type { Job } from '@/types/job'

// Must stay force-dynamic. A cached render would serve one person's jobs to
// another.
export const dynamic = 'force-dynamic'

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
    fetchJobs(url, key, 'select=*&status=neq.new&order=found_at.desc', id),
    fetchJobs(url, key, 'select=*&status=eq.new&tier=in.(APPLY,APPLY_CAVEAT)&order=found_at.desc', id),
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
