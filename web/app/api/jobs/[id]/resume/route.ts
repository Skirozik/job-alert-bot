import { NextResponse } from 'next/server'
import { requirePersonaApi } from '@/lib/auth'

/**
 * The tailored-resume build for one job.
 *
 * GET  returns the plan and its state, so the drawer can show what was chosen
 *      and why before anything is rendered.
 * POST asks for a PDF. It does NOT render one: the fonts, the bullet library
 *      and the output folder all live on Zach's laptop, and this runs on
 *      Vercel. So it flips the row to 'render_requested' and the local watcher
 *      fulfils it on its next pass.
 *
 * That split is deliberate rather than a limitation. Rendering every plan would
 * put ~40 PDFs a week into the folder that autofill uploads from, against a
 * demonstrated appetite of ~8. Rendering on request keeps the count at actual
 * demand, and `rendered_at` becomes the only usage signal this system has.
 */

// Keep the Next 14 `{ params: { id: string } }` signature — Next 15 changes it
// to a Promise, and package.json pins next to exactly 14.2.35.
type Ctx = { params: { id: string } }

const JOB_ID = /^[A-Za-z0-9:_-]{1,128}$/

function creds(persona: { supabaseUrl?: string, serviceKey?: string }) {
  const url = persona.supabaseUrl
  const key = persona.serviceKey
  return url && key ? { url, key, h: { apikey: key, Authorization: `Bearer ${key}` } } : null
}

export async function GET(_request: Request, { params }: Ctx) {
  const persona = await requirePersonaApi()
  if (!persona) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!JOB_ID.test(params.id)) return NextResponse.json({ error: 'Invalid job id' }, { status: 400 })

  const c = creds(persona)
  if (!c) return NextResponse.json({ error: 'Server misconfigured' }, { status: 500 })

  const res = await fetch(
    `${c.url}/rest/v1/resume_builds?job_id=eq.${encodeURIComponent(params.id)}` +
    `&select=job_id,status,plan,final_y,pdf_path,last_error,planned_at,rendered_at`,
    { headers: c.h, cache: 'no-store' }
  )

  // Before the migration is applied this 404s, which is a normal state and not
  // an error worth surfacing to the user — the drawer simply shows nothing.
  if (res.status === 404) return NextResponse.json({ build: null, migrated: false })
  if (!res.ok) {
    return NextResponse.json({ error: `Supabase ${res.status}` }, { status: 500 })
  }
  const rows = await res.json()
  return NextResponse.json({ build: rows?.[0] ?? null, migrated: true })
}

export async function POST(_request: Request, { params }: Ctx) {
  const persona = await requirePersonaApi()
  if (!persona) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!JOB_ID.test(params.id)) return NextResponse.json({ error: 'Invalid job id' }, { status: 400 })

  const c = creds(persona)
  if (!c) return NextResponse.json({ error: 'Server misconfigured' }, { status: 500 })

  // Only a planned or previously-rendered build can be asked for. A 'claimed'
  // row has no plan yet and a 'failed' one needs its reason addressed first,
  // so neither is a sensible thing to queue.
  const res = await fetch(
    `${c.url}/rest/v1/resume_builds?job_id=eq.${encodeURIComponent(params.id)}` +
    `&status=in.(planned,rendered)`,
    {
      method: 'PATCH',
      headers: { ...c.h, 'Content-Type': 'application/json', Prefer: 'return=representation' },
      body: JSON.stringify({ status: 'render_requested', updated_at: new Date().toISOString() }),
      cache: 'no-store',
    }
  )
  if (!res.ok) {
    const body = await res.text()
    console.error('[resume] request failed:', res.status, body, 'job:', params.id)
    return NextResponse.json({ error: `Supabase ${res.status}` }, { status: 500 })
  }
  const rows = await res.json()
  if (!rows?.length) {
    return NextResponse.json(
      { error: 'No plan ready for this job yet. The builder plans starred jobs on its next pass.' },
      { status: 409 }
    )
  }
  return NextResponse.json({ ok: true, status: 'render_requested' })
}
