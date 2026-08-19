/**
 * Guards the explicit column list in app/page.tsx.
 *
 * Two failure modes, both silent:
 *
 *  1. A column named here that a persona's schema lacks. PostgREST answers a
 *     missing column with a hard 400, NOT a silent omission (verified live),
 *     and fetchJobs returns [] on a failed response — so one wrong name
 *     renders that persona's entire dashboard empty. Only the original
 *     project has suggested_resume; scraper_beyonce/schema.sql and
 *     scraper_hassan/schema.sql omit it.
 *
 *  2. description creeping back into the list. It is 90% of the compressed
 *     bytes leaving Supabase and nothing renders it.
 *
 * Run:  node lib/__tests__/selectCols.test.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, '../../app/page.tsx'), 'utf8')
const root = join(here, '../../..')

let pass = 0, fail = 0
const check = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}

const base = /const COLS_BASE = '([^']+)'/.exec(src)
check('COLS_BASE is defined', base !== null)
const cols = base[1].split(',')

// ── Every base column must exist in EVERY persona's schema ───────────────
const schemas = ['scraper_beyonce/schema.sql', 'scraper_hassan/schema.sql']
for (const rel of schemas) {
  let sql
  try { sql = readFileSync(join(root, rel), 'utf8') } catch { continue }
  const table = /create table jobs\s*\(([\s\S]*?)\);/i.exec(sql)
  if (!table) continue
  const declared = new Set(
    table[1].split('\n').map((l) => (/^\s+([a-z_]+)\s/.exec(l) || [])[1]).filter(Boolean)
  )
  const missing = cols.filter((c) => !declared.has(c))
  check(`every COLS_BASE column exists in ${rel}`, missing.length === 0,
    `missing: ${missing.join(', ')} — this persona would 400 and render empty`)
}

// suggested_resume is the one divergent column, so it must NOT be in the base
check('suggested_resume is NOT in COLS_BASE', !cols.includes('suggested_resume'),
  'only the original schema has it; it belongs in COLS_FULL with a fallback')
check('COLS_FULL adds it on top of the base',
  /const COLS_FULL = `\$\{COLS_BASE\},suggested_resume`/.test(src))
check('fetchJobs retries without it on a 400',
  src.includes("query.includes(',suggested_resume')")
  && src.includes("query.replace(',suggested_resume', '')"),
  'without the retry, Beyonce and Hassan get an empty dashboard')

// ── description must never come back ─────────────────────────────────────
check('description is NOT selected', !cols.includes('description'),
  '90% of the compressed bytes; nothing renders it')
check('...nor in COLS_FULL', !/COLS_FULL[^\n]*description/.test(src))
check('no query still uses select=* unslimmed',
  !/fetchPaged\(url, key, 'select=\*/.test(src),
  'every fetch must go through slimSelect')

// ── The columns the UI reads must all be present ─────────────────────────
for (const needed of ['id', 'title', 'company', 'location', 'tier', 'reason',
                      'status', 'found_at', 'salary', 'logo_url', 'apply_url',
                      'url', 'is_easy_apply']) {
  check(`'${needed}' is selected`, cols.includes(needed), 'the UI reads it')
}

// ── slimSelect must not mangle the rest of the query ─────────────────────
const slim = (q, c) => q.replace(/(^|&)select=\*/, `$1select=${c}`)
check('slimSelect replaces only the select param',
  slim('select=*&status=neq.new&order=found_at.desc', 'a,b')
    === 'select=a,b&status=neq.new&order=found_at.desc')
check('...and leaves a query with no select alone',
  slim('status=eq.new', 'a,b') === 'status=eq.new')

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
