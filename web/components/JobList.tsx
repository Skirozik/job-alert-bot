'use client'

import { useState, useEffect } from 'react'
import { supabaseBrowser } from '@/lib/supabase-browser'
import { JobCard } from './JobCard'
import type { Job, Status } from '@/types/job'

const INTERN_RE = /intern|internship|co[\s-]?op|apprentice/i

function isInternship(job: Job): boolean {
  return INTERN_RE.test(job.title)
}

type RoleFilter = 'all' | 'internships' | 'entry-level'

export function JobList({ initialJobs }: { initialJobs: Job[] }) {
  const [jobs, setJobs] = useState<Job[]>(initialJobs)
  const [showSkip, setShowSkip] = useState(false)
  const [filterStatus, setFilterStatus] = useState<'all' | 'new' | 'saved' | 'applied'>('all')
  const [roleFilter, setRoleFilter] = useState<RoleFilter>('internships')

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
    const status = j.status ?? 'new'
    if (filterStatus === 'all' && (status === 'applied' || status === 'dismissed')) return false
    if (filterStatus !== 'all' && status !== filterStatus) return false
    if (roleFilter === 'internships' && !isInternship(j)) return false
    if (roleFilter === 'entry-level' && isInternship(j)) return false
    return true
  })

  const applyCount = jobs.filter(j => j.tier === 'APPLY').length
  const maybeCount = jobs.filter(j => j.tier === 'MAYBE').length
  const skipCount  = jobs.filter(j => j.tier === 'SKIP').length

  // Tab counts exclude applied/dismissed so they match what's visible in default view
  const isActive = (j: Job) => { const s = j.status ?? 'new'; return s !== 'applied' && s !== 'dismissed' }
  const internCount = jobs.filter(j => j.tier !== 'SKIP' && isInternship(j) && isActive(j)).length
  const entryCount  = jobs.filter(j => j.tier !== 'SKIP' && !isInternship(j) && isActive(j)).length

  const savedCount   = jobs.filter(j => (j.status ?? 'new') === 'saved').length
  const appliedCount = jobs.filter(j => (j.status ?? 'new') === 'applied').length

  return (
    <div>
      {/* Header */}
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

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3 mb-6">

        {/* Role type filter */}
        <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-lg p-1">
          <button
            onClick={() => setRoleFilter('internships')}
            className={`text-xs px-3 py-1 rounded-md transition-colors ${roleFilter === 'internships' ? 'bg-gray-700 text-white' : 'text-gray-500 hover:text-gray-300'}`}
          >
            Internships ({internCount})
          </button>
          <button
            onClick={() => setRoleFilter('entry-level')}
            className={`text-xs px-3 py-1 rounded-md transition-colors ${roleFilter === 'entry-level' ? 'bg-gray-700 text-white' : 'text-gray-500 hover:text-gray-300'}`}
          >
            Entry-level ({entryCount})
          </button>
          <button
            onClick={() => setRoleFilter('all')}
            className={`text-xs px-3 py-1 rounded-md transition-colors ${roleFilter === 'all' ? 'bg-gray-700 text-white' : 'text-gray-500 hover:text-gray-300'}`}
          >
            All
          </button>
        </div>

        {/* Status filter */}
        <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-lg p-1">
          {([
            { key: 'all',     label: 'All' },
            { key: 'new',     label: 'New' },
            { key: 'saved',   label: `Saved${savedCount ? ` (${savedCount})` : ''}` },
            { key: 'applied', label: `Applied${appliedCount ? ` (${appliedCount})` : ''}` },
          ] as const).map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setFilterStatus(key)}
              className={`text-xs px-3 py-1 rounded-md transition-colors ${
                filterStatus === key ? 'bg-gray-700 text-white' : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {label}
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
