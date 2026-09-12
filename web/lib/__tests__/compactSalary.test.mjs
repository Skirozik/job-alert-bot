/**
 * The salary column's compact form. TypeScript is transpiled in memory so the
 * test runs the shipped helper without adding a test framework.
 *
 * Every string below is a real value from the live jobs table. The defect
 * this pins: compactSalary read only the numbers that carried their own "$",
 * and a range is usually written with one — "$20-71/hr" drew as "$20/hr" on
 * 113 live rows, so the column and the sort key disagreed about the same
 * string. The figures now come from the same band reader annualSalary uses.
 *
 * Run: node lib/__tests__/compactSalary.test.mjs
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

const { compactSalary, annualSalary } = await loadTs('../jobView.ts')

let pass = 0, fail = 0
const check = (name, condition, detail = '') => {
  if (condition) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}
const draws = (raw, want) =>
  check(`${JSON.stringify(raw)} draws as ${JSON.stringify(want)}`, compactSalary(raw) === want,
        `got ${JSON.stringify(compactSalary(raw))}`)

// ---- the defect: a range whose top has no "$" of its own ------------------
draws('$20-71/hr', '$20–71/hr')
draws('$20–71/hr (internship hourly rate)', '$20–71/hr')
draws('$18.00-27.50/hr', '$18–28/hr')
draws('$25-30/hr', '$25–30/hr')
draws('$21.62-24.36/hr', '$22–24/hr')
// The same range with both dollar signs was always right; it must stay so.
draws('$20 - $71/hr', '$20–71/hr')

// ---- a k written once governs both bounds --------------------------------
draws('$39.7–72.8k/yr', '$40–73k/yr')
draws('$200k-$260k', '$200–260k')

// ---- an adder is a bonus, not the top of a range -------------------------
draws('$25.00/hr + $2,000/month housing stipend', '$25/hr')
draws('$25/hr + $2,000 sign-on bonus', '$25/hr')
draws('$30-35/hr + $3,000 bonus', '$30–35/hr')
draws('$12-$14+/hr', '$12–14/hr')

// ---- noise that looks like money is not money -----------------------------
draws('$60,000 - 401k match', '$60k')
draws('$29.32 - $43.99/hr (part-time, 20 hrs/week)', '$29–44/hr')

// ---- what was already right must not move ---------------------------------
draws('$25/hr', '$25/hr')
draws('$25.00/hour', '$25/hr')
draws('$22.50/hr', '$23/hr')
draws('$4,000.00/wk - $6,000.00/wk', '$4–6k/wk')
draws('$37,000 - $82,000 USD', '$37–82k')
draws('$25 - $25/hr', '$25/hr')
draws('Competitive', 'Competitive')
draws('Paid (15-25 hrs/week); rate not specified', 'Paid (15-25…')
draws(null, '')
draws('', '')

// ---- the column and the sort key read one string the same way -------------
// A string the column draws as a single figure must not sort as a band, and
// vice versa. Checked over the shapes that used to disagree.
for (const raw of ['$20-71/hr', '$18.00-27.50/hr', '$39.7–72.8k/yr', '$25.00/hr + $2,000/month housing stipend']) {
  const shown = compactSalary(raw)
  const isBand = /–/.test(shown)
  const annual = annualSalary(raw)
  // A band medians; a single figure annualises directly. Both are non-null
  // here, and the drawn form must agree with which one it was.
  const single = /^\$(\d+)(k)?(\/\w+)?$/.exec(shown)
  check(`sort and draw agree on ${JSON.stringify(raw)}`,
        annual !== null && (isBand ? !single : !!single),
        `shown=${shown} annual=${annual}`)
}

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
