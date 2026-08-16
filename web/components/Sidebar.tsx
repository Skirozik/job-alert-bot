'use client'

import type { ViewKey } from '@/lib/jobView'

/* Three groups, in the order the work actually flows:
     REVIEW   — the queue being worked
     TRACKING — where things go once acted on
     ARCHIVE  — reference, rarely opened
   Previously all eight of these were one flat band of identical chips, which
   is why nothing read as prioritised. */
const GROUPS: { label: string; items: { key: ViewKey; label: string }[] }[] = [
  { label: 'Review', items: [
    { key: 'to-apply', label: 'To apply' },
    { key: 'caveat',   label: 'With a caveat' },
    { key: 'my-list',  label: 'My list' },
  ]},
  { label: 'Tracking', items: [
    { key: 'applied',   label: 'Applied' },
    { key: 'saved',     label: 'Saved' },
    { key: 'dismissed', label: 'Dismissed' },
  ]},
  { label: 'Archive', items: [
    { key: 'ineligible', label: 'Ineligible' },
    { key: 'all',        label: 'All postings' },
  ]},
]

export function Sidebar({
  view, counts, onSelect, personaLabel, personaSub, onSignOut,
}: {
  view: ViewKey
  counts: Record<ViewKey, number>
  onSelect: (v: ViewKey) => void
  personaLabel?: string
  personaSub?: string
  onSignOut: () => void
}) {
  const initial = (personaLabel || '?').charAt(0).toUpperCase()

  return (
    <nav
      aria-label="Views"
      className="flex flex-col shrink-0 h-screen sticky top-0"
      style={{
        width: 'var(--rail-w)',
        background: 'var(--bg-rail)',
        borderRight: '1px solid var(--border)',
      }}
    >
      <div style={{ padding: 'var(--s5) var(--s4) var(--s4)' }}>
        <h1 style={{ fontSize: 'var(--text-head)', fontWeight: 600, color: 'var(--fg)' }}>
          Job Dashboard
        </h1>
      </div>

      <div className="flex-1 overflow-y-auto" style={{ paddingBottom: 'var(--s4)' }}>
        {GROUPS.map(group => (
          <div key={group.label} style={{ marginBottom: 'var(--s5)' }}>
            <div
              style={{
                fontSize: 'var(--text-meta)', letterSpacing: '0.06em', textTransform: 'uppercase',
                color: 'var(--fg-subtle)', padding: '0 var(--s4)', marginBottom: 'var(--s2)',
              }}
            >
              {group.label}
            </div>
            {group.items.map(item => {
              const on = view === item.key
              return (
                <button
                  key={item.key}
                  onClick={() => onSelect(item.key)}
                  aria-current={on ? 'page' : undefined}
                  className="w-full flex items-center justify-between text-left"
                  style={{
                    height: '32px',
                    padding: '0 var(--s4)',
                    fontSize: 'var(--text-data)',
                    color: on ? 'var(--fg)' : 'var(--fg-muted)',
                    background: on ? 'var(--bg-active)' : 'transparent',
                    // The active item is one of the three sanctioned accent uses.
                    borderLeft: `2px solid ${on ? 'var(--accent)' : 'transparent'}`,
                    transition: `background-color var(--dur) ease`,
                  }}
                >
                  <span className="truncate">{item.label}</span>
                  <span style={{ color: 'var(--fg-subtle)', fontVariantNumeric: 'tabular-nums' }}>
                    {counts[item.key]}
                  </span>
                </button>
              )
            })}
          </div>
        ))}
      </div>

      {/* Identity pinned to the bottom — keeps account controls out of the
          working area entirely. */}
      <div
        className="flex items-center gap-2"
        style={{ borderTop: '1px solid var(--border)', padding: 'var(--s3) var(--s4)' }}
      >
        <div
          className="flex items-center justify-center shrink-0 select-none"
          style={{
            width: 28, height: 28, borderRadius: 'var(--radius)',
            background: 'var(--bg-active)', color: 'var(--fg-muted)',
            fontSize: 'var(--text-meta)', fontWeight: 600,
          }}
          aria-hidden
        >
          {initial}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate" style={{ fontSize: 'var(--text-data)', color: 'var(--fg)' }}>
            {personaLabel ?? 'Signed in'}
          </div>
          {personaSub && (
            <div className="truncate" style={{ fontSize: 'var(--text-meta)', color: 'var(--fg-subtle)' }}>
              {personaSub}
            </div>
          )}
        </div>
        <button
          onClick={onSignOut}
          title="Sign out"
          aria-label="Sign out"
          style={{ fontSize: 'var(--text-meta)', color: 'var(--fg-subtle)', padding: 'var(--s1)' }}
        >
          Sign out
        </button>
      </div>
    </nav>
  )
}
