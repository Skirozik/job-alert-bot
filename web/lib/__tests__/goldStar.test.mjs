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
const { starReasons, isStarred, annualSalary } = mod

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
    is_easy_apply: c.is_easy_apply, suggested_resume: c.suggested_resume, tier: c.tier,
  }
  const got = starReasons(job)
  check(c.name, JSON.stringify(got) === JSON.stringify(c.expected),
    `expected ${JSON.stringify(c.expected)}, got ${JSON.stringify(got)}`)
}

console.log('\n-- the Easy Apply gate is a gate, not a signal --')

const strong = {
  company: 'Apple', title: 'iOS Engineer Intern', tier: 'APPLY',
  salary: '$80.00 per hour', suggested_resume: 'Mobile',
}
check('three signals star when applying externally',
  starReasons({ ...strong, is_easy_apply: false }).length === 3)
check('...and none of them survive Easy Apply',
  starReasons({ ...strong, is_easy_apply: true }).length === 0,
  'a resume curated for an Easy Apply is effort that never reaches a human')

console.log('\n-- salary parsing --')

const sal = (salary) => starReasons({ company: 'Nobody', title: 'Intern', tier: 'APPLY', salary, is_easy_apply: false })
check('hourly above the bar', sal('$60/hr').includes('salary'))
check('hourly below the bar', sal('$18/hr').length === 0)
check('annual above the bar', sal('$150,000 per year').includes('salary'))
check('annual below the bar', sal('$40,000 per year').length === 0)
// Changed 2026-09-11: the band is judged on its MEDIAN. The floor sank every
// wide band regardless of its midpoint, and a wide band is how the best payers
// post — IBM's "$61,200–$138,600" failed the bar on its rising-sophomore end.
check('range uses the median', sal('$20 - $90 per hour').includes('salary'),
  '$55/hr median clears the $35/hr bar')
check('a band under the bar at BOTH ends still does not star',
  sal('$20 - $28 per hour').length === 0)
check('monthly pay annualises', sal('$10,000/mo').includes('salary'))
check('weekly pay annualises', sal('$2,400/week').includes('salary'))
check('biweekly is not read as weekly', sal('$1,635 - $3,185 biweekly').length === 0)
check('a $0 floor never stars', sal('$0 - $200,000').length === 0)
check('an implausible figure is rejected', sal('$91,198 - $91,202/hr').length === 0)
check('null salary', sal(null).length === 0)
check('prose with no figure', sal('competitive compensation').length === 0)
check('a bare four-figure number reads as annual', sal('$95,000').includes('salary'))

console.log('\n-- company normalisation mirrors scraper/db.py norm_company --')

const comp = (company) => starReasons({ company, title: 'Intern', tier: 'APPLY', salary: null, is_easy_apply: false })
check('exact name', comp('Microsoft').includes('company'))
check('legal suffix stripped', comp('Stripe, Inc.').includes('company'))
check("leading 'The' stripped", comp('The Meta').includes('company'))
check('case insensitive', comp('nVIDIA').includes('company'))
check('unlisted company', comp('Obscure Widgets').length === 0)
check('a substring is not a match', comp("Applebee's").length === 0,
  "Apple is listed; Applebee's must not inherit its star")
check('empty company does not throw or match', comp('').length === 0)
check('null company does not throw', comp(null).length === 0)

console.log('\n-- the APPLY-only gate --')
check('an APPLY_CAVEAT job never stars',
  starReasons({ ...strong, is_easy_apply: false, tier: 'APPLY_CAVEAT' }).length === 0,
  'a caveat job already carries a known reservation')

console.log('\n-- isStarred agrees with starReasons --')
check('isStarred true when reasons exist', isStarred({ ...strong, is_easy_apply: false }) === true)
check('isStarred false when gated', isStarred({ ...strong, is_easy_apply: true }) === false)

console.log('\n-- salary parsing: the NUMBER, where a verdict cannot see the bug --')
// Deliberately the same strings and the same expected numbers as the block in
// scraper/test_gold_star.py, in the same order, so the two files can be read
// side by side. star_rules.json pins the verdict; this pins the arithmetic.

// Money to the cent: medians introduce halves, and 25.45 * 2080 is not
// bit-identical to ((21.80 + 29.10) / 2) * 2080 in IEEE754.
const near = (got, want) => got !== null && Math.abs(got - want) < 0.01

// The second bound of a range usually carries no "$" of its own. Requiring one
// found a single amount, whose median is itself -- so the band ranked at its
// FLOOR and the median rule was not in force for the commonest spelling at all.
check('a bare second bound is the top of the band', near(annualSalary('$20-71/hr'), 45.5 * 2080),
      String(annualSalary('$20-71/hr')))
check('a k written once governs both bounds', annualSalary('$200-260k') === 230_000,
      String(annualSalary('$200-260k')))
check('...even against a decimal floor', annualSalary('$39.7-72.8k/yr') === 56_250,
      String(annualSalary('$39.7-72.8k/yr')))

