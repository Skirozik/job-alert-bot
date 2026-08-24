'use client'

import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useRouter, useSearchParams, usePathname } from 'next/navigation'
import { Sidebar } from './Sidebar'
import { Toolbar } from './Toolbar'
import { JobTable } from './JobTable'
import { ApplicationStats } from './ApplicationStats'
import { JobDrawer } from './JobDrawer'
import type { Job, Status } from '@/types/job'
import { mergeGroupedJobs, type Grouped } from '@/lib/dupes'
import {
  applyGroupStatus, groupMemberIds, overlayStatusMutations,
  reconcileServerJobs, type StatusMutation,
} from '@/lib/statusMutations'
import {
  matchesView, matchesRole, matchesSource, matchesDate, matchesSearch,
  visibleOptionalColumns, sortJobs, relativeTime,
  type ViewKey, type RoleFilter, type SourceFilter, type DateFilter,
  type SortKey, type SortDir,
} from '@/lib/jobView'

const FULL_POLL_MS = 5 * 60_000
const LINKEDIN_POLL_MS = 15_000

const VIEWS: ViewKey[] = ['to-apply','caveat','my-list','applied','saved','dismissed',
                          'heard-back','interview','offer','rejected']

const EMPTY: Record<ViewKey, string> = {
  'to-apply':  'Nothing clean to apply to right now.',
  'caveat':    'No jobs with a caveat.',
  'my-list':   'Your list is empty.',
  'applied':   "You haven't marked anything as applied yet.",
  'saved':     'Nothing saved yet.',
  'dismissed': 'Nothing dismissed.',
  'heard-back': 'No replies recorded yet.',
  'interview':  'No interviews recorded yet.',
  'offer':      'No offers recorded yet.',
  'rejected':   'No rejections recorded yet.',
}

const TOASTS: Partial<Record<Status, string>> = {
  applied: 'Marked as applied',
  saved: 'Saved',
  dismissed: 'Dismissed',
  heard_back: 'Marked as heard back',
  interview: 'Marked as interview',
  offer: 'Offer recorded',
  rejected: 'Marked as rejected',
  new: 'Reset',
}

/* The views that are ABOUT what has already been sent. The stats block belongs
   on these and nowhere else -- on to-apply it would push the actual queue down
   the page to report on work that is already done. */
