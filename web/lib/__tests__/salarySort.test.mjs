/**
 * Salary sorting. TypeScript is transpiled in memory so the test runs the
 * shipped helpers without adding a test framework.
 *
 * Every salary string below is a real value from the live jobs table, taken
 * from the rows in To apply that carry a figure. A band ranks at its MEDIAN
 * (Zach's call, 2026-09-11), so "$20 - $70/hr" ranks at $45/hr. The column mixes hourly,
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

// Medians introduce halves, and 25.45 * 2080 is not bit-identical to
// ((21.80 + 29.10) / 2) * 2080 in IEEE754. Compare money to the cent.
const near = (a, b) => a !== null && b !== null && Math.abs(a - b) < 0.01

let pass = 0, fail = 0
const check = (name, condition, detail = '') => {
  if (condition) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}

// ---- parsing: every unit normalises to a year ---------------------------
check('a flat hourly rate annualises', annualSalary('$25/hr') === 25 * 2080,
      String(annualSalary('$25/hr')))
check('decimals survive', annualSalary('$32.00/hr') === 32 * 2080)
check('"per hour" is recognised', near(annualSalary('$37 - $97 an hour'), 67 * 2080),
      String(annualSalary('$37 - $97 an hour')))
check('spaced "/ hr" is recognised',
      near(annualSalary('$46.15 - $50.71 / hr'), ((46.15 + 50.71) / 2) * 2080),
      String(annualSalary('$46.15 - $50.71 / hr')))

check('an annual range medians', annualSalary('$37,000 - $82,000 USD') === 59_500,
      String(annualSalary('$37,000 - $82,000 USD')))
check('an en dash range parses and medians',
      annualSalary('$39,108–$111,111') === (39108 + 111111) / 2,
      String(annualSalary('$39,108–$111,111')))
check('an explicit /yr medians',
      annualSalary('$52,900–$108,000/yr (annualized)') === (52900 + 108000) / 2)

// The case a magnitude-only fallback gets badly wrong: 18 live rows.
check('biweekly annualises at 26 pay periods, on the median',
      annualSalary('$1,635.00 - $3,185.00 biweekly') === ((1635 + 3185) / 2) * 26,
      String(annualSalary('$1,635.00 - $3,185.00 biweekly')))
check('biweekly is NOT read as weekly',
      annualSalary('$1,635.00 biweekly') === 1635 * 26)
check('weekly annualises at 52', annualSalary('$2,000 per week') === 2000 * 52)
check('monthly annualises at 12', annualSalary('$5,000 monthly') === 5000 * 12)

// ---- a band ranks at its MEDIAN ----------------------------------------
// Changed from the floor on 2026-09-11. On a floor every wide band sank to the
// bottom whatever its midpoint, and wide bands are how the best payers post.
check('a wide band ranks at its midpoint', annualSalary('$20 - $70/hr') === 45 * 2080,
      String(annualSalary('$20 - $70/hr')))
check('a single figure is its own median', annualSalary('$45/hr') === 45 * 2080)
check('a band still loses to a flat rate above its midpoint',
      annualSalary('$20 - $70/hr') < annualSalary('$60/hr'))
check('a band now beats a flat rate below its midpoint',
      annualSalary('$20 - $70/hr') > annualSalary('$40/hr'))
check('an out-of-order band medians the same',
      annualSalary('$70 - $20/hr') === annualSalary('$20 - $70/hr'))
// An odd count takes the middle value, not the midpoint of the extremes.
check('three figures take the middle one',
      annualSalary('$50,000, $60,000, $90,000') === 60_000,
      String(annualSalary('$50,000, $60,000, $90,000')))

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
      annualSalary('$1,000–$1,450/week') === 1225 * 52,
      String(annualSalary('$1,000–$1,450/week')))

// ---- garbage is rejected rather than ranked -----------------------------
// Every string here is live data that led "highest first" before the band.
check('annual figures mislabelled /hr are rejected',
      annualSalary('$91,198–$91,202/hr (intern)') === null,
      String(annualSalary('$91,198–$91,202/hr (intern)')))
// A $0 floor is rejected outright, because the median would hide it: this
// medians to a perfectly plausible $5,000,000.
check('a $0 placeholder range is rejected',
      annualSalary('$0.00 - $10,000,000.00') === null,
      String(annualSalary('$0.00 - $10,000,000.00')))
check('a cent-scale placeholder is rejected',
      annualSalary('$0.01–$0.02/yr (salary range)') === null)
check('a flat total stipend is rejected', annualSalary('$1,000') === null)
check('a $0 floor is rejected even when the median is plausible',
      annualSalary('$0 - $200,000') === null, String(annualSalary('$0 - $200,000')))

// The band must not reject real pay at either edge.
check('a $20/hr internship is kept', annualSalary('$20/hr') === 20 * 2080)
check('a $175k quant internship is kept',
      annualSalary('$175,000–$220,000/yr') === (175000 + 220000) / 2)
check('a biweekly stipend inside the band is kept',
      annualSalary('$1,635.00 - $3,185.00 biweekly') === ((1635 + 3185) / 2) * 26)

// The magnitude fallback reads the band's FLOOR, not its median, so a
// "$900 - $1,200" band is read as hourly rather than flipping to annual on a
// midpoint of 1,050. Hourly annualises to $2.18M, which the plausibility band
// then rejects -- the honest outcome for a string that states no unit and
// could as easily be weekly.
check('an unlabelled band straddling 1000 does not flip to annual',
      annualSalary('$900 - $1,200') === null,
      String(annualSalary('$900 - $1,200')))
check('an unlabelled band clearly above 1000 is annual',
      annualSalary('$90,000 - $120,000') === 105_000)

// ---- the k suffix, which is how the best-paying rows are written --------
// Without it "$200k" reads as 200, magnitude-falls to hourly, and annualises to
// $416,000. Every row in the live top ten was inflated about 2x by this.
check('"$200k–$260k" medians to 230,000, not 416,000',
      annualSalary('$200k–$260k') === 230_000, String(annualSalary('$200k–$260k')))
check('"$100k" is 100,000', annualSalary('$100k') === 100_000)
check('a capital K works', annualSalary('$133K–$215K') === 174_000)
check('"$110k - $120k" medians to 115,000', annualSalary('$110k - $120k') === 115_000)
check('a k suffix with a space works', annualSalary('$95 k') === 95_000)
check('a plain number is unaffected by the k branch', annualSalary('$85,000') === 85_000)

// ---- a trailing adder is not the salary ---------------------------------
// "$21.80–$29.10/hr plus $5.09/hr" ranked at $10,587 when the global minimum
// picked up the differential instead of the band.
check('the band excludes the adder, and medians what is left',
      near(annualSalary('$21.80–$29.10/hr plus $5.09/hr differential'), 25.45 * 2080),
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
// biweekly medians to (1635+3185)/2 * 26 = 62,660, which now sits ABOVE the
// $25/hr row at 52,000. On the old floor ranking it was 42,510 and below it.
check('highest first orders by annualised median',
      desc.slice(0, 4).join(',') === 'hourly75,annual120,biweekly,hourly25', desc.join(','))
check('rows with no salary sort LAST when descending',
      desc.slice(-2).sort().join(',') === 'none,notmentioned', desc.join(','))

const asc = sortJobs(rows, 'salary', 'asc').map(j => j.id)
check('lowest first reverses the paying rows',
      asc.slice(0, 4).join(',') === 'hourly25,biweekly,annual120,hourly75', asc.join(','))
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

// ---- the defects the three-copy rewrite closed -------------------------
// Every string here ranked WRONGLY in this column before 2026-09-11. The gold
// star's copy of this function had the same bugs, so the badge and the sort
// were wrong together and neither could reveal the other.

// "$20-71/hr" found ONE amount, so the median was the floor and NVIDIA sorted
// at $41,600 instead of $94,640. 123 distinct live strings are written this way.
check('a bare second bound is the top of the band', annualSalary('$20-71/hr') === 45.5 * 2080,
      String(annualSalary('$20-71/hr')))
check('a k written once governs both bounds', annualSalary('$200-260k') === 230_000)
check('an unlabelled bare-bound range still medians',
      annualSalary('$120,000-150,000') === 135_000, String(annualSalary('$120,000-150,000')))

// A total stipend multiplied by a workload clause put a $3,840 payment near the
// TOP of "highest first", which is the single most visible way this sort lies.
check('a lump sum beside a workload clause does not rank as weekly pay',
      annualSalary('$3,840 stipend (14-16 weeks, 20 hrs/week)') === null,
      String(annualSalary('$3,840 stipend (14-16 weeks, 20 hrs/week)')))
check('a workload clause does not sink an hourly rate either',
      Math.abs(annualSalary('$15.09/hr, up to 20 hrs/week') - 15.09 * 2080) < 0.01,
      String(annualSalary('$15.09/hr, up to 20 hrs/week')))

// A rate restated annually is one wage written twice; medianed together it
// exploded past the plausibility cap and the row sorted last instead of high.
check('an annualised restatement is not a second band',
      near(annualSalary('$22.50–$29.00/hr ($46,800–$60,320 annualized equivalent)'), 25.75 * 2080),
      String(annualSalary('$22.50–$29.00/hr ($46,800–$60,320 annualized equivalent)')))
check('a restatement written the other way round reads the same',
      annualSalary('$93,600/yr ($45/hr)') === 93_600)
check('a weekly rate restated hourly stays weekly',
      annualSalary('$730/week (~$18.25/hr)') === 730 * 52,
      String(annualSalary('$730/week (~$18.25/hr)')))
check("an adder's unit does not override the band's",
      annualSalary('$25.00/hr + $2,000/month housing stipend') === 52_000,
      String(annualSalary('$25.00/hr + $2,000/month housing stipend')))

// ---- a foreign currency is not ranked against dollars -------------------
check('a CAD band has no comparable figure', annualSalary('$68,250–$78,000 CAD') === null)
check('CA$ is caught too', annualSalary('CA$40/hr - CA$45/hr') === null)
check('US$ is NOT Singapore dollars', annualSalary('US$120,000') === 120_000,
      String(annualSalary('US$120,000')))
check("a trailing 's' is not a currency symbol",
      annualSalary('$70,000 - $90,000 plus benefits') === 80_000,
      String(annualSalary('$70,000 - $90,000 plus benefits')))

// ---- malformed bands are rejected, not ranked --------------------------
check('an hourly floor under an annual ceiling is malformed',
      annualSalary('$17.98-$135,700') === null)
check('...even when a unit is stated', annualSalary('$37.22 - $150,000') === null)
check('a retirement plan is not the top of the band',
      annualSalary('$60,000 - 401k match') === 60_000)
check('a bonus percentage is not a figure', annualSalary('$55,000 - 15% bonus') === 55_000)

// ---- and it is the ORDER that the user sees ----------------------------
// "returns null" and "sorts last" are different claims; only the second is
// visible. A CAD row and a stipend must fall in with the blanks, not lead.
const mixed = [
  job('cad', '$68,250–$78,000 CAD'),
  job('stipend', '$3,840 stipend (14-16 weeks, 20 hrs/week)'),
  job('nvidia', '$20-71/hr'),                 //  94,640
  job('flat50', '$50/hr'),                    // 104,000
  job('blank', null),
]
const mixedDesc = sortJobs(mixed, 'salary', 'desc').map(j => j.id)
check('highest first ranks the bare-bound range on its median, not its floor',
      mixedDesc.slice(0, 2).join(',') === 'flat50,nvidia', mixedDesc.join(','))
check('an unparseable currency and a lump sum sort LAST, not first',
      mixedDesc.slice(2).sort().join(',') === 'blank,cad,stipend', mixedDesc.join(','))

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
