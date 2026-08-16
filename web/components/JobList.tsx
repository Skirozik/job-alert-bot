'use client'

import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useRouter, useSearchParams, usePathname } from 'next/navigation'
import { Sidebar } from './Sidebar'
import { Toolbar } from './Toolbar'
import { JobTable } from './JobTable'
import { JobDrawer } from './JobDrawer'
import type { Job, Status } from '@/types/job'
import type { Grouped } from '@/lib/dupes'
import {
  matchesView, matchesRole, matchesSource, matchesDate, matchesSearch,
  visibleOptionalColumns, sortJobs, relativeTime,
  type ViewKey, type RoleFilter, type SourceFilter, type DateFilter,
  type SortKey, type SortDir,
} from '@/lib/jobView'

const VIEWS: ViewKey[] = ['to-apply','caveat','my-list','applied','saved','dismissed','ineligible','all']

const EMPTY: Record<ViewKey, string> = {
  'to-apply':  'Nothing clean to apply to right now.',
  'caveat':    'No jobs with a caveat.',
  'my-list':   'Your list is empty.',
  'applied':   "You haven't marked anything as applied yet.",
  'saved':     'Nothing saved yet.',
  'dismissed': 'Nothing dismissed.',
  'ineligible':'Nothing ruled ineligible.',
  'all':       'No postings stored yet.',
}

