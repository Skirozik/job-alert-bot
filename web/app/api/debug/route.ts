import { NextResponse } from 'next/server'

export async function GET() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const key = process.env.SUPABASE_SERVICE_KEY

  if (!url || !key) {
    return NextResponse.json({ error: 'Missing env vars', url: !!url, key: !!key })
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
    non_new_jobs: data,
    count: Array.isArray(data) ? data.length : 'error',
  })
}
