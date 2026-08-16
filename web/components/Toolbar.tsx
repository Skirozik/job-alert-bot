'use client'

import { useEffect, useRef, useState } from 'react'
import { IconSearch, IconRefresh, IconChevron } from './icons'
import type { RoleFilter, SourceFilter, DateFilter } from '@/lib/jobView'

function Dropdown<T extends string>({
  label, value, options, onChange,
}: { label: string; value: T; options: { key: T; label: string }[]; onChange: (v: T) => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  const active = options.find(o => o.key === value)
  const isDefault = value === options[0].key
  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-1"
        style={{
          height: 28, padding: '0 var(--s2)', fontSize: 'var(--text-data)',
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
            top: 'calc(100% + var(--s1))', minWidth: 160,
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
                height: 28, padding: '0 var(--s2)', fontSize: 'var(--text-data)',
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
  search, onSearch, role, onRole, source, onSource, date, onDate,
  onRefresh, lastSynced, showSource,
}: {
  search: string; onSearch: (v: string) => void
  role: RoleFilter; onRole: (v: RoleFilter) => void
  source: SourceFilter; onSource: (v: SourceFilter) => void
  date: DateFilter; onDate: (v: DateFilter) => void
  onRefresh: () => void
  lastSynced: string
  showSource: boolean
}) {
  const inputRef = useRef<HTMLInputElement>(null)

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
      className="flex items-center gap-2 shrink-0"
      style={{
        height: 40, padding: '0 var(--s4)',
        borderBottom: '1px solid var(--border)', background: 'var(--bg)',
      }}
    >
      <div className="relative flex-1 max-w-md">
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
          placeholder="Search company or role…    /"
          aria-label="Search company or role"
          className="w-full"
          style={{
            height: 28, paddingLeft: 28, paddingRight: 'var(--s2)',
            fontSize: 'var(--text-data)', color: 'var(--fg)',
            background: 'var(--bg-input)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
          }}
        />
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
      <Dropdown label="Discovered" value={date} onChange={onDate} options={[
        { key: 'all', label: 'Any time' },
        { key: '24h', label: 'Past 24 hours' },
        { key: '7d', label: 'Past 7 days' },
        { key: '30d', label: 'Past 30 days' },
      ]} />

      <div className="flex items-center gap-2" style={{ marginLeft: 'auto' }}>
        <span style={{ fontSize: 'var(--text-meta)', color: 'var(--fg-subtle)' }}>
          Synced {lastSynced}
        </span>
        <button
          onClick={onRefresh}
          title="Refresh now — also refreshes automatically every 60s"
          className="flex items-center gap-1"
          style={{
            height: 28, padding: '0 var(--s2)', fontSize: 'var(--text-data)',
            color: 'var(--fg-muted)', background: 'var(--bg-input)',
            border: '1px solid var(--border)', borderRadius: 'var(--radius)',
          }}
        >
          <IconRefresh /> Refresh
        </button>
      </div>
    </div>
  )
}
