import { NextResponse } from 'next/server'
import { requirePersonaApi } from '@/lib/auth'

const VALID_STATUSES = ['new', 'saved', 'applied', 'dismissed']
const JOB_ID = /^[A-Za-z0-9:_-]{1,128}$/
const MAX_GROUP = 50

function idFilter(ids: string[]): string {
  if (ids.length === 1) return `eq.${ids[0]}`
  // IDs are validated above, so quoted PostgREST literals cannot be escaped
  // into a different filter expression.
  return `in.(${ids.map((id) => `"${id}"`).join(',')})`
}

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

  const body = await request.json().catch(() => null)
  const status = body?.status

  if (!VALID_STATUSES.includes(status)) {
    return NextResponse.json({ error: 'Invalid status' }, { status: 400 })
  }

  // A collapsed dashboard row can represent several source rows. Updating
  // only the visible leader lets an untouched sibling reappear in To apply on
  // the next refresh. The client therefore sends the complete group, while a
  // legacy/single-row request continues to work unchanged.
  const requested = Array.isArray(body?.ids) ? body.ids : []
  const ids = [...new Set([params.id, ...requested])]
  if (ids.length > MAX_GROUP || ids.some((id) => typeof id !== 'string' || !JOB_ID.test(id))) {
    return NextResponse.json({ error: 'Invalid job ids' }, { status: 400 })
  }

  const url = persona.supabaseUrl
  const key = persona.serviceKey

  if (!url || !key) {
    console.error(`[status] persona "${persona.id}" missing credentials`)
    return NextResponse.json({ error: 'Server misconfigured — missing env vars' }, { status: 500 })
  }

  const filter = idFilter(ids)

  // Write: PATCH directly via Supabase REST API (bypasses supabase-js client entirely)
  const writeRes = await fetch(
    `${url}/rest/v1/jobs?id=${encodeURIComponent(filter)}`,
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
    console.error('[status] supabase write failed:', writeRes.status, body, 'jobs:', ids)
    return NextResponse.json({ error: `Supabase error ${writeRes.status}: ${body}` }, { status: 500 })
  }

  const updated = await writeRes.json()

  if (!Array.isArray(updated) || updated.length !== ids.length) {
    console.error('[status] not every group row matched:', { requested: ids, updated })
    return NextResponse.json({ error: 'One or more jobs were not found in DB' }, { status: 404 })
  }

  // Verify every group member immediately, not just the route id.
  const readRes = await fetch(
    `${url}/rest/v1/jobs?id=${encodeURIComponent(filter)}&select=id,status`,
    {
      headers: {
        apikey: key,
        Authorization: `Bearer ${key}`,
      },
    }
  )
  if (!readRes.ok) {
    const readBody = await readRes.text()
    console.error('[status] verification read failed:', readRes.status, readBody, 'jobs:', ids)
    return NextResponse.json({ error: 'Could not verify status update' }, { status: 500 })
  }

  const readBack = await readRes.json()
  const actual = new Map((readBack ?? []).map((row: { id: string, status: string }) => [row.id, row.status]))
  const failed = ids.filter((id) => actual.get(id) !== status)

  console.log(`[status] jobs=${ids.join(',')} wanted=${status} verified=${ids.length - failed.length}`)

  if (failed.length) {
    return NextResponse.json(
      { error: `Write appeared to succeed but ${failed.length} row(s) did not verify` },
      { status: 500 }
    )
  }

  return NextResponse.json({ ok: true, updatedIds: ids })
}
