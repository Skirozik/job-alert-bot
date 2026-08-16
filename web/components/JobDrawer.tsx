'use client'

import { useEffect, useRef } from 'react'
import type { Job, Status } from '@/types/job'
import type { Grouped } from '@/lib/dupes'
import { splitLocations, fullTimestamp, isDirect } from '@/lib/jobView'
import { IconApplyFilled, IconApplyOutline, IconClose, IconBolt } from './icons'

const TIER_LABEL: Record<string, string> = {
  APPLY: 'Apply', APPLY_CAVEAT: 'Apply — caveat', INELIGIBLE: 'Ineligible',
}
const TIER_COLOR: Record<string, string> = {
  APPLY: 'var(--tier-clean)', APPLY_CAVEAT: 'var(--tier-caveat)', INELIGIBLE: 'var(--tier-inelig)',
}

function Action({
  label, onClick, disabled, primary,
}: { label: string; onClick: () => void; disabled?: boolean; primary?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="w-full"
      style={{
        height: 32, fontSize: 'var(--text-data)', borderRadius: 'var(--radius)',
        // The primary action is one of the three sanctioned accent uses.
        color: primary ? 'var(--accent)' : 'var(--fg-muted)',
        background: primary ? 'var(--accent-bg)' : 'var(--bg-active)',
        border: `1px solid ${primary ? 'var(--accent-border)' : 'var(--border)'}`,
        opacity: disabled ? 0.4 : 1,
        cursor: disabled ? 'default' : 'pointer',
      }}
    >
      {label}
    </button>
  )
}

