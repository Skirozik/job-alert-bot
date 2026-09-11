'use client'

import { useCallback, useEffect, useState } from 'react'

/**
 * The tailored-resume panel in the job drawer.
 *
 * Shows the PLAN before any PDF exists, because the plan is the reviewable
 * artifact: which bullets were chosen, the ordering signal, the structural
 * analogue, and what the resume deliberately does not claim. A PDF only tells
 * you the outcome; the plan tells you the reasoning, and the reasoning is what
 * you would argue with.
 *
 * `analogue` is the field to read hardest. It is the one part of the pipeline
 * a model will produce confidently even when there is nothing there, and a
 * weak analogue reads as a stretch. Null is a good answer and is rendered as
 * one rather than hidden.
 *
 * Requesting a render does not render anything here — the fonts and the bullet
 * library are on Zach's laptop. It queues, and the local builder fulfils it.
 */

type Build = {
  // 'ineligible' is a verdict the tailor reached, not an error it hit — the
  // plan exists and says why. It is kept distinct from 'failed' because the
  // retry policy must not spend two more tailor calls re-deriving it.
  //
  // 'hand_built' is a reservation, not an outcome: Zach is writing this one
  // himself and the pipeline must keep its hands off. It needs no new rule to
  // work — watcher.needs_build() offers only a job with NO row or a 'failed'
  // one, and claim_job_resume_build's ON CONFLICT clause fires only on
  // 'failed' — so the row IS the mechanism. Rendered here so the drawer says
  // "yours to write" rather than showing an empty panel.
  status: 'claimed' | 'planned' | 'render_requested' | 'rendered' | 'failed'
        | 'ineligible' | 'hand_built'
  plan?: {
    ordering_signal?: string
    analogue?: string | null
    not_claimed?: string[]
    eligibility?: { verdict?: string, note?: string }
    projects?: { name: string, bullets: string[] }[]
  } | null
  final_y?: number | null
  last_error?: string | null
  rendered_at?: string | null
  pdf_path?: string | null
}

/* Which file to upload, and nothing more of the path.
 *
 * The filename deliberately carries no role — it travels with the application,
 * and "Ifiok_Zachary_Ame_Data_Engineering.pdf" tells the employer it was built
 * for that exact req. The cost is that nine American Express builds are
 * Ame.pdf, Ame_Phoenix.pdf, Ame_6280.pdf and so on, which is unreadable in a
 * folder. This panel is where that gets resolved: open the job, read the
 * filename, upload it. The folder never has to be self-describing. */
function fileName(p: string | null | undefined): string | null {
  if (!p) return null
  const parts = p.split(/[\\/]/)
  return parts[parts.length - 1] || null
}

const LABEL: Record<Build['status'], string> = {
  claimed: 'Planning…',
  planned: 'Plan ready',
  render_requested: 'Render queued',
  rendered: 'PDF ready',
  failed: 'Build failed',
  ineligible: 'Not a fit',
  hand_built: 'Hand-built — yours to write',
}

