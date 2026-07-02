// Signed session tokens for the dashboard password gate.
//
// Rather than storing the raw password in the auth cookie (readable by
// anyone who gets the cookie), we store `${timestamp}.${hmac}` where the
// HMAC is keyed on the password itself. That means possessing the cookie
// doesn't reveal the password, and it can't be reused past maxAgeMs even if
// copied elsewhere. Uses Web Crypto (`crypto.subtle`) so the same code runs
// in both the Node.js API route (login) and the Edge middleware (verify).

async function hmacHex(key: string, message: string): Promise<string> {
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

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false
  let result = 0
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i)
  }
  return result === 0
}

export async function createSessionToken(secret: string): Promise<string> {
  const ts = Date.now().toString()
  const sig = await hmacHex(secret, ts)
  return `${ts}.${sig}`
}

export async function verifySessionToken(
  token: string | undefined,
  secret: string,
  maxAgeMs: number
): Promise<boolean> {
  if (!token) return false
  const [ts, sig] = token.split('.')
  if (!ts || !sig) return false

  const age = Date.now() - Number(ts)
  if (!Number.isFinite(age) || age < 0 || age > maxAgeMs) return false

  const expected = await hmacHex(secret, ts)
  return timingSafeEqual(sig, expected)
}

export async function verifyPassword(submitted: string, secret: string): Promise<boolean> {
  return timingSafeEqual(submitted, secret)
}
