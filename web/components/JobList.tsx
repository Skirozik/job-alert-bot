'use client'

import { useState, useEffect, useRef } from 'react'
import { supabaseBrowser } from '@/lib/supabase-browser'
import { JobCard } from './JobCard'
import type { Job, Status } from '@/types/job'

const INTERN_RE = /intern|internship|co[\s-]?op|apprentice/i

function isInternship(job: Job): boolean {
  return INTERN_RE.test(job.title)
}

// Tier and Status are independent axes — a job can be any combination of
// the two (e.g. MAYBE + Saved), so they're separate, freely-composable
// filters rather than one mutually-exclusive "view".
type TierFilter = 'all' | 'apply' | 'maybe' | 'skip'
type StatusFilter = 'active' | 'all' | 'applied' | 'saved' | 'dismissed'
type RoleFilter = 'all' | 'internships' | 'entry-level'

function matchesTier(j: Job, t: TierFilter): boolean {
  if (t === 'all') return j.tier !== 'SKIP' // SKIP stays an explicit opt-in, not folded into "all"
  if (t === 'apply') return j.tier === 'APPLY'
  if (t === 'maybe') return j.tier === 'MAYBE'
  return j.tier === 'SKIP'
}

function matchesStatus(j: Job, s: StatusFilter): boolean {
  const status = j.status ?? 'new'
  if (s === 'active') return status !== 'applied' && status !== 'dismissed'
  if (s === 'all') return true
  return status === s
}

function matchesRole(j: Job, r: RoleFilter): boolean {
  if (r === 'internships') return isInternship(j)
  if (r === 'entry-level') return !isInternship(j)
  return true
}

const NEUTRAL_ACTIVE = 'bg-gray-800 border-gray-600 text-gray-200'
const INACTIVE_PILL = 'bg-gray-900 border-gray-800 text-gray-500 hover:border-gray-600 hover:text-gray-300'

const TIER_OPTS: { key: TierFilter; label: string; activeClass: string }[] = [
  { key: 'all', label: 'All', activeClass: NEUTRAL_ACTIVE },
  { key: 'apply', label: 'Apply', activeClass: 'bg-green-600/20 border-green-500/40 text-green-300' },
  { key: 'maybe', label: 'Maybe', activeClass: 'bg-yellow-600/20 border-yellow-500/40 text-yellow-300' },
  { key: 'skip', label: 'Skip', activeClass: 'bg-gray-700 border-gray-600 text-white' },
]

const STATUS_OPTS: { key: StatusFilter; label: string; activeClass: string }[] = [
  { key: 'active', label: 'Active', activeClass: NEUTRAL_ACTIVE },
  { key: 'all', label: 'All', activeClass: NEUTRAL_ACTIVE },
  { key: 'applied', label: 'Applied', activeClass: 'bg-blue-600/20 border-blue-500/40 text-blue-300' },
  { key: 'saved', label: 'Saved', activeClass: 'bg-purple-600/20 border-purple-500/40 text-purple-300' },
  { key: 'dismissed', label: 'Dismissed', activeClass: 'bg-red-900/30 border-red-700/40 text-red-400' },
]

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

export function JobList({ initialJobs }: { initialJobs: Job[] }) {
  const [jobs, setJobs] = useState<Job[]>(initialJobs)
  const [tierFilter, setTierFilter] = useState<TierFilter>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('active')
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

  // Fully composable: every predicate is independent, so no combination can
  // ever silently ignore one of the active filters.
  const displayed = jobs.filter(j =>
    matchesTier(j, tierFilter) &&
    matchesStatus(j, statusFilter) &&
    matchesRole(j, roleFilter)
  )

  // Header summary is a stable "pipeline health" readout — unconditional
  // totals, not affected by the current filter selection.
  const isActive = (j: Job) => { const s = j.status ?? 'new'; return s !== 'applied' && s !== 'dismissed' }
  const applyCount = jobs.filter(j => j.tier === 'APPLY' && isActive(j)).length
  const maybeCount = jobs.filter(j => j.tier === 'MAYBE' && isActive(j)).length
  const skipCount  = jobs.filter(j => j.tier === 'SKIP').length

  // Pill badge counts are faceted: each shows what you'd see if you clicked
  // it, given your *other* current selections — a static total would lie
  // once two axes combine (e.g. "Applied (25)" while Tier=Skip is selected
  // would still show 25 even though almost none of those are skip-tier).
  const tierBase = jobs.filter(j => matchesStatus(j, statusFilter) && matchesRole(j, roleFilter))
  const tierCounts: Record<TierFilter, number> = {
    all: tierBase.filter(j => matchesTier(j, 'all')).length,
    apply: tierBase.filter(j => matchesTier(j, 'apply')).length,
    maybe: tierBase.filter(j => matchesTier(j, 'maybe')).length,
    skip: tierBase.filter(j => matchesTier(j, 'skip')).length,
  }

  const statusBase = jobs.filter(j => matchesTier(j, tierFilter) && matchesRole(j, roleFilter))
  const statusCounts: Record<StatusFilter, number> = {
    active: statusBase.filter(j => matchesStatus(j, 'active')).length,
    all: statusBase.length,
    applied: statusBase.filter(j => matchesStatus(j, 'applied')).length,
    saved: statusBase.filter(j => matchesStatus(j, 'saved')).length,
    dismissed: statusBase.filter(j => matchesStatus(j, 'dismissed')).length,
  }

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

      {/* Controls — Tier and Status are independent filters that compose;
          Role narrows either. Each pill's count reflects the other active
          selections, so it always matches what clicking it will show. */}
      <div className="mb-6 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
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

          <span className="text-xs uppercase tracking-wide text-gray-600 ml-1">Tier</span>
          {TIER_OPTS.map(({ key, label, activeClass }) => (
            <button
              key={key}
              onClick={() => setTierFilter(key)}
              className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
                tierFilter === key ? activeClass : INACTIVE_PILL
              }`}
            >
              {label} ({tierCounts[key]})
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-gray-600 mr-1">Status</span>
          {STATUS_OPTS.map(({ key, label, activeClass }) => (
            <button
              key={key}
              onClick={() => setStatusFilter(key)}
              className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
                statusFilter === key ? activeClass : INACTIVE_PILL
              }`}
            >
              {label} ({statusCounts[key]})
            </button>
          ))}
        </div>
      </div>

      {/* List */}
      <div className="space-y-3">
        {displayed.map(job => (
          <JobCard key={job.id} job={job} onStatusChange={handleStatusChange} />
        ))}
        {displayed.length === 0 && jobs.length > 0 && (
          <div className="text-center py-20 text-gray-600">
            <p className="text-lg mb-1">No jobs match these filters</p>
            <p className="text-sm">Try widening the Tier, Status, or Role filter above.</p>
          </div>
        )}
        {displayed.length === 0 && jobs.length === 0 && (
          <div className="text-center py-20 text-gray-600">
            <p className="text-lg mb-1">No jobs here</p>
            <p className="text-sm">Scraper runs every 30 minutes — check back soon.</p>
          </div>
        )}
      </div>
    </div>
  )
}
