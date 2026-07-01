import { JobList } from '@/components/JobList'
import type { Job } from '@/types/job'

export const dynamic = 'force-dynamic'

export default async function HomePage() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL!
  const key = process.env.SUPABASE_SERVICE_KEY!

  const res = await fetch(
    `${url}/rest/v1/jobs?select=*&order=found_at.desc&limit=500`,
    {
      headers: {
        apikey: key,
        Authorization: `Bearer ${key}`,
      },
      cache: 'no-store',
    }
  )

  const jobs: Job[] = res.ok ? await res.json() : []

  return (
    <main className="min-h-screen bg-gray-950 text-white">
      <div className="max-w-3xl mx-auto px-4 py-10">
        <JobList initialJobs={jobs} />
      </div>
    </main>
  )
}
