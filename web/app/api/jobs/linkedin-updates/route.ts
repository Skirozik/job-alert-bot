import { NextResponse } from 'next/server'
import { requirePersonaApi } from '@/lib/auth'
import type { Job } from '@/types/job'

export const dynamic = 'force-dynamic'

const COLS_BASE = 'id,title,company,location,url,tier,reason,status,found_at,apply_url,is_easy_apply,salary,logo_url'
const COLS_FULL = `${COLS_BASE},suggested_resume`
const MAX_LOOKBACK_MS = 10 * 60_000

async function fetchUpdates(
  url: string,
  key: string,
  after: string,
  columns: string,
): Promise<{ response: Response, jobs: Job[] | null }> {
  const query = new URLSearchParams({
    select: columns,
    status: 'eq.new',
    tier: 'in.(APPLY,APPLY_CAVEAT)',
    found_at: `gt.${after}`,
    // LinkedIn ids are numeric. Every internal source id has a colon prefix.
    id: 'not.like.*:*',
    order: 'found_at.asc',
    limit: '100',
  })
  const response = await fetch(`${url}/rest/v1/jobs?${query}`, {
    headers: { apikey: key, Authorization: `Bearer ${key}` },
    cache: 'no-store',
  })
  return { response, jobs: response.ok ? await response.json() : null }
}

export async function GET(request: Request) {
  const persona = await requirePersonaApi()
  if (!persona) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const raw = new URL(request.url).searchParams.get('after')
  const parsed = raw ? new Date(raw).getTime() : NaN
  if (!Number.isFinite(parsed)) {
    return NextResponse.json({ error: 'Invalid after timestamp' }, { status: 400 })
  }

  // A backgrounded tab can be days old. The normal five-minute full refresh
  // reconciles that case; cap this endpoint to the tiny delta it is designed
  // for so waking a phone never downloads a second full dashboard.
  const after = new Date(Math.max(parsed, Date.now() - MAX_LOOKBACK_MS)).toISOString()
  // Capture before the query. A row committed while the query is running has a
  // later found_at and is guaranteed to be eligible on the next poll.
  const checkedAt = new Date().toISOString()

  let result = await fetchUpdates(persona.supabaseUrl, persona.serviceKey, after, COLS_FULL)
  if (!result.response.ok && result.response.status === 400) {
    // Beyonce/Hassan schemas omit suggested_resume; preserve the same graceful
    // fallback used by the full dashboard query.
    result = await fetchUpdates(persona.supabaseUrl, persona.serviceKey, after, COLS_BASE)
  }
  if (!result.response.ok || !result.jobs) {
    const detail = await result.response.text().catch(() => '')
    console.error(`[linkedin-updates] persona=${persona.id} HTTP ${result.response.status}: ${detail.slice(0, 300)}`)
    return NextResponse.json({ error: 'Could not fetch LinkedIn updates' }, { status: 502 })
  }

  return NextResponse.json({ checkedAt, jobs: result.jobs })
}