const TRACKING_VIEWS: ViewKey[] = ['applied','heard-back','interview','offer','rejected']

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
  const jobsRef = useRef<Grouped[]>(initialJobs)
  const statusLedger = useRef(new Map<string, StatusMutation>())
  const mutationSequence = useRef(0)
  const [pendingIds, setPendingIds] = useState<Set<string>>(() => new Set())
  /* Mobile nav. Lives here rather than in Sidebar because the toggle that
     opens it sits in the Toolbar — two siblings, so the state has to be
     above both. Always false on desktop, where Sidebar ignores it. */
  const [navOpen, setNavOpen] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [lastSynced, setLastSynced] = useState(() => new Date().toISOString())
  const linkedinCursor = useRef(new Date(Date.now() - 60_000).toISOString())

  // Every producer of client job state goes through one synchronous ref. This
  // prevents two rapid actions from both reading the same pre-click render and
  // lets async poll/refresh responses reconcile against the newest local list.
  const updateJobs = useCallback((updater: (current: Grouped[]) => Grouped[]) => {
    const next = updater(jobsRef.current)
    jobsRef.current = next
    setJobs(next)
  }, [])

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

  useEffect(() => {
    const now = new Date()
    const reconciled = reconcileServerJobs(initialJobs, statusLedger.current)
    if (reconciled.acknowledgedMutationIds.size) {
      statusLedger.current.forEach((mutation, id) => {
        if (reconciled.acknowledgedMutationIds.has(mutation.mutationId)) {
          statusLedger.current.delete(id)
        }
      })
    }
    updateJobs(() => reconciled.jobs)
    setLastSynced(now.toISOString())
    // Deliberate overlap closes the server-render/hydration race. Re-seeing an
    // id is cheap because mergeGroupedJobs keys by id.
    linkedinCursor.current = new Date(now.getTime() - 60_000).toISOString()
  }, [initialJobs, updateJobs])

  // LinkedIn-only delta polling. A full router.refresh() is far too expensive
  // to run every few seconds; this endpoint returns only newly-actionable
  // LinkedIn rows and merges them into the current duplicate groups. Applied
  // state wins during merging, so an in-flight "new" response cannot resurrect
  // a job the user just marked Applied.
  useEffect(() => {
    let cancelled = false
    let inFlight = false

    const pollLinkedIn = async () => {
      if (cancelled || inFlight || document.visibilityState !== 'visible') return
      inFlight = true
      try {
        const res = await fetch(
          `/api/jobs/linkedin-updates?after=${encodeURIComponent(linkedinCursor.current)}`,
          { cache: 'no-store' },
        )
        if (!res.ok) return
        const body = await res.json() as { checkedAt: string, jobs: Job[] }
        if (cancelled) return
        if (body.jobs.length) {
          updateJobs(current => overlayStatusMutations(
            mergeGroupedJobs(current, body.jobs),
            statusLedger.current,
          ))
        }
        linkedinCursor.current = body.checkedAt
        setLastSynced(body.checkedAt)
      } catch {
        // The five-minute full refresh remains the reconciliation path. A
        // transient delta failure should not flash an error or disturb rows.
      } finally {
        inFlight = false
      }
    }

    void pollLinkedIn()
    const id = window.setInterval(() => { void pollLinkedIn() }, LINKEDIN_POLL_MS)
    return () => { cancelled = true; window.clearInterval(id) }
  }, [updateJobs])

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
  // important of three refresh paths: the focus listener below fires when you
  // return to the tab (rate-limited — see below), the toolbar has a manual
  // Refresh, and the
  // scrapers only produce new rows every 5-20 minutes anyway — so a 5-minute
  // poll now roughly matches the rate at which data can actually change.
  // Notifications are unaffected either way: ntfy pushes at classification
  // time and never waits on the dashboard.
  /* The focus path is rate-limited to the same FULL_POLL_MS as the interval.
     On a desktop that listener fires when you come back to the tab, which is
     what it was written for. On a phone it fires on every app switch, every
     lock and unlock, and every return from the browser's own UI — and each
     one is another 14.09 MB server fetch. A minute of switching between this
     and an email client was pulling tens of MB over cellular and stalling a
     mobile CPU that is already holding every row in memory.

     Rate-limiting rather than dropping the listener: coming back to the tab
     after a while should still refresh immediately, which is the behaviour
     the 5-minute interval above is explicitly relying on. Within the poll
     window the interval has it covered anyway, so the suppressed calls cost
     no freshness at all. */
  const lastRefresh = useRef(Date.now())
  useEffect(() => {
    const refresh = () => { lastRefresh.current = Date.now(); router.refresh() }
    const tick = () => { if (document.visibilityState === 'visible') refresh() }
    const onFocus = () => {
      if (document.visibilityState !== 'visible') return
      if (Date.now() - lastRefresh.current < FULL_POLL_MS) return
      refresh()
    }
    const id = setInterval(tick, FULL_POLL_MS)
    window.addEventListener('focus', onFocus)
    return () => { clearInterval(id); window.removeEventListener('focus', onFocus) }
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
    const group = jobsRef.current.find(j => groupMemberIds(j).includes(id))
    const ids = group ? groupMemberIds(group) : [id]

    // The ref is updated before React paints the disabled controls, closing
    // the tiny double-click window where two writes for one group could race
    // and reach Supabase out of order.
    if (ids.some((memberId) => statusLedger.current.get(memberId)?.phase === 'pending')) return

    const mutation: StatusMutation = {
      mutationId: ++mutationSequence.current,
      ids,
      desiredStatus: status,
      previousStatus: group?.status ?? 'new',
      phase: 'pending',
    }
    ids.forEach((memberId) => statusLedger.current.set(memberId, mutation))
    setPendingIds(current => {
      const next = new Set(current)
      ids.forEach((memberId) => next.add(memberId))
      return next
    })
    updateJobs(current => applyGroupStatus(current, ids, status))

    const settlePending = () => setPendingIds(current => {
      const next = new Set(current)
      ids.forEach((memberId) => {
        if (statusLedger.current.get(memberId)?.phase !== 'pending') next.delete(memberId)
      })
      return next
    })

    try {
      const res = await fetch(`/api/jobs/${id}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status, ids }),
      })
      const body = await res.json().catch(() => null) as {
        error?: string
        updatedRows?: Array<{ id: string, status: Status }>
      } | null
      if (!res.ok) throw new Error(body?.error ?? `HTTP ${res.status}`)

      const actual = new Map((body?.updatedRows ?? []).map(row => [row.id, row.status]))
      if (ids.some((memberId) => actual.get(memberId) !== status)) {
        throw new Error('The database response did not confirm every duplicate')
      }

      // A newer mutation would own these ids. This should be unreachable
      // because pending controls are disabled, but retaining the sequence
      // check makes late network responses harmless.
      if (ids.some((memberId) => statusLedger.current.get(memberId)?.mutationId !== mutation.mutationId)) {
        return
      }
      const confirmed = { ...mutation, phase: 'confirmed' as const }
      ids.forEach((memberId) => statusLedger.current.set(memberId, confirmed))
      settlePending()
      setToast(TOASTS[status] ?? 'Updated')
      setTimeout(() => setToast(null), 1200)
    } catch (error) {
      // Roll back this group only, and only if this request is still its newest
      // mutation. One failed job can no longer resurrect other successful
      // actions by restoring a captured whole-list snapshot.
      if (ids.every((memberId) => statusLedger.current.get(memberId)?.mutationId === mutation.mutationId)) {
        ids.forEach((memberId) => statusLedger.current.delete(memberId))
        updateJobs(current => applyGroupStatus(current, ids, mutation.previousStatus))
      }
      settlePending()
      setSaveError(`Failed to save: ${error instanceof Error ? error.message : 'unknown error'}`)
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
    /* This must be a HEIGHT, not a minimum height. With min-h-screen the
       flex column grows to the full table, so [data-scroll-root] never
       overflows and JobTable's windowing silently renders the whole data set.
       On the live-sized list that means thousands of mounted rows and body
       scrolling; on iOS it also puts the bottom rows behind browser chrome.
       .h-dvh supplies the 100vh -> 100dvh fallback used by the two drawers. */
    <div className="flex h-dvh overflow-hidden" style={{ background: 'var(--bg)' }}>
      <Sidebar
        view={view}
        counts={counts}
        onSelect={v => patch({ view: v, job: null })}
        personaLabel={personaLabel}
        personaSub={personaSub}
        onSignOut={async () => { await fetch('/api/auth/logout', { method: 'POST' }); router.push('/login'); router.refresh() }}
        open={navOpen}
        onClose={() => setNavOpen(false)}
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
          onMenu={() => setNavOpen(true)}
        />

        {saveError && (
          <div style={{ padding: 'var(--s2) var(--s4)', fontSize: 'var(--text-data)', color: 'var(--danger)',
                        borderBottom: '1px solid var(--border)' }}>
            {saveError}
          </div>
        )}

        {TRACKING_VIEWS.includes(view) && <ApplicationStats jobs={jobs} />}

        <div data-scroll-root className="flex-1 overflow-y-auto" style={{ background: 'var(--bg-surface)' }}>
          <JobTable
            rows={rows}
            cols={cols}
            selectedId={selectedId}
            pendingIds={pendingIds}
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
        <JobDrawer
          job={selected}
          pending={groupMemberIds(selected).some(id => pendingIds.has(id))}
          onClose={() => patch({ job: null })}
          onStatus={onStatus}
        />
      )}

      {toast && (
        <div role="status" className="fixed left-1/2 -translate-x-1/2 z-50"
             style={{ // iOS Safari's bottom toolbar overlays ~50-90px, which is
                      // where this 1.2s confirmation was being painted — so
                      // Save/Dismiss appeared to do nothing on a phone.
                      bottom: 'calc(var(--s6) + env(safe-area-inset-bottom))',
                      padding: 'var(--s2) var(--s4)', fontSize: 'var(--text-data)',
                      color: 'var(--fg)', background: 'var(--bg-raised)',
                      border: '1px solid var(--border-strong)', borderRadius: 'var(--radius)' }}>
          {toast}
        </div>
      )}
    </div>
  )
}
