import { NextResponse } from 'next/server'

const VALID_STATUSES = ['new', 'saved', 'applied', 'dismissed']

export async function PATCH(
  request: Request,
  { params }: { params: { id: string } }
) {
  const { status } = await request.json()

  if (!VALID_STATUSES.includes(status)) {
    return NextResponse.json({ error: 'Invalid status' }, { status: 400 })
  }

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const key = process.env.SUPABASE_SERVICE_KEY

  if (!url || !key) {
    console.error('[status] missing env vars — URL:', !!url, 'KEY:', !!key)
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