export function JobList({
  initialJobs, personaLabel, personaSub,
}: {
  initialJobs: Grouped[]
  personaLabel?: string
  personaSub?: string
}) {
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()

  const [jobs, setJobs] = useState<Grouped[]>(initialJobs)
  const [toast, setToast] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [lastSynced, setLastSynced] = useState(() => new Date().toISOString())

  // URL is the source of truth for view/sort/search, so the current view is
  // bookmarkable and the back button works. Sidebar selection and filter state
  // cannot drift because they read the same value.
  const view = (VIEWS.includes(params.get('view') as ViewKey) ? params.get('view') : 'to-apply') as ViewKey
  const search = params.get('q') ?? ''
  const role = (params.get('role') ?? 'all') as RoleFilter
  const source = (params.get('src') ?? 'all') as SourceFilter
  const date = (params.get('date') ?? 'all') as DateFilter
  const sort = (params.get('sort') ?? 'found_at') as SortKey
  const dir = (params.get('dir') ?? 'desc') as SortDir
  const selectedId = params.get('job')

  const setParams = useCallback((patch: Record<string, string | null>) => {
    const p = new URLSearchParams(params.toString())
    for (const [k, v] of Object.entries(patch)) {
      if (v == null || v === '' || v === 'all') p.delete(k)
      else p.set(k, v)
    }
    router.replace(`${pathname}?${p.toString()}`, { scroll: false })
  }, [params, pathname, router])

  useEffect(() => { setJobs(initialJobs); setLastSynced(new Date().toISOString()) }, [initialJobs])

  // Poll instead of subscribing: NEXT_PUBLIC_* is build-time-inlined and so
  // single-valued, which cannot work on a multi-tenant deployment.
  useEffect(() => {
    const tick = () => { if (document.visibilityState === 'visible') router.refresh() }
    const id = setInterval(tick, 60_000)
    window.addEventListener('focus', tick)
    return () => { clearInterval(id); window.removeEventListener('focus', tick) }
  }, [router])

  const counts = useMemo(() => {
    const c = {} as Record<ViewKey, number>
    for (const v of VIEWS) c[v] = jobs.filter(j => matchesView(j, v)).length
    return c
  }, [jobs])

  const rows = useMemo(() => {
    const filtered = jobs.filter(j =>
      matchesView(j, view) && matchesRole(j, role) &&
      matchesSource(j, source) && matchesDate(j, date) && matchesSearch(j, search)
    )
    return sortJobs(filtered, sort, dir) as Grouped[]
  }, [jobs, view, role, source, date, search, sort, dir])

  // The optional-column rule: one implementation, three columns. Computed from
  // the CURRENT filtered set, so a persona with no resume variants (Beyonce,
  // Hassan) simply never sees that column — no per-tenant branch.
  const cols = useMemo(() => visibleOptionalColumns(rows), [rows])

  const selected = useMemo(() => rows.find(j => j.id === selectedId) ?? null, [rows, selectedId])

  async function onStatus(id: string, status: Status) {
    setSaveError(null)
    const prev = jobs
    setJobs(js => js.map(j => (j.id === id ? { ...j, status } : j)))
    try {
      const res = await fetch(`/api/jobs/${id}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      })
      if (!res.ok) throw new Error(String(res.status))
      setToast(status === 'applied' ? 'Marked as applied'
             : status === 'saved' ? 'Saved'
             : status === 'dismissed' ? 'Dismissed' : 'Reset')
      setTimeout(() => setToast(null), 1200)
    } catch {
      setJobs(prev)   // roll the optimistic update back
      setSaveError('Failed to save — check the dashboard service key')
    }
  }

  // Up/Down move between rows while the drawer is open, so a queue can be
  // triaged without touching the mouse.
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
      const t = e.target as HTMLElement
      if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA') return
      if (!rows.length) return
      e.preventDefault()
      const i = rows.findIndex(j => j.id === selectedId)
      const next = e.key === 'ArrowDown'
        ? Math.min(i < 0 ? 0 : i + 1, rows.length - 1)
        : Math.max(i < 0 ? 0 : i - 1, 0)
      const id = rows[next].id
      setParams({ job: id })
      document.querySelector<HTMLElement>(`[data-job-row="${CSS.escape(id)}"]`)
        ?.scrollIntoView({ block: 'nearest' })
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [rows, selectedId, setParams])

  return (
    <div className="flex min-h-screen" style={{ background: 'var(--bg)' }}>
      <Sidebar
        view={view}
        counts={counts}
        onSelect={v => setParams({ view: v, job: null })}
        personaLabel={personaLabel}
        personaSub={personaSub}
        onSignOut={async () => { await fetch('/api/auth/logout', { method: 'POST' }); router.push('/login'); router.refresh() }}
      />

      <main className="flex-1 min-w-0 flex flex-col">
        <Toolbar
          search={search} onSearch={v => setParams({ q: v })}
          role={role} onRole={v => setParams({ role: v })}
          source={source} onSource={v => setParams({ src: v })}
          date={date} onDate={v => setParams({ date: v })}
          onRefresh={() => router.refresh()}
          lastSynced={relativeTime(lastSynced)}
          showSource={jobs.some(j => j.id.startsWith('ats:'))}
        />

        {saveError && (
          <div style={{ padding: 'var(--s2) var(--s4)', fontSize: 'var(--text-data)', color: 'var(--danger)',
                        borderBottom: '1px solid var(--border)' }}>
            {saveError}
          </div>
        )}

        <div className="flex-1 overflow-y-auto" style={{ background: 'var(--bg-surface)' }}>
          <JobTable
            rows={rows}
            cols={cols}
            selectedId={selectedId}
            onSelect={id => setParams({ job: id === selectedId ? null : id })}
            onStatus={onStatus}
            sort={sort}
            dir={dir}
            onSort={c => setParams({ sort: c, dir: sort === c && dir === 'desc' ? 'asc' : 'desc' })}
            emptyMessage={EMPTY[view]}
          />
        </div>
      </main>

      {selected && (
        <JobDrawer job={selected} onClose={() => setParams({ job: null })} onStatus={onStatus} />
      )}

      {toast && (
        <div role="status" className="fixed left-1/2 -translate-x-1/2 z-50"
             style={{ bottom: 'var(--s6)', padding: 'var(--s2) var(--s4)', fontSize: 'var(--text-data)',
                      color: 'var(--fg)', background: 'var(--bg-raised)',
                      border: '1px solid var(--border-strong)', borderRadius: 'var(--radius)' }}>
          {toast}
        </div>
      )}
    </div>
  )
}
