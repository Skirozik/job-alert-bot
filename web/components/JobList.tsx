'use client'

import { useState, useEffect } from 'react'
import { supabaseBrowser } from '@/lib/supabase-browser'
import { JobCard } from './JobCard'
import type { Job, Status } from '@/types/job'

export function JobList({ initialJobs }: { initialJobs: Job[] }) {
  const [jobs, setJobs] = useState<Job[]>(initialJobs)
  const [showSkip, setShowSkip] = useState(false)
  const [filterStatus, setFilterStatus] = useState<'all' | 'new' | 'saved' | 'applied'>('all')

  // Real-time: new jobs float in as they're inserted
  useEffect(() => {
    const channel = supabaseBrowser
      .channel('jobs-live')
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'jobs' },
        (payload) => {
          const job = payload.new as Job
          setJobs(prev => {
            if (prev.some(j => j.id === job.id)) return prev
            return [job, ...prev]
          })
        }
      )
      .subscribe()

    return () => { supabaseBrowser.removeChannel(channel) }
  }, [])

  function handleStatusChange(id: string, status: Status) {
    setJobs(prev => prev.map(j => j.id === id ? { ...j, status } : j))
  }

  const displayed = jobs.filter(j => {
    if (!showSkip && j.tier === 'SKIP') return false
    if (filterStatus !== 'all' && (j.status ?? 'new') !== filterStatus) return false
    return true
  })

  const applyCount = jobs.filter(j => j.tier === 'APPLY').length
  const maybeCount = jobs.filter(j => j.tier === 'MAYBE').length

  return (
    <div>
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        {/* Status filter */}
        <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-lg p-1">
          {(['all', 'new', 'saved', 'applied'] as const).map(s => (
            <button
              key={s}
              onClick={() => setFilterStatus(s)}
              className={`text-xs px-3 py-1 rounded-md transition-colors ${
                filterStatus === s
                  ? 'bg-gray-700 text-white'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {s === 'all' ? `All (${applyCount + maybeCount})` : s}
            </button>
          ))}
        </div>

        {/* Skip toggle */}
        <label className="flex items-center gap-2 text-sm text-gray-500 cursor-pointer select-none hover:text-gray-300 transition-colors">
          <div className={`w-9 h-5 rounded-full transition-colors relative ${showSkip ? 'bg-gray-600' : 'bg-gray-800'}`}>
            <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${showSkip ? 'translate-x-4' : 'translate-x-0.5'}`} />
          </div>
          <input
            type="checkbox"
            checked={showSkip}
            onChange={e => setShowSkip(e.target.checked)}
            className="sr-only"
          />
          Show skipped
        </label>
      </div>

      {/* List */}
      <div className="space-y-3">
        {displayed.map(job => (
          <JobCard key={job.id} job={job} onStatusChange={handleStatusChange} />
        ))}
        {displayed.length === 0 && (
          <div className="text-center py-20 text-gray-600">
            <p className="text-lg mb-1">No jobs here</p>
            <p className="text-sm">Scraper runs every 30 minutes — check back soon.</p>
          </div>
        )}
      </div>
    </div>
  )
}
