/**
 * Gold-star rules, and their parity with scraper/gold_star.py.
 *
 * Every `cases` entry in lib/star_rules.json is asserted here AND in
 * scraper/test_gold_star.py. That is the contract that matters: the phone and
 * the dashboard must agree about what is starred, or the badge is worse than
 * not having one.
 *
 * Run: node lib/__tests__/goldStar.test.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import ts from 'typescript'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, '../goldStar.ts'), 'utf8')
const rulesJson = readFileSync(join(here, '../star_rules.json'), 'utf8')

// The module imports './star_rules.json' and '@/types/job'. Inline the JSON and
// drop the type-only import so it runs standalone, exactly as dupes.test.mjs
// transpiles the shipped source rather than a copy of it.
const compiled = ts.transpileModule(
  src
    .replace(/import type \{ Job \} from '@\/types\/job'\n/, '')
    .replace(/import rules from '\.\/star_rules\.json'/, `const rules = ${rulesJson}`),
  { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } },
).outputText

const mod = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
const { starReasons, isStarred } = mod

let pass = 0, fail = 0
const check = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}

const rules = JSON.parse(rulesJson)

console.log('-- shared fixture: TypeScript must match the Python rules exactly --')

check('the fixture has cases', rules.cases.length > 0)

for (const c of rules.cases) {
  const job = {
    company: c.company, title: c.title, salary: c.salary,
    is_easy_apply: c.is_easy_apply, suggested_resume: c.suggested_resume,
  }
  const got = starReasons(job)
  check(c.name, JSON.stringify(got) === JSON.stringify(c.expected),
    `expected ${JSON.stringify(c.expected)}, got ${JSON.stringify(got)}`)
}

console.log('\n-- the Easy Apply gate is a gate, not a signal --')

const strong = {
  company: 'Apple', title: 'iOS Engineer Intern',
  salary: '$80.00 per hour', suggested_resume: 'Mobile',
}
check('three signals star when applying externally',
  starReasons({ ...strong, is_easy_apply: false }).length === 3)
check('...and none of them survive Easy Apply',
  starReasons({ ...strong, is_easy_apply: true }).length === 0,
  'a resume curated for an Easy Apply is effort that never reaches a human')

console.log('\n-- salary parsing --')

const sal = (salary) => starReasons({ company: 'Nobody', title: 'Intern', salary, is_easy_apply: false })
check('hourly above the bar', sal('$60/hr').includes('salary'))
check('hourly below the bar', sal('$18/hr').length === 0)
check('annual above the bar', sal('$150,000 per year').includes('salary'))
check('annual below the bar', sal('$40,000 per year').length === 0)
check('range uses the lower bound', sal('$20 - $90 per hour').length === 0,
  'starring on the ceiling is how a badge becomes noise')
check('null salary', sal(null).length === 0)
check('prose with no figure', sal('competitive compensation').length === 0)
check('a bare four-figure number reads as annual', sal('$95,000').includes('salary'))

console.log('\n-- company normalisation mirrors scraper/db.py norm_company --')

const comp = (company) => starReasons({ company, title: 'Intern', salary: null, is_easy_apply: false })
check('exact name', comp('Microsoft').includes('company'))
check('legal suffix stripped', comp('Stripe, Inc.').includes('company'))
check("leading 'The' stripped", comp('The Meta').includes('company'))
check('case insensitive', comp('nVIDIA').includes('company'))
check('unlisted company', comp('Obscure Widgets').length === 0)
check('a substring is not a match', comp("Applebee's").length === 0,
  "Apple is listed; Applebee's must not inherit its star")
check('empty company does not throw or match', comp('').length === 0)
check('null company does not throw', comp(null).length === 0)

console.log('\n-- isStarred agrees with starReasons --')
check('isStarred true when reasons exist', isStarred({ ...strong, is_easy_apply: false }) === true)
check('isStarred false when gated', isStarred({ ...strong, is_easy_apply: true }) === false)

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
