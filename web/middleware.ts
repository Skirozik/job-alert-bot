import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'
import { verifySessionToken } from '@/lib/session'

const THIRTY_DAYS_MS = 1000 * 60 * 60 * 24 * 30

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl

  // /api/auth must stay open (it's how the login POST itself succeeds).
  if (pathname.startsWith('/login') || pathname.startsWith('/api/auth')) {
    return NextResponse.next()
  }

  const secret = process.env.DASHBOARD_PASSWORD
  const token = request.cookies.get('dashboard_auth')?.value
  const isAuthed = !!secret && (await verifySessionToken(token, secret, THIRTY_DAYS_MS))

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
