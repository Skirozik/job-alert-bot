// Persona registry: maps a login password to one person's Supabase project.
//
// ===========================================================================
// NODE RUNTIME ONLY. Do not import this from middleware.ts, from a Client
// Component, or from any route carrying `export const runtime = 'edge'`.
//
// It reads process.env with COMPUTED keys (`process.env[name]`). Next.js only
// inlines statically-analyzable `process.env.FOO` for the Edge Runtime, so a
// computed lookup silently returns undefined there — every persona would
// vanish and everyone would get 401s with no obvious cause. This is the single
// most likely way a future change breaks the whole dashboard.
// ===========================================================================
//
// Configuration is by convention, discovered at runtime:
//
//   PERSONA_<ID>_PASSWORD       (required — its presence is what defines a persona)
//   PERSONA_<ID>_SUPABASE_URL   (required)
//   PERSONA_<ID>_SERVICE_KEY    (required)
//   PERSONA_<ID>_LABEL          (optional, display only)
//
// Adding a fourth person is three environment variables and a redeploy. No
// code change, no list to keep in sync.
//
// Deliberately NOT a single JSON blob: one stray comma in a 2 KB secret field
// would take down authentication for everybody at once, in a value the Vercel
// UI won't render back to you.

import { timingSafeEqual } from './session'

if (typeof window !== 'undefined') {
  throw new Error('lib/personas.ts was pulled into a client bundle — it holds service keys')
}

export type Persona = {
  id: string
  label: string
  supabaseUrl: string
  serviceKey: string
}

// The password is deliberately NOT on Persona. It's needed only at login and
// when computing the password tag, so it never travels with the object that
// gets passed into page and route code.
type PersonaRecord = Persona & { password: string }

let cache: PersonaRecord[] | null = null

function build(): PersonaRecord[] {
  const found: PersonaRecord[] = []
  const seenPasswords = new Map<string, string>()

  // An explicit allowlist, if set, lets you disable someone without deleting
  // their credentials. Left unset normally.
  const allow = (process.env.PERSONA_IDS ?? '')
    .split(',')
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean)

  for (const key of Object.keys(process.env)) {
    const m = /^PERSONA_([A-Z0-9_]+)_PASSWORD$/.exec(key)
    if (!m) continue

    const upper = m[1]
    const id = upper.toLowerCase()
    if (allow.length && !allow.includes(id)) continue

    const password = process.env[`PERSONA_${upper}_PASSWORD`] ?? ''
    const supabaseUrl = (process.env[`PERSONA_${upper}_SUPABASE_URL`] ?? '').replace(/\/+$/, '')
    const serviceKey = process.env[`PERSONA_${upper}_SERVICE_KEY`] ?? ''
    const label = process.env[`PERSONA_${upper}_LABEL`] ?? id

    const missing = [
      !password && `PERSONA_${upper}_PASSWORD`,
      !supabaseUrl && `PERSONA_${upper}_SUPABASE_URL`,
      !serviceKey && `PERSONA_${upper}_SERVICE_KEY`,
    ].filter(Boolean)

    if (missing.length) {
      // Drop this persona only — everyone else keeps working.
      console.error(`[personas] "${id}" misconfigured — missing ${missing.join(', ')} — skipping`)
      continue
    }

    const clash = seenPasswords.get(password)
    if (clash) {
      console.error(
        `[personas] "${id}" and "${clash}" share a password. Passwords double as identity here, ` +
          `so neither will be able to log in until this is fixed. Generate with: openssl rand -base64 24`
      )
    }
    seenPasswords.set(password, id)

    found.push({ id, label, supabaseUrl, serviceKey, password })
  }

  // ---- Legacy single-tenant fallback -------------------------------------
  // Lets this branch deploy to the EXISTING project with its existing env vars
  // and behave identically. Active only when no personas are configured.
  // Remove together with verifyLegacySessionToken() once every deployment has
  // SESSION_SECRET + PERSONA_* set.
  if (found.length === 0) {
    const password = process.env.DASHBOARD_PASSWORD ?? ''
    const supabaseUrl = (process.env.NEXT_PUBLIC_SUPABASE_URL ?? '').replace(/\/+$/, '')
    const serviceKey = process.env.SUPABASE_SERVICE_KEY ?? ''
    if (password && supabaseUrl && serviceKey) {
      console.warn('[personas] no PERSONA_* config found — running in legacy single-tenant mode')
      found.push({ id: 'owner', label: 'Dashboard', supabaseUrl, serviceKey, password })
    } else {
      console.error(
        '[personas] no personas configured and no legacy DASHBOARD_PASSWORD/SUPABASE_* fallback available — nobody can log in'
      )
    }
  }

  return found
}

function all(): PersonaRecord[] {
  // Env is immutable for a lambda's lifetime, so memoizing is safe.
  if (cache === null) cache = build()
  return cache
}

export function listPersonas(): Persona[] {
  return all().map(({ password: _password, ...p }) => p)
}

export function getPersona(id: string): Persona | null {
  const p = all().find((x) => x.id === id)
  if (!p) return null
  const { password: _password, ...rest } = p
  return rest
}

export function getPersonaPassword(id: string): string | null {
  return all().find((x) => x.id === id)?.password ?? null
}

/** Resolve a submitted password to its persona. Compares against every
 *  configured persona with no early exit, and refuses to guess if more than
 *  one matches. */
export function findPersonaByPassword(submitted: string): Persona | null {
  if (!submitted) return null
  const matches = all().filter((p) => timingSafeEqual(submitted, p.password))
  if (matches.length !== 1) {
    if (matches.length > 1) {
      console.error(
        `[personas] password matched ${matches.length} personas (${matches
          .map((m) => m.id)
          .join(', ')}) — refusing to authenticate`
      )
    }
    return null
  }
  const { password: _password, ...rest } = matches[0]
  return rest
}

/** Signing key for session cookies. Falls back to DASHBOARD_PASSWORD only in
 *  legacy single-tenant mode — see the ordering warning in DEPLOY notes:
 *  SESSION_SECRET must be set before a second persona exists, or whoever knows
 *  DASHBOARD_PASSWORD could mint a cookie naming any persona. */
export function sessionSecret(): string {
  const s = process.env.SESSION_SECRET
  if (s) return s

  const personaCount = Object.keys(process.env).filter((k) =>
    /^PERSONA_[A-Z0-9_]+_PASSWORD$/.test(k)
  ).length

  if (personaCount > 1) {
    console.error(
      '[personas] SESSION_SECRET is not set but multiple personas are configured. ' +
        'Refusing all authentication — generate one with: openssl rand -hex 32'
    )
    return ''
  }
  return process.env.DASHBOARD_PASSWORD ?? ''
}
