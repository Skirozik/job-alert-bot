import { NextResponse } from 'next/server'
import { COOKIE_NAME } from '@/lib/auth'

// Reachable without a session on purpose — logging out should always work,
// even from an already-invalid cookie. Permitted by the /api/auth prefix
// allowance in middleware.ts.
export async function POST() {
  const response = NextResponse.json({ ok: true })
  response.cookies.set(COOKIE_NAME, '', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: 0,
    path: '/',
  })
  return response
}
