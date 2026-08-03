import { NextResponse } from 'next/server'
import { requirePersonaApi } from '@/lib/auth'

const VALID_STATUSES = ['new', 'saved', 'applied', 'dismissed']

// Keep the Next 14 `{ params: { id: string } }` signature — Next 15 changes it
// to a Promise, and package.json pins next to exactly 14.2.35.
export async function PATCH(
  request: Request,
  { params }: { params: { id: string } }
) {
  // Cross-tenant writes aren't merely denied here, they're unrepresentable:
  // the PATCH targets `?id=eq.<id>` against THIS persona's project, so another
  // persona's job id simply matches no row and falls through to the 404 below.
  const persona = await requirePersonaApi()
  if (!persona) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const { status } = await request.json()

  if (!VALID_STATUSES.includes(status)) {
    return NextResponse.json({ error: 'Invalid status' }, { status: 400 })
  }

  const url = persona.supabaseUrl
  const key = persona.serviceKey

  if (!url || !key) {
    console.error(`[status] persona "${persona.id}" missing credentials`)
    return NextResponse.json({ error: 'Server misconfigured — missing env vars' }, { status: 500 })
  }

  const jobId = params.id

  // Write: PATCH directly via Supabase REST API (bypasses supabase-js client entirely)
  const writeRes = await fetch(
    `${url}/rest/v1/jobs?id=eq.${encodeURIComponent(jobId)}`,
    {
      method: 'PATCH',
      headers: {
        apikey: key,
        Authorization: `Bearer ${key}`,
        'Content-Type': 'application/json',
        Prefer: 'return=representation',
      },
      body: JSON.stringify({ status }),
    }
  )

  if (!writeRes.ok) {
    const body = await writeRes.text()
    console.error('[status] supabase write failed:', writeRes.status, body, 'job:', jobId)
    return NextResponse.json({ error: `Supabase error ${writeRes.status}: ${body}` }, { status: 500 })
  }

  const updated = await writeRes.json()

  if (!updated || updated.length === 0) {
    console.error('[status] no rows matched for job:', jobId)
    return NextResponse.json({ error: 'Job not found in DB' }, { status: 404 })
  }

  // Verify: read it back immediately to confirm it actually persisted
  const readRes = await fetch(
    `${url}/rest/v1/jobs?id=eq.${encodeURIComponent(jobId)}&select=id,status`,
    {
      headers: {
        apikey: key,
        Authorization: `Bearer ${key}`,
      },
    }
  )
  const readBack = await readRes.json()
  const actualStatus = readBack?.[0]?.status

  console.log(`[status] job=${jobId} wanted=${status} db_has=${actualStatus}`)

  if (actualStatus !== status) {
    return NextResponse.json(
      { error: `Write appeared to succeed but DB has status="${actualStatus}" not "${status}"` },
      { status: 500 }
    )
  }

  return NextResponse.json({ ok: true })
}
