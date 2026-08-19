/**
 * Guards the payload strip in app/page.tsx.
 *
 * Descriptions are up to 12,000 chars each and nothing on the client reads
 * them — the only consumer is groupNearDuplicates, which runs server-side and
 * has already finished by the time this is called. Measured on real data: a
 * 300-row slice is 1,739,434 B with descriptions and 276,390 B without.
 *
 * The two failure modes worth a test:
 *   - forgetting nested duplicates[], which is where the payload hides
 *   - mutating the input, which would blank the descriptions the server still
 *     holds a reference to
 *
 * The function is extracted from app/page.tsx by source, not copied, so this
 * cannot pass against a stale duplicate of the logic.
 *
 * Run:  node lib/__tests__/stripForClient.test.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, '../../app/page.tsx'), 'utf8')

let pass = 0, fail = 0
const check = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}

const start = src.indexOf('function stripForClient')
check('stripForClient exists in app/page.tsx', start !== -1)

// Strip TS annotations so plain node can evaluate it. Covers the signature
// (": Grouped[]") AND parameter annotations ("(d: Job)") — an earlier version
// handled only the first and broke the moment the second was added.
const body = src.slice(start, src.indexOf('\n}', start) + 2)
  .replace(/:\s*[A-Z][A-Za-z0-9_]*(\[\])?/g, '')
const stripForClient = new Function(`${body}; return stripForClient`)()

const input = [
  {
    id: 'a', company: 'Acme', title: 'SWE Intern', tier: 'APPLY',
    description: 'x'.repeat(12000), salary: '$40/hr', reason: 'good fit',
    duplicates: [
      { id: 'a2', company: 'Acme', title: 'SWE Intern (Summer)', description: 'y'.repeat(9000) },
    ],
  },
  { id: 'b', company: 'Beta', title: 'Intern', tier: 'APPLY_CAVEAT', description: 'z'.repeat(5000) },
  { id: 'c', company: 'Gamma', title: 'Intern', tier: 'APPLY', description: null },
]
const before = JSON.stringify(input)
const out = stripForClient(input)

check('every top-level description is null', out.every((j) => j.description === null))
check('nested duplicates[] are stripped too',
  out[0].duplicates.every((d) => d.description === null),
  'this is where most of the payload actually hides')
check('a row with no duplicates survives', out[1].duplicates === undefined)
check('an already-null description stays null', out[2].description === null)

check('other fields are untouched',
  out[0].id === 'a' && out[0].company === 'Acme' && out[0].title === 'SWE Intern'
  && out[0].tier === 'APPLY' && out[0].salary === '$40/hr' && out[0].reason === 'good fit')
check('nested duplicate keeps its other fields',
  out[0].duplicates[0].id === 'a2' && out[0].duplicates[0].title === 'SWE Intern (Summer)')
check('row count is unchanged', out.length === input.length)

check('the INPUT is not mutated', JSON.stringify(input) === before,
  'the server still holds this array; blanking it would break grouping on re-render')
check('...including nested duplicates', input[0].duplicates[0].description.length === 9000)
check('the returned objects are new', out[0] !== input[0])

const bytes = (v) => JSON.stringify(v).length
check(`payload actually shrinks (${bytes(input).toLocaleString()} -> ${bytes(out).toLocaleString()} B)`,
  bytes(out) < bytes(input) / 10)

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
