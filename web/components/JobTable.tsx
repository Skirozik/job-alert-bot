'use client'

import { useState, useEffect, useRef } from 'react'
import { CompanyLogo } from './CompanyLogo'
import type { Job, Status } from '@/types/job'
import type { Grouped } from '@/lib/dupes'
import {
  compactSalary, splitLocations, relativeTime, fullTimestamp, isDirect,
  type SortKey, type SortDir, type OptionalCol,
} from '@/lib/jobView'
import {
  IconApplyFilled, IconApplyOutline, IconApplied, IconSave, IconDismiss,
  IconReset, IconBolt, IconChevron,
} from './icons'

const TIER_BAR: Record<string, string> = {
  APPLY: 'var(--tier-clean)', APPLY_CAVEAT: 'var(--tier-caveat)', INELIGIBLE: 'var(--tier-inelig)',
}

function IconBtn({
  label, onClick, disabled, children,
}: { label: string; onClick: (e: React.MouseEvent) => void; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className="flex items-center justify-center shrink-0"
      style={{
        width: 24, height: 24, borderRadius: 'var(--radius)',
        color: 'var(--fg-subtle)', opacity: disabled ? 0.3 : 1,
        cursor: disabled ? 'default' : 'pointer',
      }}
    >
      {children}
    </button>
  )
}

function SortHead({
  label, col, sort, dir, onSort, style,
}: {
  label: string; col?: SortKey; sort: SortKey; dir: SortDir
  onSort: (c: SortKey) => void; style: React.CSSProperties
}) {
  const active = col && sort === col
  return (
    <div style={style} className="flex items-center gap-1 select-none">
      {col ? (
        <button
          onClick={() => onSort(col)}
          className="flex items-center gap-1"
          style={{ color: active ? 'var(--fg)' : 'var(--fg-subtle)', fontSize: 'var(--text-meta)' }}
          aria-sort={active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}
        >
          {label}
          {active && <IconChevron dir={dir === 'asc' ? 'up' : 'down'} />}
        </button>
      ) : (
        <span style={{ color: 'var(--fg-subtle)', fontSize: 'var(--text-meta)' }}>{label}</span>
      )}
    </div>
  )
}

