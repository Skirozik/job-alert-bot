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

const POLL_MS = 5 * 60_000

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
  initialJobs, personaLabel, personaSub, ineligibleTotal,
}: {
  initialJobs: Grouped[]
  personaLabel?: string
  personaSub?: string
  /** True number of ineligible rows; the loaded set is capped at 500. */
  ineligibleTotal?: number | null
}) {
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()

  const [jobs, setJobs] = useState<Grouped[]>(initialJobs)
  const [toast, setToast] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [lastSynced, setLastSynced] = useState(() => new Date().toISOString())

  /* ── View state ───────────────────────────────────────────────────────
     LOCAL STATE is the source of truth; the URL is a mirror written after the
     fact. It used to be the other way round, reading every filter out of
     useSearchParams() on each render, and that produced a race: a click on
     "Dismissed" could land on ?view=to-apply because a slow in-flight router
     response for the previous view resolved after the newer one and
     overwrote it. Rendering off local state cannot race — the click applies
     synchronously and the URL catches up.

     The URL still round-trips: it seeds the initial state and is re-read on
     popstate, so bookmarks and the back button work as before. */
  type ViewState = {
    view: ViewKey; q: string; role: RoleFilter; src: SourceFilter
    date: DateFilter; sort: SortKey; dir: SortDir; job: string | null
  }

  const DEFAULTS: ViewState = {
    view: 'to-apply', q: '', role: 'all', src: 'all', date: 'all',
    sort: 'found_at', dir: 'desc', job: null,
  }

  const readUrl = useCallback((sp: URLSearchParams): ViewState => ({
    view: (VIEWS.includes(sp.get('view') as ViewKey) ? sp.get('view') : DEFAULTS.view) as ViewKey,
    q:    sp.get('q') ?? '',
    role: (sp.get('role') ?? 'all') as RoleFilter,
    src:  (sp.get('src') ?? 'all') as SourceFilter,
    date: (sp.get('date') ?? 'all') as DateFilter,
    sort: (sp.get('sort') ?? 'found_at') as SortKey,
    dir:  (sp.get('dir') ?? 'desc') as SortDir,
    job:  sp.get('job'),
  }), [])

  const [st, setSt] = useState<ViewState>(() => readUrl(new URLSearchParams(params.toString())))
  const { view, q: search, role, src: source, date, sort, dir, job: selectedId } = st

  const patch = useCallback((p: Partial<ViewState>) => setSt(prev => ({ ...prev, ...p })), [])

  // Mirror state -> URL with history.replaceState, NOT router.replace().
  // router.replace() triggers a Next server round-trip on every click; with
  // 2262 rows mounted that queued behind the render and the address bar lagged
  // 7.2s, so copying the URL straight after a click gave the previous view.
  // Nothing here needs the server — the data is already client-side and the
  // URL is purely a bookmark — so this writes it synchronously instead.
  useEffect(() => {
    const sp = new URLSearchParams()
    ;(Object.keys(DEFAULTS) as (keyof ViewState)[]).forEach(k => {
      const v = st[k]
      if (v != null && v !== '' && v !== DEFAULTS[k]) sp.set(k, String(v))
    })
    const qs = sp.toString()
    const next = qs ? pathname + '?' + qs : pathname
    if (next !== window.location.pathname + window.location.search) {
      window.history.replaceState(null, '', next)
    }
  }, [st, pathname])

  // Back/forward: re-seed local state from whatever the browser restored.
  useEffect(() => {
    const h = () => setSt(readUrl(new URLSearchParams(window.location.search)))
    window.addEventListener('popstate', h)
    return () => window.removeEventListener('popstate', h)
  }, [readUrl])

  useEffect(() => { setJobs(initialJobs); setLastSynced(new Date().toISOString()) }, [initialJobs])

  // Poll instead of subscribing: NEXT_PUBLIC_* is build-time-inlined and so
  // single-valued, which cannot work on a multi-tenant deployment.
  //
  // 5 minutes, not 60 seconds. Each refresh re-runs the full server fetch,
  // which pulls 14.09 MB out of Supabase — measured, not estimated. At 60s
  // that is 845 MB/hour with the tab open, and it put the project 192% over
  // its 5 GB egress quota; the arithmetic said 23 minutes of daily use
  // explains the whole 9.6 GB bill.
  //
  // Costs almost nothing in freshness, because the interval is the LEAST
  // important of three refresh paths: the focus listener below fires the
  // moment you look at the tab, the toolbar has a manual Refresh, and the
  // scrapers only produce new rows every 5-20 minutes anyway — so a 5-minute
  // poll now roughly matches the rate at which data can actually change.
  // Notifications are unaffected either way: ntfy pushes at classification
  // time and never waits on the dashboard.
  useEffect(() => {
    const tick = () => { if (document.visibilityState === 'visible') router.refresh() }
    const id = setInterval(tick, POLL_MS)
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
      patch({ job: id })
      document.querySelector<HTMLElement>(`[data-job-row="${CSS.escape(id)}"]`)
        ?.scrollIntoView({ block: 'nearest' })
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [rows, selectedId, patch])

  return (
    <div className="flex min-h-screen" style={{ background: 'var(--bg)' }}>
      <Sidebar
        view={view}
        counts={counts}
        onSelect={v => patch({ view: v, job: null })}
        personaLabel={personaLabel}
        personaSub={personaSub}
        ineligibleTotal={ineligibleTotal}
        onSignOut={async () => { await fetch('/api/auth/logout', { method: 'POST' }); router.push('/login'); router.refresh() }}
      />

      <main className="flex-1 min-w-0 flex flex-col">
        <Toolbar
          search={search} onSearch={v => patch({ q: v })}
          role={role} onRole={v => patch({ role: v })}
          source={source} onSource={v => patch({ src: v })}
          date={date} onDate={v => patch({ date: v })}
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

        <div data-scroll-root className="flex-1 overflow-y-auto" style={{ background: 'var(--bg-surface)' }}>
          <JobTable
            rows={rows}
            cols={cols}
            selectedId={selectedId}
            onSelect={id => patch({ job: id === selectedId ? null : id })}
            onStatus={onStatus}
            sort={sort}
            dir={dir}
            onSort={c => patch({ sort: c, dir: sort === c && dir === 'desc' ? 'asc' : 'desc' })}
            emptyMessage={EMPTY[view]}
          />
        </div>
      </main>

      {selected && (
        <JobDrawer job={selected} onClose={() => patch({ job: null })} onStatus={onStatus} />
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
