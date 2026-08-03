import { NextResponse } from 'next/server'
import { requirePersonaApi } from '@/lib/auth'

export async function GET() {
  const persona = await requirePersonaApi()
  if (!persona) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const url = persona.supabaseUrl
  const key = persona.serviceKey

  // Scoped to the caller's OWN persona only. Never enumerate the others — a
  // logged-in user must not be able to learn who else has an account here.
  if (!url || !key) {
    return NextResponse.json({
      persona: { id: persona.id, label: persona.label, hasUrl: !!url, hasKey: !!key },
      error: 'Missing credentials for this persona',
    })
  }

  const res = await fetch(
    `${url}/rest/v1/jobs?select=id,title,company,status&status=neq.new&order=status`,
    {
      headers: {
        apikey: key,
        Authorization: `Bearer ${key}`,
      },
    }
  )

  const data = await res.json()
  return NextResponse.json({
    persona: { id: persona.id, label: persona.label },
    non_new_jobs: data,
    count: Array.isArray(data) ? data.length : 'error',
  })
}