export function JobDrawer({
  job, onClose, onStatus, resumeHref,
}: {
  job: Grouped
  onClose: () => void
  onStatus: (id: string, s: Status) => void
  resumeHref?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const locations = splitLocations(job.location)
  const status = job.status ?? 'new'
  const easy = job.is_easy_apply
  const applyHref = easy ? job.url : (job.apply_url ?? job.url)

  // Escape closes. Arrow keys are handled by the table so they keep working
  // while the drawer is open — that is the whole point of the drawer, triage
  // without touching the mouse.
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  return (
    <aside
      ref={ref}
      role="complementary"
      aria-label="Job detail"
      className="drawer fixed right-0 top-0 h-screen z-40 flex flex-col"
      style={{
        width: 'var(--drawer-w)',
        background: 'var(--bg-raised)',
        borderLeft: '1px solid var(--border-strong)',
        // The one shadow in the app. It floats; nothing else does.
        boxShadow: '-16px 0 40px rgba(0,0,0,0.45)',
      }}
    >
      <div
        className="flex items-start gap-2 shrink-0"
        style={{ padding: 'var(--s4)', borderBottom: '1px solid var(--border)' }}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2" style={{ marginBottom: 'var(--s1)' }}>
            <span style={{ fontSize: 'var(--text-data)', color: 'var(--fg-muted)' }}>{job.company}</span>
            {isDirect(job) && (
              <span
                className="inline-flex items-center gap-1"
                style={{ fontSize: 'var(--text-meta)', color: 'var(--accent)' }}
                title="Caught directly from the company's own ATS, ahead of LinkedIn"
              >
                <IconBolt /> Direct
              </span>
            )}
          </div>
          <h2 style={{ fontSize: 'var(--text-head)', fontWeight: 600, color: 'var(--fg)', lineHeight: 1.35 }}>
            {job.title}
          </h2>
        </div>
        <button onClick={onClose} aria-label="Close" title="Close (Esc)" style={{ color: 'var(--fg-subtle)', padding: 'var(--s1)' }}>
          <IconClose />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto" style={{ padding: 'var(--s4)' }}>
        <dl className="grid grid-cols-[92px_1fr]" style={{ gap: 'var(--s2) var(--s3)', fontSize: 'var(--text-data)' }}>
          <dt style={{ color: 'var(--fg-subtle)' }}>Locations</dt>
          <dd style={{ color: 'var(--fg)' }}>
            {locations.length ? locations.join(' · ') : '—'}
          </dd>

          <dt style={{ color: 'var(--fg-subtle)' }}>Discovered</dt>
          <dd style={{ color: 'var(--fg)' }}>{fullTimestamp(job.found_at)}</dd>

          {job.salary && (<>
            <dt style={{ color: 'var(--fg-subtle)' }}>Salary</dt>
            {/* The original string, verbatim — the table's compact form is for
                scanning and is not a replacement for the source. */}
            <dd style={{ color: 'var(--fg)' }}>{job.salary}</dd>
          </>)}

          {job.suggested_resume && job.suggested_resume !== 'N/A' && (<>
            <dt style={{ color: 'var(--fg-subtle)' }}>Resume</dt>
            <dd>
              {resumeHref
                ? <a href={resumeHref} target="_blank" rel="noopener noreferrer"
                     style={{ color: 'var(--accent)' }}>{job.suggested_resume} — open PDF</a>
                : <span style={{ color: 'var(--fg)' }}>{job.suggested_resume}</span>}
            </dd>
          </>)}
        </dl>

        {/* Reasoning now shows for EVERY tier, including INELIGIBLE. Under the
            old taxonomy ineligible meant discarded so it was hidden; now it is
            how a wrong ruling gets caught. */}
        <div style={{ marginTop: 'var(--s5)' }}>
          <div className="flex items-center gap-2" style={{ marginBottom: 'var(--s2)' }}>
            <span style={{ width: 3, height: 12, background: TIER_COLOR[job.tier], borderRadius: 1 }} aria-hidden />
            <span style={{ fontSize: 'var(--text-meta)', letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--fg-subtle)' }}>
              {TIER_LABEL[job.tier] ?? job.tier}
            </span>
          </div>
          <p style={{ fontSize: 'var(--text-data)', lineHeight: 1.6, color: 'var(--fg-muted)' }}>
            {job.reason || 'No reasoning recorded.'}
          </p>
        </div>

        {job.duplicates && job.duplicates.length > 0 && (
          <div style={{ marginTop: 'var(--s5)' }}>
            <div style={{ fontSize: 'var(--text-meta)', letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--fg-subtle)', marginBottom: 'var(--s2)' }}>
              {job.duplicates.length} duplicate posting{job.duplicates.length > 1 ? 's' : ''}
            </div>
            {job.duplicates.map(d => (
              <a key={d.id} href={d.url} target="_blank" rel="noopener noreferrer" title={d.title}
                 className="block truncate"
                 style={{ fontSize: 'var(--text-data)', color: 'var(--fg-muted)', padding: '2px 0' }}>
                {d.id.startsWith('gh:') ? 'GitHub tracker' : d.id.startsWith('ats:') ? 'company ATS' : 'LinkedIn'} version
              </a>
            ))}
          </div>
        )}
      </div>

      <div className="shrink-0" style={{ padding: 'var(--s4)', borderTop: '1px solid var(--border)' }}>
        <a
          href={applyHref}
          target="_blank"
          rel="noopener noreferrer"
          className="w-full flex items-center justify-center gap-2"
          style={{
            height: 34, fontSize: 'var(--text-data)', borderRadius: 'var(--radius)',
            color: 'var(--accent)', background: 'var(--accent-bg)',
            border: '1px solid var(--accent-border)', marginBottom: 'var(--s2)',
          }}
        >
          {easy ? <IconApplyFilled /> : <IconApplyOutline />}
          {easy ? 'Easy Apply on LinkedIn' : 'Open original posting'}
        </a>
        <div className="grid grid-cols-3" style={{ gap: 'var(--s2)' }}>
          <Action label="Applied"  onClick={() => onStatus(job.id, 'applied')}   disabled={status === 'applied'} />
          <Action label="Save"     onClick={() => onStatus(job.id, 'saved')}     disabled={status === 'saved'} />
          <Action label="Dismiss"  onClick={() => onStatus(job.id, 'dismissed')} disabled={status === 'dismissed'} />
        </div>
        {status !== 'new' && (
          <div style={{ marginTop: 'var(--s2)' }}>
            <Action label="Reset to new" onClick={() => onStatus(job.id, 'new')} />
          </div>
        )}
      </div>
    </aside>
  )
}
