'use client'

import { useState, useEffect, useRef } from 'react'
import { supabaseBrowser } from '@/lib/supabase-browser'
import { JobCard } from './JobCard'
import type { Job, Status } from '@/types/job'

const INTERN_RE = /intern|internship|co[\s-]?op|apprentice/i

function isInternship(job: Job): boolean {
  return INTERN_RE.test(job.title)
}

type View = 'all' | 'applied' | 'saved' | 'skipped' | 'dismissed'
type RoleFilter = 'all' | 'internships' | 'entry-level'

export function JobList({ initialJobs }: { initialJobs: Job[] }) {
  const [jobs, setJobs] = useState<Job[]>(initialJobs)
  const [view, setView] = useState<View>('all')
  const [roleFilter, setRoleFilter] = useState<RoleFilter>('all')
  const [showFilterMenu, setShowFilterMenu] = useState(false)
  const filterRef = useRef<HTMLDivElement>(null)

  // Close dropdown on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (filterRef.current && !filterRef.current.contains(e.target as Node)) {
        setShowFilterMenu(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  // Real-time: new jobs float in as they're inserted, and status/tier edits
  // made elsewhere (another tab, another device) sync in live too.
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
      .on(
        'postgres_changes',
        { event: 'UPDATE', schema: 'public', table: 'jobs' },
        (payload) => {
          const job = payload.new as Job
          setJobs(prev => prev.map(j => j.id === job.id ? job : j))
        }
      )
      .subscribe()

    return () => { supabaseBrowser.removeChannel(channel) }
  }, [])

  function handleStatusChange(id: string, status: Status) {
    setJobs(prev => prev.map(j => j.id === id ? { ...j, status } : j))
  }

  const displayed = jobs.filter(j => {
    if (view === 'applied')   return (j.status ?? 'new') === 'applied'
    if (view === 'saved')     return (j.status ?? 'new') === 'saved'
    if (view === 'skipped')   return j.tier === 'SKIP'
    if (view === 'dismissed') return (j.status ?? 'new') === 'dismissed'
    // view === 'all': APPLY + MAYBE tier, active status only
    if (j.tier === 'SKIP') return false
    const status = j.status ?? 'new'
    if (status === 'applied' || status === 'dismissed') return false
    if (roleFilter === 'internships' && !isInternship(j)) return false
    if (roleFilter === 'entry-level' && isInternship(j)) return false
    return true
  })

  const isActive = (j: Job) => { const s = j.status ?? 'new'; return s !== 'applied' && s !== 'dismissed' }

  const applyCount     = jobs.filter(j => j.tier === 'APPLY' && isActive(j)).length
  const maybeCount     = jobs.filter(j => j.tier === 'MAYBE' && isActive(j)).length
  const skipCount      = jobs.filter(j => j.tier === 'SKIP').length
  const appliedCount   = jobs.filter(j => (j.status ?? 'new') === 'applied').length
  const savedCount     = jobs.filter(j => (j.status ?? 'new') === 'saved').length
  const dismissedCount = jobs.filter(j => (j.status ?? 'new') === 'dismissed').length

  const ROLE_LABELS: Record<RoleFilter, string> = {
    'all': 'Filter',
    'internships': 'Internships',
    'entry-level': 'Entry-level',
  }
  const ROLE_OPTIONS: { key: RoleFilter; label: string }[] = [
    { key: 'all', label: 'All roles' },
    { key: 'internships', label: 'Internships' },
    { key: 'entry-level', label: 'Entry-level' },
  ]

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
      <div className="flex flex-wrap items-center gap-2 mb-6">

        {/* Role filter dropdown */}
        <div className="relative" ref={filterRef}>
          <button
            onClick={() => setShowFilterMenu(v => !v)}
            className="text-xs px-3 py-1.5 rounded-lg bg-gray-900 border border-gray-700 text-gray-300 hover:border-gray-500 transition-colors flex items-center gap-1"
          >
            {ROLE_LABELS[roleFilter]} <span className="text-gray-500">▾</span>
          </button>
          {showFilterMenu && (
            <div className="absolute top-full mt-1 left-0 bg-gray-900 border border-gray-700 rounded-lg py-1 z-20 min-w-[140px] shadow-xl">
              {ROLE_OPTIONS.map(({ key, label }) => (
                <button
                  key={key}
                  onClick={() => { setRoleFilter(key); setShowFilterMenu(false) }}
                  className={`block w-full text-left px-3 py-1.5 text-xs transition-colors hover:bg-gray-800 ${
                    roleFilter === key ? 'text-white' : 'text-gray-400'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Applied toggle */}
        <button
          onClick={() => setView(v => v === 'applied' ? 'all' : 'applied')}
          className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
            view === 'applied'
              ? 'bg-blue-600/20 border-blue-500/40 text-blue-300'
              : 'bg-gray-900 border-gray-800 text-gray-500 hover:border-gray-600 hover:text-gray-300'
          }`}
        >
          Applied ({appliedCount})
        </button>

        {/* Saved toggle */}
        <button
          onClick={() => setView(v => v === 'saved' ? 'all' : 'saved')}
          className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
            view === 'saved'
              ? 'bg-purple-600/20 border-purple-500/40 text-purple-300'
              : 'bg-gray-900 border-gray-800 text-gray-500 hover:border-gray-600 hover:text-gray-300'
          }`}
        >
          Saved ({savedCount})
        </button>

        {/* Skipped toggle */}
        <button
          onClick={() => setView(v => v === 'skipped' ? 'all' : 'skipped')}
          className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
            view === 'skipped'
              ? 'bg-gray-700 border-gray-600 text-white'
              : 'bg-gray-900 border-gray-800 text-gray-500 hover:border-gray-600 hover:text-gray-300'
          }`}
        >
          Skipped ({skipCount})
        </button>

        {/* Dismissed toggle */}
        <button
          onClick={() => setView(v => v === 'dismissed' ? 'all' : 'dismissed')}
          className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
            view === 'dismissed'
              ? 'bg-red-900/30 border-red-700/40 text-red-400'
              : 'bg-gray-900 border-gray-800 text-gray-500 hover:border-gray-600 hover:text-gray-300'
          }`}
        >
          Dismissed ({dismissedCount})
        </button>

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
