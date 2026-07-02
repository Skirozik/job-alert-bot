import { JobList } from '@/components/JobList'
import type { Job } from '@/types/job'

export const dynamic = 'force-dynamic'

async function fetchJobs(url: string, key: string, query: string): Promise<Job[]> {
  const res = await fetch(`${url}/rest/v1/jobs?${query}`, {
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
    },
    cache: 'no-store',
  })
  return res.ok ? await res.json() : []
}

export default async function HomePage() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL!
  const key = process.env.SUPABASE_SERVICE_KEY!

  // Jobs you've acted on (applied/saved/dismissed) must never fall off the
  // page just because more jobs got scraped later — only the unreviewed
  // "new" queue is capped, since that's the one that's fine to trim by age.
  const [tracked, recent] = await Promise.all([
    fetchJobs(url, key, 'select=*&status=neq.new&order=found_at.desc'),
    fetchJobs(url, key, 'select=*&status=eq.new&order=found_at.desc&limit=500'),
  ])

  const seen = new Set<string>()
  const jobs: Job[] = [...tracked, ...recent]
    .filter((j) => (seen.has(j.id) ? false : (seen.add(j.id), true)))
    .sort((a, b) => new Date(b.found_at).getTime() - new Date(a.found_at).getTime())

  return (
    <main className="min-h-screen bg-gray-950 text-white">
      <div className="max-w-3xl mx-auto px-4 py-10">
        <JobList initialJobs={jobs} />
      </div>
    </main>
  )
}
