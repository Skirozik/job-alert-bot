'use client'

import { useEffect } from 'react'
import type { ViewKey } from '@/lib/jobView'
import { useIsMobile } from '@/lib/useMediaQuery'

/* Two groups, in the order the work actually flows:
     REVIEW   — the queue being worked
     TRACKING — where things go once acted on
   The old ARCHIVE group (Ineligible, All postings) is gone. It browsed 518 of
   51,151 rows with search running client-side over only that slice, so it
   could not actually find a wrongly-rejected job — which was the only reason
   to have it. Dropping it also stops fetching 500 ineligible rows on every
   refresh (0.52 MB of 5.38 MB). */
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
  /* OUTCOMES exists because 560 applications had been sent with no record of
     what came back, which made the only questions worth asking -- does direct-
     to-ATS beat LinkedIn, does the AI resume convert -- unanswerable. */
  { label: 'Outcomes', items: [
    { key: 'heard-back', label: 'Heard back' },
    { key: 'interview',  label: 'Interview' },
    { key: 'offer',      label: 'Offer' },
    { key: 'rejected',   label: 'Rejected' },
  ]},
]

export function Sidebar({
  view, counts, onSelect, personaLabel, personaSub, onSignOut,
  open = false, onClose,
}: {
  view: ViewKey
  counts: Record<ViewKey, number>
  onSelect: (v: ViewKey) => void
  personaLabel?: string
  personaSub?: string
  onSignOut: () => void
  /* Mobile only. On desktop the rail is always present and these are ignored,
     which is why they default to a closed/no-op pair — the desktop call site
     does not have to know this component has an open state at all. */
  open?: boolean
  onClose?: () => void
}) {
  const initial = (personaLabel || '?').charAt(0).toUpperCase()
  const isMobile = useIsMobile()

  // Escape closes the drawer, matching the drawer and dropdown behaviour
  // elsewhere in the app. Bound only while it is actually open so it cannot
  // swallow Escape from anything else.
  useEffect(() => {
    if (!isMobile || !open) return
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [isMobile, open, onClose])

  // Stop the page behind the overlay from scrolling under the finger.
  useEffect(() => {
    if (!isMobile || !open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [isMobile, open])

  /* Desktop: an in-flow sticky column. Mobile: an overlay that slides in over
     the content, so it costs zero horizontal space when closed. Transform
     rather than width/display so it animates on the compositor and so the
     nav stays in the DOM (and thus focusable/measurable) either way. */
  const shell: React.CSSProperties = isMobile
    ? {
        position: 'fixed', insetBlock: 0, left: 0, zIndex: 60,
        width: 'var(--rail-w)',
        transform: open ? 'translateX(0)' : 'translateX(-100%)',
        transition: 'transform var(--dur) ease',
        background: 'var(--bg-rail)',
        borderRight: '1px solid var(--border)',
      }
    : {
        width: 'var(--rail-w)',
        background: 'var(--bg-rail)',
        borderRight: '1px solid var(--border)',
      }

  return (
    <>
      {/* Backdrop. Mobile-only, and only while open, so it can never
          intercept clicks on desktop. */}
      {isMobile && open && (
        <div
          onClick={onClose}
          aria-hidden
          className="fixed inset-0"
          style={{ zIndex: 55, background: 'rgba(0,0,0,0.6)' }}
        />
      )}

      <nav
        aria-label="Views"
        aria-hidden={isMobile && !open}
        className={
          isMobile
            ? 'flex flex-col safe-t safe-b'
            : 'flex flex-col shrink-0 h-dvh sticky top-0'
        }
        style={shell}
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
                  onClick={() => { onSelect(item.key); if (isMobile) onClose?.() }}
                  aria-current={on ? 'page' : undefined}
                  className="w-full flex items-center justify-between text-left"
                  style={{
                    // 32px is a fine mouse target and a poor thumb one.
                    height: isMobile ? '44px' : '32px',
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
                  <span
                    className="shrink-0"
                    style={{ color: 'var(--fg-subtle)', fontVariantNumeric: 'tabular-nums' }}
                  >
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
          className="tap shrink-0 flex items-center justify-center"
          style={{ fontSize: 'var(--text-meta)', color: 'var(--fg-subtle)', padding: 'var(--s1)' }}
        >
          Sign out
        </button>
      </div>
      </nav>
    </>
  )
}