// A workload clause is not a pay unit -- PER_WEEK matched the "/week" inside
// "20 hrs/week" -- but neither is it a rate when it is the ONLY unit present.
check('a workload clause does not supply the unit',
      near(annualSalary('$15.09/hr, up to 20 hrs/week'), 15.09 * 2080),
      String(annualSalary('$15.09/hr, up to 20 hrs/week')))
check('a lump sum beside a workload clause is not a weekly rate',
      annualSalary('$3,840 stipend (14-16 weeks, 20 hrs/week)') === null,
      String(annualSalary('$3,840 stipend (14-16 weeks, 20 hrs/week)')))

// Amounts were cut at the adder and the unit was not, so a housing stipend
// written after "+" set the multiplier for the band in front of it.
check("an adder's unit does not override the band's",
      annualSalary('$25.00/hr + $2,000/month housing stipend') === 52_000,
      String(annualSalary('$25.00/hr + $2,000/month housing stipend')))
check('an adder still supplies the unit when the band has none',
      near(annualSalary('$21.80-$29.10 plus $5.09/hr differential'), 25.45 * 2080),
      String(annualSalary('$21.80-$29.10 plus $5.09/hr differential')))

// A rate and its annualised restatement are one wage written twice.
check('an annualised restatement is not a second band',
      near(annualSalary('$22.50-$29.00/hr ($46,800-$60,320 annualized equivalent)'), 25.75 * 2080),
      String(annualSalary('$22.50-$29.00/hr ($46,800-$60,320 annualized equivalent)')))
check('a restatement written the other way round reads the same',
      annualSalary('$93,600/yr ($45/hr)') === 93_600, String(annualSalary('$93,600/yr ($45/hr)')))
check('a weekly rate restated hourly stays weekly',
      annualSalary('$730/week (~$18.25/hr)') === 730 * 52,
      String(annualSalary('$730/week (~$18.25/hr)')))

// Units are chosen by POSITION -- the headline rate comes first.
check('two units for one job resolve to the one stated first',
      annualSalary('$1,000-$2,000 USD weekly / $21.00-$36.00/hr') === 1500 * 52,
      String(annualSalary('$1,000-$2,000 USD weekly / $21.00-$36.00/hr')))

// An hourly floor glued to an annual ceiling is two units, not one range.
check('a band spanning hourly to annual scale is malformed',
      annualSalary('$17.98-$135,700') === null, String(annualSalary('$17.98-$135,700')))
check('...even when a unit is stated', annualSalary('$37.22 - $150,000.00/yr') === null,
      String(annualSalary('$37.22 - $150,000.00/yr')))
check('a genuinely wide band is NOT malformed',
      annualSalary('$1,500-$2,500/month') === 2000 * 12,
      String(annualSalary('$1,500-$2,500/month')))

// The spaced slash never matched, so 199 live rows never reached the hourly
// branch -- rescued by accident by the magnitude fallback, with the artifact
// guard bypassed for exactly the string its comment cites.
check('a spaced slash is still an hourly rate', near(annualSalary('$45.00 / hour'), 45 * 2080),
      String(annualSalary('$45.00 / hour')))
check('an annual figure mislabelled hourly is rejected, either spelling',
      annualSalary('$91,198 - $91,202 / hr') === null && annualSalary('$91,198-$91,202/hr') === null,
      `spaced ${annualSalary('$91,198 - $91,202 / hr')}, unspaced ${annualSalary('$91,198-$91,202/hr')}`)

// Non-USD returns no figure; the symbol forms need a guard on the preceding
// character or "S$" matches inside "US$120,000" and every American posting
// written that way silently vanishes from the board.
check('a CAD band is not scored against a USD bar', annualSalary('$68,250-$78,000 CAD') === null)
check('CA$ is caught too', annualSalary('CA$40/hr - CA$45/hr') === null)
check('US$ is NOT Singapore dollars', annualSalary('US$120,000') === 120_000,
      String(annualSalary('US$120,000')))
check("a trailing 's' is not a currency symbol",
      annualSalary('$70,000 - $90,000 plus benefits') === 80_000,
      String(annualSalary('$70,000 - $90,000 plus benefits')))
check('USD in words is not foreign', annualSalary('$120,000 - $150,000 USD') === 135_000)

// Noise the range-aware pattern would otherwise eat as a second bound.
check('a retirement plan is not the top of the band',
      annualSalary('$60,000 - 401k match') === 60_000,
      String(annualSalary('$60,000 - 401k match')))
check('a bonus percentage is not a figure',
      annualSalary('$55,000 - 15% bonus') === 55_000,
      String(annualSalary('$55,000 - 15% bonus')))

// The floor matters as much as the cap: a cap alone cannot see a wrong answer
// that lands inside the plausible range.
check('an implausible hourly rate is rejected', annualSalary('$1,000/hr') === null)
check('a lump-sum stipend is not a salary', annualSalary('$1,000') === null)

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
