'use client'

import { useState, useEffect, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { JobCard } from './JobCard'
import type { Job, Status } from '@/types/job'
import type { Grouped } from '@/lib/dupes'

const INTERN_RE = /intern|internship|co[\s-]?op|apprentice/i

function isInternship(job: Job): boolean {
  return INTERN_RE.test(job.title)
}

// Tier and Status are independent axes — a job can be any combination of
// the two (e.g. MAYBE + Saved), so they're separate, freely-composable
// filters rather than one mutually-exclusive "view".
type TierFilter = 'actionable' | 'apply' | 'caveat' | 'ineligible'
type StatusFilter = 'active' | 'all' | 'applied' | 'saved' | 'dismissed'
type RoleFilter = 'all' | 'internships' | 'entry-level'

// APPLY and APPLY_CAVEAT share one list — that is the point of the scheme.
// The old MAYBE tier was never looked at, so splitting them again would
// recreate exactly the problem this replaced.
function matchesTier(j: Job, t: TierFilter): boolean {
  if (t === 'actionable') return j.tier === 'APPLY' || j.tier === 'APPLY_CAVEAT'
  if (t === 'apply') return j.tier === 'APPLY'
  if (t === 'caveat') return j.tier === 'APPLY_CAVEAT'
  return j.tier === 'INELIGIBLE'
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
  { key: 'actionable', label: 'My list', activeClass: NEUTRAL_ACTIVE },
  { key: 'apply', label: 'Clean', activeClass: 'bg-green-600/20 border-green-500/40 text-green-300' },
  { key: 'caveat', label: 'Caveat', activeClass: 'bg-amber-600/20 border-amber-500/40 text-amber-300' },
  { key: 'ineligible', label: 'Ineligible', activeClass: 'bg-gray-700 border-gray-600 text-white' },
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

export function JobList({
  initialJobs,
  personaLabel,
}: {
  initialJobs: Grouped[]
  personaLabel?: string
}) {
  const router = useRouter()
  const [jobs, setJobs] = useState<Grouped[]>(initialJobs)
  const [tierFilter, setTierFilter] = useState<TierFilter>('actionable')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('active')
  const [roleFilter, setRoleFilter] = useState<RoleFilter>('all')
  const [showFilterMenu, setShowFilterMenu] = useState(false)
  // INELIGIBLE is the ONLY tier that is ever hidden, and only behind this
  // explicit toggle with a visible count — never silently dropped.
  const [showIneligible, setShowIneligible] = useState(false)
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

  // Adopt refreshed server data. Without this, `useState(initialJobs)` above
  // captures the first render's array forever and every router.refresh() below
  // would silently change nothing on screen. `initialJobs` is a fresh array
  // identity on each server render, so this fires on every refresh — intended.
  //
  // Safe against the optimistic updates in handleStatusChange: the PATCH has
  // already persisted (and is read-back-verified server-side) before
  // onStatusChange runs, so a refresh landing afterwards carries the same value.
  useEffect(() => {
    setJobs(initialJobs)
  }, [initialJobs])

  // Poll instead of subscribing to Supabase realtime.
  //
  // Realtime needed NEXT_PUBLIC_SUPABASE_ANON_KEY in the browser, which is
  // inlined at build time and therefore single-valued — it cannot vary per
  // logged-in person, so it can't work on a multi-tenant deployment. Making it
  // work would also require a permissive anon SELECT policy on every project,
  // which would put real people's job-search history behind a key that ships
  // in a publicly-served JS bundle.
  //
  // Polling removes the browser's Supabase access entirely. Freshness costs
  // nothing here: the scrapers run every 20 minutes to 2 hours, so a 60-second
  // poll is an order of magnitude fresher than the underlying data.
  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === 'visible') router.refresh()
    }
    const id = setInterval(tick, 60_000)
    window.addEventListener('focus', tick)
    return () => {
      clearInterval(id)
      window.removeEventListener('focus', tick)
    }
  }, [router])

  function handleStatusChange(id: string, status: Status) {
    setJobs(prev => prev.map(j => j.id === id ? { ...j, status } : j))
  }

  // Fully composable: every predicate is independent, so no combination can
  // ever silently ignore one of the active filters.
  const matching = jobs.filter(j =>
    matchesTier(j, tierFilter) &&
    matchesStatus(j, statusFilter) &&
    matchesRole(j, roleFilter)
  )
  // When the user is on their own list, ineligible rows collapse behind a
  // toggle. Selecting the Ineligible filter explicitly shows them regardless.
  const collapsing = tierFilter !== 'ineligible'
  const hiddenIneligible = collapsing ? matching.filter(j => j.tier === 'INELIGIBLE') : []
  const displayed = collapsing && !showIneligible
    ? matching.filter(j => j.tier !== 'INELIGIBLE')
    : matching

  // Header summary is a stable "pipeline health" readout — unconditional
  // totals, not affected by the current filter selection.
  const isActive = (j: Job) => { const s = j.status ?? 'new'; return s !== 'applied' && s !== 'dismissed' }
  const applyCount = jobs.filter(j => j.tier === 'APPLY' && isActive(j)).length
  const caveatCount = jobs.filter(j => j.tier === 'APPLY_CAVEAT' && isActive(j)).length
  
  const ineligibleCount = jobs.filter(j => j.tier === 'INELIGIBLE').length

  // Pill badge counts are faceted: each shows what you'd see if you clicked
  // it, given your *other* current selections — a static total would lie
  // once two axes combine (e.g. "Applied (25)" while Tier=Skip is selected
  // would still show 25 even though almost none of those are skip-tier).
  const tierBase = jobs.filter(j => matchesStatus(j, statusFilter) && matchesRole(j, roleFilter))
  const tierCounts: Record<TierFilter, number> = {
    actionable: tierBase.filter(j => matchesTier(j, 'actionable')).length,
    apply: tierBase.filter(j => matchesTier(j, 'apply')).length,
    caveat: tierBase.filter(j => matchesTier(j, 'caveat')).length,
    ineligible: tierBase.filter(j => matchesTier(j, 'ineligible')).length,
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
      <div className="mb-8 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white">
            Job Dashboard
            {personaLabel && (
              <span className="ml-2 text-sm font-normal text-gray-500">{personaLabel}</span>
            )}
          </h1>
          <p className="text-gray-500 text-sm mt-1">
              <span className="text-green-400 font-medium">{applyCount + caveatCount} to review</span>
              {caveatCount > 0 && (
                <>
                  {' · '}
                  <span className="text-amber-300 font-medium">{caveatCount} with a caveat</span>
                </>
              )}
              {' · '}
              <span className="text-gray-600">{ineligibleCount} ineligible</span>
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => router.refresh()}
            title="Refresh now (also refreshes automatically every 60s)"
            className="text-xs px-3 py-1.5 rounded-lg bg-gray-900 border border-gray-700 text-gray-300 hover:border-gray-500 transition-colors"
          >
            Refresh
          </button>
          <button
            onClick={async () => {
              await fetch('/api/auth/logout', { method: 'POST' })
              router.push('/login')
              router.refresh()
            }}
            className="text-xs px-3 py-1.5 rounded-lg bg-gray-900 border border-gray-800 text-gray-500 hover:text-gray-300 hover:border-gray-600 transition-colors"
          >
            Sign out
          </button>
        </div>
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
        {hiddenIneligible.length > 0 && (
          <button
            onClick={() => setShowIneligible(v => !v)}
            className="w-full text-left text-xs px-3 py-2 rounded-lg bg-gray-900 border border-gray-800 text-gray-500 hover:text-gray-300 hover:border-gray-700 transition-colors"
          >
            {showIneligible
              ? `Hide ${hiddenIneligible.length} ineligible`
              : `Show ${hiddenIneligible.length} ineligible`}
          </button>
        )}
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
