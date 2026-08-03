import { JobList } from '@/components/JobList'
import { requirePersonaPage } from '@/lib/auth'
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
  //   - tier APPLY/MAYBE + status = 'new': jobs still awaiting your decision
  //   - tier SKIP + status = 'new': already bot-rejected, fine to trim by age
  const [tracked, activeReview, skipRecent] = await Promise.all([
    fetchJobs(url, key, 'select=*&status=neq.new&order=found_at.desc', id),
    fetchJobs(url, key, 'select=*&status=eq.new&tier=in.(APPLY,MAYBE)&order=found_at.desc', id),
    fetchJobs(url, key, 'select=*&status=eq.new&tier=eq.SKIP&order=found_at.desc&limit=500', id),
  ])

  const seen = new Set<string>()
  const jobs: Job[] = [...tracked, ...activeReview, ...skipRecent]
    .filter((j) => (seen.has(j.id) ? false : (seen.add(j.id), true)))
    .sort((a, b) => new Date(b.found_at).getTime() - new Date(a.found_at).getTime())

  return (
    <main className="min-h-screen bg-gray-950 text-white">
      <div className="max-w-3xl mx-auto px-4 py-10">
        <JobList initialJobs={jobs} personaLabel={persona.label} />
      </div>
    </main>
  )
}
