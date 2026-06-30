import { NextResponse } from 'next/server'
import { supabaseServer } from '@/lib/supabase-server'

const VALID_STATUSES = ['new', 'saved', 'applied', 'dismissed']

export async function PATCH(
  request: Request,
  { params }: { params: { id: string } }
) {
  const { status } = await request.json()

  if (!VALID_STATUSES.includes(status)) {
    return NextResponse.json({ error: 'Invalid status' }, { status: 400 })
  }

  const { error } = await supabaseServer
    .from('jobs')
    .update({ status })
    .eq('id', params.id)

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }

  return NextResponse.json({ ok: true })
}
