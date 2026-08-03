import { NextResponse } from 'next/server'
import { createSessionToken, passwordTag } from '@/lib/session'
import { findPersonaByPassword, sessionSecret } from '@/lib/personas'
import { COOKIE_NAME } from '@/lib/auth'

// The password alone identifies who you are — there's no "who are you?"
// dropdown. A picker would publish the roster of everyone using this dashboard
// on an unauthenticated page, which is the same disclosure .gitignore already
// works to prevent, and it hands out an enumeration oracle for free.
//
// The cost is that passwords double as identity, so they must be unique and
// high-entropy. personas.ts refuses to authenticate on a collision rather than
// picking one, and the 401 below is bare — no hint about which persona, or
// whether any, was close.
export async function POST(request: Request) {
  let password: unknown
  try {
    ;({ password } = await request.json())
  } catch {
    return NextResponse.json({ error: 'Bad request' }, { status: 400 })
  }

  if (typeof password !== 'string' || !password) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const secret = sessionSecret()
  if (!secret) {
    console.error('[auth] no session signing key available — set SESSION_SECRET')
    return NextResponse.json({ error: 'Server misconfigured' }, { status: 500 })
  }

  const persona = findPersonaByPassword(password)
  if (!persona) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const tag = await passwordTag(secret, persona.id, password)
  const token = await createSessionToken(secret, persona.id, tag)

  const response = NextResponse.json({ ok: true })
  response.cookies.set(COOKIE_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: 60 * 60 * 24 * 30,
    path: '/',
  })
  return response
}
