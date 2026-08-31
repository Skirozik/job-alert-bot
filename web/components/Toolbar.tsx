'use client'

import { useEffect, useRef, useState } from 'react'
import { IconSearch, IconRefresh, IconChevron, IconMenu } from './icons'
import type { RoleFilter, SourceFilter, DateFilter } from '@/lib/jobView'
import type { StarFilter } from '@/lib/goldStar'
import { useIsMobile } from '@/lib/useMediaQuery'

function Dropdown<T extends string>({
  label, value, options, onChange,
}: { label: string; value: T; options: { key: T; label: string }[]; onChange: (v: T) => void }) {
  const [open, setOpen] = useState(false)
  const isMobile = useIsMobile()
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    // pointerdown, not mousedown: it fires for mouse, touch and pen alike.
    // iOS Safari does not reliably synthesise mouse events for taps on
    // non-interactive elements (the page background, the table body), so the
    // mousedown version left filter menus stuck open on a phone.
    const h = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', h)
    return () => document.removeEventListener('pointerdown', h)
  }, [])
  const active = options.find(o => o.key === value)
  const isDefault = value === options[0].key
  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="tap flex items-center gap-1 shrink-0"
        style={{
          minHeight: isMobile ? 44 : 28, height: isMobile ? undefined : 28,
          padding: isMobile ? '0 var(--s3)' : '0 var(--s2)',
          fontSize: 'var(--text-data)',
          // An active filter swaps the short label for the full option text
          // ("Past 30 days"), which otherwise widens the bar and can wrap
          // inside the button.
          whiteSpace: 'nowrap', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis',
          color: isDefault ? 'var(--fg-muted)' : 'var(--fg)',
          background: 'var(--bg-input)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
        }}
      >
        <span>{isDefault ? label : active?.label}</span>
        <IconChevron dir={open ? 'up' : 'down'} />
      </button>
      {open && (
        <div
          role="listbox"
          className="absolute right-0 z-30"
          style={{
            top: 'calc(100% + var(--s1))',
            minWidth: 'min(160px, calc(100vw - var(--s5)))',
            maxWidth: 'calc(100vw - var(--s5))',
            background: 'var(--bg-raised)', border: '1px solid var(--border-strong)',
            borderRadius: 'var(--radius)', padding: 'var(--s1)',
          }}
        >
          {options.map(o => (
            <button
              key={o.key}
              role="option"
              aria-selected={o.key === value}
              onClick={() => { onChange(o.key); setOpen(false) }}
              className="w-full text-left"
              style={{
                display: 'flex', alignItems: 'center',
                minHeight: isMobile ? 44 : 28,
                padding: '0 var(--s2)', fontSize: 'var(--text-data)',
                color: o.key === value ? 'var(--fg)' : 'var(--fg-muted)',
                background: o.key === value ? 'var(--bg-active)' : 'transparent',
                borderRadius: 'var(--radius)',
              }}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function Toolbar({
  search, onSearch, role, onRole, source, onSource, date, onDate, star, onStar,
  onRefresh, lastSynced, showSource, onMenu,
}: {
  search: string; onSearch: (v: string) => void
  role: RoleFilter; onRole: (v: RoleFilter) => void
  source: SourceFilter; onSource: (v: SourceFilter) => void
  date: DateFilter; onDate: (v: DateFilter) => void
  star: StarFilter; onStar: (v: StarFilter) => void
  onRefresh: () => void
  lastSynced: string
  showSource: boolean
  /* Opens the view rail. Mobile only — on desktop the rail is always on
     screen, so the button is not rendered at all. */
  onMenu?: () => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const isMobile = useIsMobile()

  // "/" focuses search, unless the user is already typing somewhere.
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.metaKey || e.ctrlKey) return
      const t = e.target as HTMLElement
      if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable) return
      e.preventDefault()
      inputRef.current?.focus()
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  return (
    <div
      /* Desktop keeps one fixed-height row. Mobile wraps, because search plus
         three dropdowns plus refresh cannot share 390px on one line — without
         wrapping they either overflow or crush the search box to nothing. */
      className={
        isMobile
          ? 'flex items-center gap-2 shrink-0 flex-wrap safe-x'
          : 'flex items-center gap-2 shrink-0'
      }
      style={{
        height: isMobile ? 'auto' : 40,
        padding: isMobile ? 'var(--s2) var(--s3)' : '0 var(--s4)',
        borderBottom: '1px solid var(--border)', background: 'var(--bg)',
      }}
    >
      {/* Mobile row 1: menu + search, claiming the full width via basis-full so
          the three shrink-0 dropdowns are forced to wrap onto row 2. Without
          this they win the space and the search field collapses to about the
          width of its own icon. On desktop this wrapper is transparent —
          `contents` makes its children lay out as direct flex items of the
          toolbar, exactly as they did before. */}
      <div className={isMobile ? 'flex items-center gap-2 basis-full min-w-0' : 'contents'}>
        {isMobile && onMenu && (
          <button
            onClick={onMenu}
            aria-label="Open views menu"
            title="Views"
            className="tap flex items-center justify-center shrink-0"
            style={{
              color: 'var(--fg-muted)', background: 'var(--bg-input)',
              border: '1px solid var(--border)', borderRadius: 'var(--radius)',
            }}
          >
            <IconMenu />
          </button>
        )}

        {/* max-w-md is a desktop constraint; on mobile the field should use
            whatever width is left after the menu button. */}
        <div className={isMobile ? 'relative flex-1 min-w-0' : 'relative flex-1 max-w-md'}>
        <span
          className="absolute inset-y-0 left-0 flex items-center pointer-events-none"
          style={{ paddingLeft: 'var(--s2)', color: 'var(--fg-subtle)' }}
        >
          <IconSearch />
        </span>
        <input
          ref={inputRef}
          value={search}
          onChange={e => onSearch(e.target.value)}
          onKeyDown={e => { if (e.key === 'Escape') { onSearch(''); e.currentTarget.blur() } }}
          placeholder={isMobile ? 'Search company or role…' : 'Search company or role…    /'}
          aria-label="Search company or role"
          className="w-full"
          style={{
            height: isMobile ? 44 : 28, paddingLeft: 28, paddingRight: 'var(--s2)',
            // globals.css forces 16px on inputs under the breakpoint; without
            // it iOS Safari zooms the page on focus and never zooms back.
            fontSize: 'var(--text-data)', color: 'var(--fg)',
            background: 'var(--bg-input)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
          }}
        />
        </div>
      </div>

      {showSource && (
        <Dropdown label="Source" value={source} onChange={onSource} options={[
          { key: 'all', label: 'Any source' },
          { key: 'direct', label: 'Direct only' },
          { key: 'linkedin', label: 'LinkedIn only' },
        ]} />
      )}
      <Dropdown label="Role" value={role} onChange={onRole} options={[
        { key: 'all', label: 'All roles' },
        { key: 'internships', label: 'Internships' },
        { key: 'entry-level', label: 'Entry-level' },
      ]} />
      {/* Gold star. A filter rather than a sidebar view -- see matchesStar in
          jobView.ts for why. Without it the stars are unfindable among ~1,550
          To-apply rows, which would make the badge decorative. */}
      <Dropdown label="Star" value={star} onChange={onStar} options={[
        { key: 'all', label: 'All jobs' },
        { key: 'starred', label: 'Gold star only' },
      ]} />
      <Dropdown label="Discovered" value={date} onChange={onDate} options={[
        { key: 'all', label: 'Any time' },
        { key: '24h', label: 'Past 24 hours' },
        { key: '7d', label: 'Past 7 days' },
        { key: '30d', label: 'Past 30 days' },
      ]} />

      <div className="flex items-center gap-2 shrink-0" style={{ marginLeft: 'auto' }}>
        <span style={{
          // 11px --fg-subtle on --bg is marginal on a phone; and this label is
          // the widest optional thing in the bar, so it is what pushes Refresh
          // off the edge. Bigger and non-wrapping on mobile.
          fontSize: isMobile ? 'var(--text-data)' : 'var(--text-meta)',
          color: 'var(--fg-subtle)', whiteSpace: 'nowrap',
        }}>
          Synced {lastSynced}
        </span>
        <button
          onClick={onRefresh}
          title="Refresh now — also refreshes automatically every 5 min"
          aria-label="Refresh now"
          className="tap flex items-center justify-center gap-1 shrink-0"
          style={{
            minHeight: isMobile ? 44 : 28, height: isMobile ? undefined : 28,
            padding: '0 var(--s2)', fontSize: 'var(--text-data)',
            color: 'var(--fg-muted)', background: 'var(--bg-input)',
            border: '1px solid var(--border)', borderRadius: 'var(--radius)',
          }}
        >
          {/* The word is dropped on mobile — the glyph plus aria-label carry
              it, and the row is already the tightest thing on the screen. */}
          <IconRefresh />{!isMobile && ' Refresh'}
        </button>
      </div>
    </div>
  )
}
