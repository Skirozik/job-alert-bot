import { NextResponse } from 'next/server'
import { createSessionToken, passwordTag } from '@/lib/session'
import { findPersonaByCredentials, sessionSecret } from '@/lib/personas'
import { COOKIE_NAME } from '@/lib/auth'

// Username + password. There is no "who are you?" dropdown and no user list
// anywhere on an unauthenticated page — a picker would publish the roster of
// everyone using this dashboard, which is the same disclosure .gitignore
// already works to prevent, and would hand out an enumeration oracle.
//
// For the same reason every failure below returns an IDENTICAL bare 401: a
// wrong username, a wrong password, and a well-formed request for an account
// that doesn't exist are indistinguishable to the caller. findPersonaByCredentials
// also does one constant-time comparison either way, so timing doesn't leak
// which usernames are real.
export async function POST(request: Request) {
  let username: unknown
  let password: unknown
  try {
    ;({ username, password } = await request.json())
  } catch {
    return NextResponse.json({ error: 'Bad request' }, { status: 400 })
  }

  if (typeof username !== 'string' || typeof password !== 'string' || !username || !password) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const secret = sessionSecret()
  if (!secret) {
    console.error('[auth] no session signing key available — set SESSION_SECRET')
    return NextResponse.json({ error: 'Server misconfigured' }, { status: 500 })
  }

  const persona = findPersonaByCredentials(username, password)
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