export function TailoredResume({ jobId }: { jobId: string }) {
  const [build, setBuild] = useState<Build | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    setBuild(null); setError(null)
    fetch(`/api/jobs/${encodeURIComponent(jobId)}/resume`)
      .then(r => r.json())
      .then(d => { if (live) setBuild(d.build ?? null) })
      .catch(() => { /* absent is the normal state; the panel just stays hidden */ })
    return () => { live = false }
  }, [jobId])

  const request = useCallback(async () => {
    setBusy(true); setError(null)
    try {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/resume`, { method: 'POST' })
      const d = await res.json()
      if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`)
      setBuild(b => (b ? { ...b, status: 'render_requested' } : b))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed')
    } finally {
      setBusy(false)
    }
  }, [jobId])

  // No build row means this job was never starred with a company reason, or the
  // builder has not reached it. Nothing useful to say, so say nothing.
  if (!build) return null

  const plan = build.plan
  const bullets = plan?.projects?.reduce((n, p) => n + (p.bullets?.length ?? 0), 0) ?? 0

  return (
    <div style={{ marginTop: 'var(--s4)', paddingTop: 'var(--s3)',
                  borderTop: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--s2)',
                    marginBottom: 'var(--s2)' }}>
        <span style={{ fontSize: 'var(--text-meta)', letterSpacing: '0.06em',
                       textTransform: 'uppercase', color: 'var(--fg-subtle)' }}>
          Tailored resume
        </span>
        <span style={{ fontSize: 'var(--text-meta)', color: 'var(--fg-muted)' }}>
          {LABEL[build.status]}
          {build.final_y != null && build.status === 'rendered' ? ` · final y ${build.final_y}` : ''}
        </span>
      </div>

      {build.status === 'rendered' && fileName(build.pdf_path) && (
        <p style={{ fontSize: 'var(--text-data)', margin: '0 0 var(--s2)',
                    lineHeight: 1.45, color: 'var(--fg)' }}>
          <strong style={{ color: 'var(--fg-subtle)', fontWeight: 600 }}>Upload: </strong>
          <code style={{ fontSize: 'var(--text-meta)', background: 'var(--bg-active)',
                         padding: '1px 5px', borderRadius: 'var(--radius)',
                         wordBreak: 'break-all' }}>
            {fileName(build.pdf_path)}
          </code>
          <span style={{ color: 'var(--fg-subtle)' }}> · in resumes/tailored/</span>
        </p>
      )}

      {plan?.ordering_signal && (
        <p style={{ fontSize: 'var(--text-data)', color: 'var(--fg)', margin: '0 0 var(--s2)',
                    lineHeight: 1.45 }}>
          {plan.ordering_signal}
        </p>
      )}

      {plan && (
        <p style={{ fontSize: 'var(--text-data)', margin: '0 0 var(--s2)', lineHeight: 1.45,
                    color: plan.analogue ? 'var(--fg-muted)' : 'var(--fg-subtle)' }}>
          <strong style={{ color: 'var(--fg-subtle)', fontWeight: 600 }}>Analogue: </strong>
          {plan.analogue || 'none — a clean general framing, which is the honest answer when there is no real structural match'}
        </p>
      )}

      {!!plan?.not_claimed?.length && (
        <p style={{ fontSize: 'var(--text-meta)', color: 'var(--fg-subtle)',
                    margin: '0 0 var(--s2)', lineHeight: 1.45 }}>
          <strong style={{ fontWeight: 600 }}>Not claimed: </strong>
          {plan.not_claimed.join(' · ')}
        </p>
      )}

      {/* last_error also carries the reservation note for a hand_built row,
          which is not an error and must not be painted like one. */}
      {build.last_error && (
        <p style={{ fontSize: 'var(--text-meta)',
                    color: build.status === 'hand_built' ? 'var(--fg-subtle)' : 'var(--danger)',
                    margin: '0 0 var(--s2)' }}>
          {build.last_error}
        </p>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s2)' }}>
        <button
          onClick={request}
          disabled={busy || build.status === 'claimed' || build.status === 'failed'
                    || build.status === 'ineligible' || build.status === 'hand_built'
                    || build.status === 'render_requested'}
          style={{
            height: 28, padding: '0 var(--s3)', fontSize: 'var(--text-data)',
            color: 'var(--fg)', background: 'var(--bg-active)',
            border: '1px solid var(--border-strong)', borderRadius: 'var(--radius)',
            opacity: (busy || build.status !== 'planned') && build.status !== 'rendered' ? 0.5 : 1,
          }}
        >
          {build.status === 'render_requested' ? 'Queued' : busy ? 'Requesting…' : 'Render PDF'}
        </button>
        {bullets > 0 && (
          <span style={{ fontSize: 'var(--text-meta)', color: 'var(--fg-subtle)' }}>
            {plan?.projects?.length} projects · {bullets} bullets
          </span>
        )}
      </div>

      {build.status === 'render_requested' && (
        <p style={{ fontSize: 'var(--text-meta)', color: 'var(--fg-subtle)',
                    margin: 'var(--s2) 0 0' }}>
          The builder renders it on its next pass, on your laptop. The PDF lands in
          internship-2026/resumes/.
        </p>
      )}

      {error && (
        <p style={{ fontSize: 'var(--text-meta)', color: 'var(--danger)',
                    margin: 'var(--s2) 0 0' }}>{error}</p>
      )}
    </div>
  )
}
