// Signed, persona-bound session tokens for the dashboard password gate.
//
// The dashboard is multi-tenant: several people share one deployment, each
// logging in with their own password and seeing only their own Supabase
// project. The cookie therefore has to say WHICH person, and that claim has
// to be unforgeable — otherwise editing one field of your own cookie would
// hand you someone else's job search.
//
// Token format (exactly 5 dot-separated fields):
//
//     v1 . personaId . issuedAtMillis . pwdTag . hmac
//
//   hmac   = HMAC-SHA256(SESSION_SECRET, "v1.personaId.issuedAt.pwdTag")
//   pwdTag = first 16 hex of HMAC(SESSION_SECRET, "pwtag:personaId:password")
//
// Three properties this buys:
//
//   1. personaId is INSIDE the signed payload, so changing `beyonce` to
//      `owner` invalidates the signature. Forging one requires SESSION_SECRET,
//      which is server-only and is not any user's password.
//   2. Every field is charset-restricted and the split must yield exactly 5
//      parts, so no field can contain a `.` and shift the boundaries. Without
//      that, HMAC("a.b.1.t") is reachable from more than one field split —
//      the classic canonicalisation forgery.
//   3. pwdTag binds the session to the password that created it, so changing
//      someone's password actually revokes their live sessions. That matters
//      with 30-day cookies on other people's devices.
//
// Uses Web Crypto (`crypto.subtle`) so the same code runs in both the Node.js
// API route (login) and the Edge middleware (verify).

const TOKEN_VERSION = 'v1'

// No `.` (the delimiter) and no uppercase — persona ids are derived by
// lowercasing an env var infix, and env var names cannot contain hyphens.
const PERSONA_RE = /^[a-z0-9_]{1,32}$/

export function isValidPersonaId(id: string): boolean {
  return PERSONA_RE.test(id)
}

export async function hmacHex(key: string, message: string): Promise<string> {
  const enc = new TextEncoder()
  const cryptoKey = await crypto.subtle.importKey(
    'raw',
    enc.encode(key),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  )
  const sig = await crypto.subtle.sign('HMAC', cryptoKey, enc.encode(message))
  return Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

export function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false
  let result = 0
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i)
  }
  return result === 0
}

/** Binds a session to the password that created it, so rotating the password
 *  invalidates outstanding cookies. Truncated to 16 hex chars — it only needs
 *  to detect a change, not resist preimage attacks. */
export async function passwordTag(sessionSecret: string, personaId: string, password: string): Promise<string> {
  return (await hmacHex(sessionSecret, `pwtag:${personaId}:${password}`)).slice(0, 16)
}

export async function createSessionToken(
  sessionSecret: string,
  personaId: string,
  pwdTag: string
): Promise<string> {
  if (!isValidPersonaId(personaId)) throw new Error(`invalid persona id: ${personaId}`)
  const payload = `${TOKEN_VERSION}.${personaId}.${Date.now()}.${pwdTag}`
  return `${payload}.${await hmacHex(sessionSecret, payload)}`
}

export type VerifiedSession = {
  personaId: string
  pwdTag: string
  issuedAt: number
}

/** Returns the session's claims, or null. Deliberately NOT a boolean: there is
 *  no way to learn the persona id from a cookie without passing the signature
 *  check first. Never parse the cookie at a call site. */
export async function verifySessionToken(
  token: string | undefined,
  sessionSecret: string,
  maxAgeMs: number
): Promise<VerifiedSession | null> {
  if (!token || !sessionSecret) return null

  const parts = token.split('.')
  if (parts.length !== 5) return null

  const [version, personaId, ts, pwdTag, sig] = parts
  if (version !== TOKEN_VERSION) return null
  if (!isValidPersonaId(personaId)) return null
  if (!/^\d{1,15}$/.test(ts)) return null
  if (!/^[0-9a-f]{0,32}$/.test(pwdTag)) return null
  if (!/^[0-9a-f]{64}$/.test(sig)) return null

  const age = Date.now() - Number(ts)
  if (!Number.isFinite(age) || age < 0 || age > maxAgeMs) return null

  const expected = await hmacHex(sessionSecret, `${version}.${personaId}.${ts}.${pwdTag}`)
  return timingSafeEqual(sig, expected) ? { personaId, pwdTag, issuedAt: Number(ts) } : null
}

export async function verifyPassword(submitted: string, secret: string): Promise<boolean> {
  return timingSafeEqual(submitted, secret)
}

// ---------------------------------------------------------------------------
// Legacy single-tenant support.
//
// The pre-multi-tenant cookie was `${timestamp}.${HMAC(DASHBOARD_PASSWORD, ts)}`
// — 2 fields, no persona. This accepts those and maps them to the synthesized
// `owner` persona (see lib/personas.ts), so merging this branch cannot log the
// existing user out or break the currently-deployed project.
//
// Active ONLY while SESSION_SECRET is unset. Delete this, and the fallback in
// personas.ts, once every deployment has SESSION_SECRET configured.
// ---------------------------------------------------------------------------
export async function verifyLegacySessionToken(
  token: string | undefined,
  dashboardPassword: string,
  maxAgeMs: number
): Promise<boolean> {
  if (!token || !dashboardPassword) return false
  const parts = token.split('.')
  if (parts.length !== 2) return false
  const [ts, sig] = parts
  if (!/^\d{1,15}$/.test(ts)) return false

  const age = Date.now() - Number(ts)
  if (!Number.isFinite(age) || age < 0 || age > maxAgeMs) return false

  return timingSafeEqual(sig, await hmacHex(dashboardPassword, ts))
}
