import { NextResponse } from 'next/server'
import { createSessionToken, verifyPassword } from '@/lib/session'

export async function POST(request: Request) {
  const { password } = await request.json()
  const secret = process.env.DASHBOARD_PASSWORD

  if (!secret || !password || !(await verifyPassword(password, secret))) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const token = await createSessionToken(secret)
  const response = NextResponse.json({ ok: true })
  response.cookies.set('dashboard_auth', token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: 60 * 60 * 24 * 30,
    path: '/',
  })
  return response
}
