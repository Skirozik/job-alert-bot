/**
 * The Greenhouse backup link, and its parity with scraper/notifier.py.
 *
 * Every case in fixtures/greenhouse_form_links.json is asserted here AND in
 * scraper/test_greenhouse_backup_link.py. The phone alert and the dashboard
 * must agree about which postings get Greenhouse's own form and what the link
 * is, or the two surfaces send you to different places for one posting.
 *
 * Run: node lib/__tests__/greenhouseForm.test.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import ts from 'typescript'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, '../greenhouseForm.ts'), 'utf8')
const boardsJson = readFileSync(join(here, '../greenhouse_boards.json'), 'utf8')

// The module imports './greenhouse_boards.json' and '@/types/job'. Inline the
// JSON and drop the type-only import so the shipped source runs standalone.
const compiled = ts.transpileModule(
  src
    .replace(/import type \{ Job \} from '@\/types\/job'\n/, '')
    .replace(/import boards from '\.\/greenhouse_boards\.json'/, `const boards = ${boardsJson}`),
  { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } },
).outputText

const mod = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
const { greenhouseFormUrl } = mod

let pass = 0, fail = 0
const check = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` -- ${detail}` : ''}`) }
}

console.log('-- shared fixture: TypeScript must match the Python backup link exactly --')

const fixture = JSON.parse(readFileSync(join(here, '../../../fixtures/greenhouse_form_links.json'), 'utf8'))
check('the fixture has cases', fixture.cases.length > 0)
check('the fixture covers both outcomes',
  fixture.cases.some(c => c.expected === null) && fixture.cases.some(c => typeof c.expected === 'string'))

for (const c of fixture.cases) {
  const got = greenhouseFormUrl(c.job)
  check(c.name, got === c.expected, `expected ${JSON.stringify(c.expected)}, got ${JSON.stringify(got)}`)
}

console.log('\n-- the board list is the one the scraper uses --')
const boards = JSON.parse(boardsJson)
check('the board list is not empty', Object.keys(boards).length > 0)
check('Airbnb is a Greenhouse board with token airbnb', boards.Airbnb === 'airbnb')
check('the keys are sorted, as the regen command writes them',
  JSON.stringify(Object.keys(boards)) === JSON.stringify([...Object.keys(boards)].sort()))

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
