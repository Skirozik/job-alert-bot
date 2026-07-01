import { createClient } from '@supabase/supabase-js'

export function getSupabaseServer() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const key = process.env.SUPABASE_SERVICE_KEY
  if (!url || !key) {
    throw new Error(`Missing Supabase env vars — URL: ${!!url}, KEY: ${!!key}`)
  }
  return createClient(url, key)
}