export function JobTable({
  rows, cols, selectedId, onSelect, onStatus, sort, dir, onSort, emptyMessage,
}: {
  rows: Grouped[]
  cols: Record<OptionalCol, boolean>
  selectedId: string | null
  onSelect: (id: string) => void
  onStatus: (id: string, s: Status) => void
  sort: SortKey; dir: SortDir; onSort: (c: SortKey) => void
  emptyMessage: string
}) {
  // Fixed column widths per the spec; Role takes the remainder.
  const W = {
    company: '200px',
    role: 'minmax(0,1fr)',
    location: '160px',
    source: cols.source ? '90px' : '0px',
    salary: cols.salary ? '90px' : '0px',
    resume: cols.resume ? '120px' : '0px',
    found: '110px',
    actions: '120px',
  }
  const grid = [W.company, W.role, W.location, W.source, W.salary, W.resume, W.found, W.actions].join(' ')

  /* Windowing. "All postings" mounted all 2262 rows: 76,545 DOM nodes, a
     99,592px document and 3.25s to render, well past the ~15,000 nodes where
     Chrome degrades. Rows are a fixed 45px (44 + 1px divider), which makes the
     visible slice pure arithmetic — no measurement pass, no library.
     Everything above and below is replaced by a single spacer div, so scroll
     height and scrollbar position stay exact. */
  const ROW_PX = 45
  const OVERSCAN = 8
  const scrollRef = useRef<HTMLElement | null>(null)
  const [range, setRange] = useState({ start: 0, end: 60 })

  useEffect(() => {
    // The scroll container is the ancestor that actually overflows.
    const el = (scrollRef.current?.closest('[data-scroll-root]') as HTMLElement) ?? null
    const recompute = () => {
      const top = el ? el.scrollTop : 0
      const h = el ? el.clientHeight : 800
      const start = Math.max(0, Math.floor(top / ROW_PX) - OVERSCAN)
      const end = Math.min(rows.length, Math.ceil((top + h) / ROW_PX) + OVERSCAN)
      setRange(prev => (prev.start === start && prev.end === end ? prev : { start, end }))
    }
    recompute()
    el?.addEventListener('scroll', recompute, { passive: true })
    window.addEventListener('resize', recompute)
    return () => { el?.removeEventListener('scroll', recompute); window.removeEventListener('resize', recompute) }
  }, [rows.length])

  const visible = rows.slice(range.start, range.end)
  const padTop = range.start * ROW_PX
  const padBottom = Math.max(0, (rows.length - range.end) * ROW_PX)
  const cell: React.CSSProperties = { padding: '0 var(--s3)', minWidth: 0 }

  if (!rows.length) {
    return (
      <div style={{ padding: 'var(--s8) var(--s4)', fontSize: 'var(--text-data)', color: 'var(--fg-subtle)' }}>
        {emptyMessage}
      </div>
    )
  }

  return (
    <div role="table" aria-label="Jobs" ref={scrollRef as any}>
      {/* Header. 28px so it costs as little vertical space as possible. */}
      <div
        role="row"
        className="grid sticky top-0 z-10 items-center"
        style={{
          gridTemplateColumns: grid, height: 24,
          background: 'var(--bg)', borderBottom: '1px solid var(--border)',
          paddingLeft: 3, // matches the tier bar so headers line up with cells
        }}
      >
        <SortHead label="Company"    col="company"  sort={sort} dir={dir} onSort={onSort} style={cell} />
        <SortHead label="Role"       col="title"    sort={sort} dir={dir} onSort={onSort} style={cell} />
        <SortHead label="Location"   col="location" sort={sort} dir={dir} onSort={onSort} style={cell} />
        {cols.source && <SortHead label="Source" sort={sort} dir={dir} onSort={onSort} style={cell} />}
        {cols.salary && <SortHead label="Salary" sort={sort} dir={dir} onSort={onSort} style={{ ...cell, textAlign: 'right' }} />}
        {cols.resume && <SortHead label="Resume" sort={sort} dir={dir} onSort={onSort} style={cell} />}
        <SortHead label="Discovered" col="found_at" sort={sort} dir={dir} onSort={onSort} style={cell} />
        <span />
      </div>

      {padTop > 0 && <div style={{ height: padTop }} aria-hidden />}
      {visible.map(job => {
        const locs = splitLocations(job.location)
        const status = job.status ?? 'new'
        const easy = job.is_easy_apply
        const applyHref = easy ? job.url : (job.apply_url ?? job.url)
        const inelig = job.tier === 'INELIGIBLE'
        // Muted FOREGROUND, not an opacity filter — 40% opacity on a dark
        // theme is close to unreadable, and these rows still need to be
        // readable when deliberately opened.
        const fg = inelig ? 'var(--fg-dim)' : 'var(--fg)'
        const fgMuted = inelig ? 'var(--fg-dim)' : 'var(--fg-muted)'

        return (
          <div
            key={job.id}
            role="row"
            tabIndex={0}
            data-selected={selectedId === job.id}
            data-job-row={job.id}
            onClick={() => onSelect(job.id)}
            onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(job.id) } }}
            className="row-hit grid items-center cursor-default"
            style={{
              gridTemplateColumns: grid,
              height: 'var(--row-h)',
              borderBottom: '1px solid var(--border)',
              borderLeft: `3px solid ${TIER_BAR[job.tier] ?? 'transparent'}`,
            }}
          >
            {/* Company */}
            <div style={cell} className="flex items-center gap-2">
              <CompanyLogo src={job.logo_url} company={job.company} />
              <span className="truncate" style={{ color: fg }} title={job.company}>{job.company}</span>
              {status === 'new' && (
                <span aria-label="New" title="New" className="shrink-0"
                      style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--status-new)' }} />
              )}
            </div>

            {/* Role — truncates, full title in the tooltip */}
            <div style={cell}>
              <span className="block truncate" style={{ color: fg }} title={job.title}>{job.title}</span>
            </div>

            {/* Location — first + overflow count, full list in the drawer */}
            <div style={cell} className="flex items-center gap-1 min-w-0">
              <span className="truncate" style={{ color: fgMuted }} title={locs.join(' · ')}>
                {locs[0] ?? '—'}
              </span>
              {locs.length > 1 && (
                <span className="shrink-0" style={{ color: 'var(--fg-subtle)', fontSize: 'var(--text-meta)' }}
                      title={locs.join(' · ')}>+{locs.length - 1}</span>
              )}
            </div>

            {cols.source && (
              <div style={cell}>
                {isDirect(job) && (
                  <span className="inline-flex items-center gap-1"
                        style={{ fontSize: 'var(--text-meta)', color: 'var(--fg-muted)',
                                 background: 'var(--bg-active)', border: '1px solid var(--border)',
                                 borderRadius: 'var(--radius)', padding: '1px 6px' }}
                        title="Caught directly from the company's own ATS, ahead of LinkedIn">
                    <IconBolt /> Direct
                  </span>
                )}
              </div>
            )}

            {cols.salary && (
              <div style={{ ...cell, textAlign: 'right' }}>
                <span className="truncate inline-block max-w-full"
                      style={{ color: fgMuted, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}
                      title={job.salary ?? ''}>
                  {compactSalary(job.salary)}
                </span>
              </div>
            )}

            {cols.resume && (
              <div style={cell}>
                {job.suggested_resume && job.suggested_resume !== 'N/A' && (
                  <span className="truncate inline-block max-w-full"
                        style={{ fontSize: 'var(--text-meta)', color: 'var(--fg-muted)',
                                 background: 'var(--bg-active)', border: '1px solid var(--border)',
                                 borderRadius: 'var(--radius)', padding: '1px 6px' }}>
                    {job.suggested_resume}
                  </span>
                )}
              </div>
            )}

            <div style={cell}>
              <span style={{ color: 'var(--fg-subtle)' }} title={fullTimestamp(job.found_at)}>
                {relativeTime(job.found_at)}
              </span>
            </div>

            {/* Actions: hidden until hover, always painted on the selected row,
                always keyboard-reachable (focus-within keeps them visible). */}
            <div style={cell} className="row-actions flex items-center justify-end gap-1"
                 onClick={e => e.stopPropagation()}>
              <a href={applyHref} target="_blank" rel="noopener noreferrer"
                 title={easy ? 'Easy Apply' : 'Apply (external)'}
                 aria-label={easy ? 'Easy Apply' : 'Apply (external)'}
                 className="flex items-center justify-center shrink-0"
                 style={{ width: 24, height: 24, borderRadius: 'var(--radius)', color: 'var(--accent)' }}>
                {easy ? <IconApplyFilled /> : <IconApplyOutline />}
              </a>
              <IconBtn label="Mark applied" disabled={status === 'applied'}
                       onClick={() => onStatus(job.id, 'applied')}><IconApplied /></IconBtn>
              <IconBtn label="Save" disabled={status === 'saved'}
                       onClick={() => onStatus(job.id, 'saved')}><IconSave /></IconBtn>
              <IconBtn label="Dismiss" disabled={status === 'dismissed'}
                       onClick={() => onStatus(job.id, 'dismissed')}><IconDismiss /></IconBtn>
              {status !== 'new' && (
                <IconBtn label="Reset to new" onClick={() => onStatus(job.id, 'new')}><IconReset /></IconBtn>
              )}
            </div>
          </div>
        )
      })}
      {padBottom > 0 && <div style={{ height: padBottom }} aria-hidden />}
    </div>
  )
}
