import { supabaseServer } from '@/lib/supabase-server'
import { JobList } from '@/components/JobList'
import type { Job } from '@/types/job'

export const dynamic = 'force-dynamic'

export default async function HomePage() {
  const { data: jobs } = await supabaseServer
    .from('jobs')
    .select('*')
    .order('found_at', { ascending: false })
    .limit(500)

  const all = (jobs ?? []) as Job[]

  return (
    <main className="min-h-screen bg-gray-950 text-white">
      <div className="max-w-3xl mx-auto px-4 py-10">
        <JobList initialJobs={all} />
      </div>
    </main>
  )
}
