// Session -> persona resolution. THIS is the authorization boundary.
//
// middleware.ts only proves the cookie is well-formed, correctly signed, and
// unexpired — it runs on the Edge Runtime and cannot read the persona registry
// (see the header comment in personas.ts). Every page or route that touches a
// persona's data must call one of the require* helpers below.
//
// Imports next/headers, so this module is server-only by construction and
// cannot be pulled into a client bundle.

import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'
import { verifySessionToken, verifyLegacySessionToken, passwordTag } from './session'
import { getPersona, getPersonaPassword, sessionSecret } from './personas'
import type { Persona } from './personas'

export const THIRTY_DAYS_MS = 1000 * 60 * 60 * 24 * 30
export const COOKIE_NAME = 'dashboard_auth'

export async function getSessionPersona(): Promise<Persona | null> {
  // Next 14: cookies() is synchronous. Do not await it.
  const token = cookies().get(COOKIE_NAME)?.value
  if (!token) return null

  const secret = sessionSecret()
  if (!secret) return null

  const session = await verifySessionToken(token, secret, THIRTY_DAYS_MS)

  if (!session) {
    // Legacy 2-field cookie from before multi-tenancy. Only honoured while
    // SESSION_SECRET is unset, i.e. genuine single-tenant mode.
    if (!process.env.SESSION_SECRET) {
      const legacyOk = await verifyLegacySessionToken(
        token,
        process.env.DASHBOARD_PASSWORD ?? '',
        THIRTY_DAYS_MS
      )
      if (legacyOk) return getPersona('owner')
    }
    return null
  }

  const persona = getPersona(session.personaId)
  if (!persona) {
    // Configured, then removed. Deleting someone's env vars is a real
    // revocation, not just a login block.
    return null
  }

  // Password rotation revokes outstanding sessions.
  const currentPassword = getPersonaPassword(persona.id)
  if (currentPassword) {
    const expected = await passwordTag(secret, persona.id, currentPassword)
    if (session.pwdTag && session.pwdTag !== expected) return null
  }

  return persona
}

/** For Server Components / pages. Redirects to /login when unauthenticated. */
export async function requirePersonaPage(): Promise<Persona> {
  const persona = await getSessionPersona()
  if (!persona) redirect('/login')
  return persona
}

/** For route handlers. Returns null so the caller can emit 401 JSON.
 *
 *  Deliberately does NOT redirect: a redirect from a route handler produces a
 *  307 that fetch() follows into a 200 HTML login page, and the caller then
 *  treats that as a successful write. That exact bug is documented in
 *  middleware.ts. */
export async function requirePersonaApi(): Promise<Persona | null> {
  return getSessionPersona()
}
