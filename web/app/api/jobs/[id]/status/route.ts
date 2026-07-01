import { NextResponse } from 'next/server'
import { getSupabaseServer } from '@/lib/supabase-server'

const VALID_STATUSES = ['new', 'saved', 'applied', 'dismissed']

export async function PATCH(
  request: Request,
  { params }: { params: { id: string } }
) {
  const { status } = await request.json()

  if (!VALID_STATUSES.includes(status)) {
    return NextResponse.json({ error: 'Invalid status' }, { status: 400 })
  }

  const { data, error } = await getSupabaseServer()
    .from('jobs')
    .update({ status })
    .eq('id', params.id)
    .select('id')

  if (error) {
    console.error('[status] supabase error:', error.message, 'job:', params.id)
    return NextResponse.json({ error: error.message }, { status: 500 })
  }

  if (!data || data.length === 0) {
    console.error('[status] no rows updated for job:', params.id)
    return NextResponse.json({ error: 'Job not found' }, { status: 404 })
  }

  return NextResponse.json({ ok: true })
}
