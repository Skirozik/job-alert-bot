import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'
import { verifySessionToken, verifyLegacySessionToken } from '@/lib/session'

const THIRTY_DAYS_MS = 1000 * 60 * 60 * 24 * 30

// This runs on the Edge Runtime, which only inlines statically-analyzable
// `process.env.FOO` reads — so it deliberately does NOT import lib/personas.ts
// (computed env lookups return undefined there). It is a coarse gate only:
//
//   middleware  -> "this cookie is well-formed, signed, and unexpired"
//   requirePersona*() in lib/auth.ts (Node) -> "and it belongs to THIS persona,
//                                              whose credentials are these"
//
// Every page and route that reads persona data must call requirePersona*.
// Passing this check is not authorization.
export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl

  // /api/auth must stay open (it's how the login POST itself succeeds).
  // NOTE this prefix opens everything under /api/auth/* — keep only login and
  // logout there.
  if (pathname.startsWith('/login') || pathname.startsWith('/api/auth')) {
    return NextResponse.next()
  }

  const sessionSecret = process.env.SESSION_SECRET
  const legacyPassword = process.env.DASHBOARD_PASSWORD
  const token = request.cookies.get('dashboard_auth')?.value

  let isAuthed = false
  if (sessionSecret) {
    isAuthed = (await verifySessionToken(token, sessionSecret, THIRTY_DAYS_MS)) !== null
  } else if (legacyPassword) {
    // Legacy single-tenant mode: accept both the old 2-field cookie and a new
    // persona-bound one signed with the password as the key.
    isAuthed =
      (await verifyLegacySessionToken(token, legacyPassword, THIRTY_DAYS_MS)) ||
      (await verifySessionToken(token, legacyPassword, THIRTY_DAYS_MS)) !== null
  }

  // Other API routes: return 401 JSON instead of redirecting to /login.
  // (A redirect here was the original bug — fetch() follows it, gets a 200
  // HTML login page back, and the caller treats that as a successful write.)
  if (pathname.startsWith('/api/')) {
    if (!isAuthed) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
    }
    return NextResponse.next()
  }

  if (!isAuthed) {
    return NextResponse.redirect(new URL('/login', request.url))
  }

  return NextResponse.next()
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
}
