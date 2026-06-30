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
  const applyCount = all.filter(j => j.tier === 'APPLY').length
  const maybeCount = all.filter(j => j.tier === 'MAYBE').length
  const skipCount = all.filter(j => j.tier === 'SKIP').length

  return (
    <main className="min-h-screen bg-gray-950 text-white">
      <div className="max-w-3xl mx-auto px-4 py-10">
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-white">Job Dashboard</h1>
          <p className="text-gray-500 text-sm mt-1">
            <span className="text-green-400 font-medium">{applyCount} APPLY</span>
            {' · '}
            <span className="text-yellow-400 font-medium">{maybeCount} MAYBE</span>
            {' · '}
            <span className="text-gray-500">{skipCount} SKIP</span>
          </p>
        </div>
        <JobList initialJobs={all} />
      </div>
    </main>
  )
}
