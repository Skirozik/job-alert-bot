/**
 * Salary sorting. TypeScript is transpiled in memory so the test runs the
 * shipped helpers without adding a test framework.
 *
 * Every salary string below is a real value from the live jobs table, taken
 * from the 2,033 rows in To apply that carry a figure. The column mixes hourly,
 * annual, biweekly and unlabelled figures freely, which is the whole reason a
 * plain string sort was never going to work.
 *
 * Run: node lib/__tests__/salarySort.test.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import ts from 'typescript'

const here = dirname(fileURLToPath(import.meta.url))
const loadTs = async (path) => {
  const src = readFileSync(join(here, path), 'utf8')
  const compiled = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
  }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
}

const { annualSalary, sortJobs } = await loadTs('../jobView.ts')

let pass = 0, fail = 0
const check = (name, condition, detail = '') => {
  if (condition) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}

// ---- parsing: every unit normalises to a year ---------------------------
check('a flat hourly rate annualises', annualSalary('$25/hr') === 25 * 2080,
      String(annualSalary('$25/hr')))
check('decimals survive', annualSalary('$32.00/hr') === 32 * 2080)
check('"per hour" is recognised', annualSalary('$37 - $97 an hour') === 37 * 2080)
check('spaced "/ hr" is recognised', annualSalary('$46.15 - $50.71 / hr') === 46.15 * 2080)

check('an annual range reads as annual', annualSalary('$37,000 - $82,000 USD') === 37000)
check('an en dash range parses', annualSalary('$39,108–$111,111') === 39108,
      String(annualSalary('$39,108–$111,111')))
check('an explicit /yr parses', annualSalary('$52,900–$108,000/yr (annualized)') === 52900)

// The case a magnitude-only fallback gets badly wrong: 18 live rows.
check('biweekly annualises at 26 pay periods',
      annualSalary('$1,635.00 - $3,185.00 biweekly') === 1635 * 26,
      String(annualSalary('$1,635.00 - $3,185.00 biweekly')))
check('biweekly is NOT read as weekly',
      annualSalary('$1,635.00 biweekly') !== 1635 * 52)
check('weekly annualises at 52', annualSalary('$2,000 per week') === 2000 * 52)
check('monthly annualises at 12', annualSalary('$5,000 monthly') === 5000 * 12)

// ---- the lower bound ranks, never the ceiling ---------------------------
check('a wide band ranks on its floor', annualSalary('$20 - $70/hr') === 20 * 2080)
check('a wide band does not outrank a higher flat rate',
      annualSalary('$20 - $70/hr') < annualSalary('$60/hr'))

// ---- no figure at all ---------------------------------------------------
for (const v of [null, undefined, '', '   ', 'Not mentioned', 'Competitive', 'DOE']) {
  check(`${JSON.stringify(v)} has no figure`, annualSalary(v) === null)
}
check('a bare number with no dollar sign is not a salary', annualSalary('2027') === null)

// ---- magnitude fallback when no unit is stated --------------------------
check('an unlabelled four-figure number is annual', annualSalary('$85,000') === 85000)
check('an unlabelled two-figure number is hourly', annualSalary('$24') === 24 * 2080)

// ---- an explicit unit always beats the magnitude fallback ---------------
// "$500-$2,000 annually" is a stipend. Read by magnitude it becomes 500 * 2080
// = $1,040,000 and leads the whole board, which is what it did before PER_YEAR.
check('"annually" is honoured, not guessed', annualSalary('$500-$2,000 annually') === null,
      String(annualSalary('$500-$2,000 annually')))
check('"/yr" is honoured', annualSalary('$95,000/yr') === 95000)
check('"per year" is honoured', annualSalary('$88,000 per year') === 88000)
check('"/week" is matched, not just "/wk"',
      annualSalary('$1,000–$1,450/week') === 1000 * 52,
      String(annualSalary('$1,000–$1,450/week')))

// ---- garbage is rejected rather than ranked -----------------------------
// Every string here is live data that led "highest first" before the band.
check('annual figures mislabelled /hr are rejected',
      annualSalary('$91,198–$91,202/hr (intern)') === null,
      String(annualSalary('$91,198–$91,202/hr (intern)')))
check('a $0 placeholder range is rejected',
      annualSalary('$0.00 - $10,000,000.00') === null,
      String(annualSalary('$0.00 - $10,000,000.00')))
check('a cent-scale placeholder is rejected',
      annualSalary('$0.01–$0.02/yr (salary range)') === null)
check('a flat total stipend is rejected', annualSalary('$1,000') === null)
check('zero is kept so a $0 floor is seen',
      annualSalary('$0 - $200,000') === null, String(annualSalary('$0 - $200,000')))

// The band must not reject real pay at either edge.
check('a $20/hr internship is kept', annualSalary('$20/hr') === 20 * 2080)
check('a $175k quant internship is kept', annualSalary('$175,000–$220,000/yr') === 175000)
check('a biweekly stipend inside the band is kept',
      annualSalary('$1,635.00 - $3,185.00 biweekly') === 1635 * 26)

// ---- the k suffix, which is how the best-paying rows are written --------
// Without it "$200k" reads as 200, magnitude-falls to hourly, and annualises to
// $416,000. Every row in the live top ten was inflated about 2x by this.
check('"$200k–$260k" is 200,000 not 416,000',
      annualSalary('$200k–$260k') === 200_000, String(annualSalary('$200k–$260k')))
check('"$100k" is 100,000', annualSalary('$100k') === 100_000)
check('a capital K works', annualSalary('$133K–$215K') === 133_000)
check('"$110k - $120k" is 110,000', annualSalary('$110k - $120k') === 110_000)
check('a k suffix with a space works', annualSalary('$95 k') === 95_000)
check('a plain number is unaffected by the k branch', annualSalary('$85,000') === 85_000)

// ---- a trailing adder is not the salary ---------------------------------
// "$21.80–$29.10/hr plus $5.09/hr" ranked at $10,587 when the global minimum
// picked up the differential instead of the band.
check('the range is the first two figures, not every figure',
      annualSalary('$21.80–$29.10/hr plus $5.09/hr differential') === 21.80 * 2080,
      String(annualSalary('$21.80–$29.10/hr plus $5.09/hr differential')))
check('a single figure with an adder still reads the figure',
      annualSalary('$30/hr plus $2/hr shift premium') === 30 * 2080)

// ---- sorting ------------------------------------------------------------
const job = (id, salary, found_at = '2026-09-01T00:00:00Z') => ({
  id, salary, found_at, company: 'C', title: 'T', location: 'L',
})

const rows = [
  job('none', null),
  job('hourly25', '$25/hr'),                          //  52,000
  job('annual120', '$120,000/yr'),                    // 120,000
  job('notmentioned', 'Not mentioned'),
  job('biweekly', '$1,635.00 - $3,185.00 biweekly'),  //  42,510
  job('hourly75', '$75/hr'),                          // 156,000
]

const desc = sortJobs(rows, 'salary', 'desc').map(j => j.id)
check('highest first puts the top payer first', desc[0] === 'hourly75', desc.join(','))
check('highest first orders by annualised value',
      desc.slice(0, 4).join(',') === 'hourly75,annual120,hourly25,biweekly', desc.join(','))
check('rows with no salary sort LAST when descending',
      desc.slice(-2).sort().join(',') === 'none,notmentioned', desc.join(','))

const asc = sortJobs(rows, 'salary', 'asc').map(j => j.id)
check('lowest first reverses the paying rows',
      asc.slice(0, 4).join(',') === 'biweekly,hourly25,annual120,hourly75', asc.join(','))
check('rows with no salary sort LAST when ascending too',
      asc.slice(-2).sort().join(',') === 'none,notmentioned', asc.join(','))

// Blank rows keep a stable, useful order rather than an arbitrary one.
const blanks = [
  job('older', null, '2026-09-01T00:00:00Z'),
  job('newer', null, '2026-09-09T00:00:00Z'),
]
check('blank salaries fall back to newest discovered first',
      sortJobs(blanks, 'salary', 'desc').map(j => j.id).join(',') === 'newer,older')

// The other sort keys must be untouched by this change.
const byFound = sortJobs([
  job('a', null, '2026-09-01T00:00:00Z'),
  job('b', null, '2026-09-09T00:00:00Z'),
], 'found_at', 'desc').map(j => j.id)
check('found_at sorting still works', byFound.join(',') === 'b,a', byFound.join(','))

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
