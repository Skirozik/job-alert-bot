import { NextResponse } from 'next/server'
import { requirePersonaApi } from '@/lib/auth'

const VALID_STATUSES = ['new', 'saved', 'applied', 'dismissed']
const JOB_ID = /^[A-Za-z0-9:_-]{1,128}$/
const MAX_GROUP = 50
type UpdatedRow = { id: string, status: string }

function idFilter(ids: string[]): string {
  if (ids.length === 1) return `eq.${ids[0]}`
  // IDs are validated above, so quoted PostgREST literals cannot be escaped
  // into a different filter expression.
  return `in.(${ids.map((id) => `"${id}"`).join(',')})`
}

function parseJson(text: string): any {
  try { return JSON.parse(text) } catch { return null }
}

async function legacyVerifiedUpdate(
  url: string,
  key: string,
  ids: string[],
  status: string,
): Promise<{ rows?: UpdatedRow[], error?: string }> {
  const filter = idFilter(ids)
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
      cache: 'no-store',
    }
  )
  const writeText = await writeRes.text()
  if (!writeRes.ok) return { error: `Supabase write failed (HTTP ${writeRes.status}): ${writeText.slice(0, 300)}` }

  const rows = parseJson(writeText)
  if (!Array.isArray(rows) || rows.length !== ids.length) {
    return { error: `Expected ${ids.length} updated rows but received ${Array.isArray(rows) ? rows.length : 0}` }
  }

  const readRes = await fetch(
    `${url}/rest/v1/jobs?id=${encodeURIComponent(filter)}&select=id,status`,
    {
      headers: { apikey: key, Authorization: `Bearer ${key}` },
      cache: 'no-store',
    }
  )
  const readText = await readRes.text()
  if (!readRes.ok) return { error: `Status verification failed (HTTP ${readRes.status}): ${readText.slice(0, 300)}` }

  const readBack = parseJson(readText)
  const actual = new Map(
    (Array.isArray(readBack) ? readBack : []).map((row: UpdatedRow) => [row.id, row.status])
  )
  const failed = ids.filter((id) => actual.get(id) !== status)
  return failed.length
    ? { error: `${failed.length} row(s) did not verify after the update` }
    : { rows: readBack as UpdatedRow[] }
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

  const requestId = crypto.randomUUID()
  let mode: 'atomic-rpc' | 'verified-fallback' = 'atomic-rpc'
  let updatedRows: UpdatedRow[] | undefined

  // A Postgres function is one transaction: it validates the complete group
  // before updating anything. During rollout, deployments whose migration has
  // not reached Supabase yet retain the old verified PATCH behavior instead
  // of turning every dashboard button into a hard failure.
  const rpcRes = await fetch(`${url}/rest/v1/rpc/set_job_group_status`, {
    method: 'POST',
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ p_ids: ids, p_status: status }),
    cache: 'no-store',
  })
  const rpcText = await rpcRes.text()
  const rpcBody = parseJson(rpcText)

  if (rpcRes.ok && Array.isArray(rpcBody)) {
    updatedRows = rpcBody
  } else if (rpcRes.status === 404 && rpcBody?.code === 'PGRST202') {
    mode = 'verified-fallback'
    console.warn(`[status] request=${requestId} atomic RPC missing; using verified fallback`)
    const fallback = await legacyVerifiedUpdate(url, key, ids, status)
    if (fallback.error) {
      console.error(`[status] request=${requestId} mode=${mode} jobs=${ids.join(',')} error=${fallback.error}`)
      return NextResponse.json(
        { error: `Status update failed (reference ${requestId})` },
        { status: 502, headers: { 'Cache-Control': 'no-store' } }
      )
    }
    updatedRows = fallback.rows
  } else {
    console.error(
      `[status] request=${requestId} mode=${mode} jobs=${ids.join(',')} ` +
      `HTTP=${rpcRes.status} body=${rpcText.slice(0, 300)}`
    )
    const responseStatus = rpcBody?.code === 'P0002' ? 409 : 502
    return NextResponse.json(
      { error: `Status update failed (reference ${requestId})` },
      { status: responseStatus, headers: { 'Cache-Control': 'no-store' } }
    )
  }

  const actual = new Map((updatedRows ?? []).map((row) => [row.id, row.status]))
  const failed = ids.filter((id) => actual.get(id) !== status)
  if (failed.length) {
    console.error(
      `[status] request=${requestId} mode=${mode} wanted=${status} ` +
      `verified=${ids.length - failed.length}/${ids.length}`
    )
    return NextResponse.json(
      { error: `Status update could not be verified (reference ${requestId})` },
      { status: 502, headers: { 'Cache-Control': 'no-store' } }
    )
  }

  console.log(
    `[status] request=${requestId} mode=${mode} jobs=${ids.join(',')} ` +
    `wanted=${status} verified=${ids.length}`
  )
  return NextResponse.json(
    { ok: true, updatedIds: ids, updatedRows, requestId },
    { headers: { 'Cache-Control': 'no-store' } }
  )
}
