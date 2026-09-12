/**
 * The salary parser exists in THREE copies. This is what stops them drifting.
 *
 *   scraper/gold_star.py   -> the phone push
 *   web/lib/goldStar.ts    -> the dashboard's gold star
 *   web/lib/jobView.ts     -> the Salary column sort
 *
 * star_rules.json already pins the star VERDICT across the two star copies, and
 * that was not enough. A verdict is coarse: two implementations can agree a job
 * is starred while disagreeing wildly about what it pays, and jobView.ts is not
 * covered by it at all. That is how jobView came to be the only copy reading
 * "$45.00 / hr" as hourly while the other two silently read it as annual — for
 * 199 live rows, with no test anywhere going red.
 *
 * So this file pins the NUMBER, over a corpus of real salary strings, and
 * asserts it three ways:
 *
 *   1. goldStar.ts === jobView.ts            (the two that ship together)
 *   2. goldStar.ts === the fixture's number  (which binds Python, since
 *   3. jobView.ts  === the fixture's number   scraper/test_gold_star.py
 *                                             asserts the same file)
 *
 * (1) alone would pass happily if both TS copies broke identically. (2) and (3)
 * alone would give a worse error message. Together they name which file is
 * wrong.
 *
 * Corpus: fixtures/salary_corpus.json, beside canonical_target_keys.json — the
 * other cross-language contract fixture — and NOT in web/lib/, because nothing
 * imports it; only tests read it. Regenerate with scraper/dump_salary_corpus.py.
 *
 * Run: node lib/__tests__/salaryParity.test.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import ts from 'typescript'

const here = dirname(fileURLToPath(import.meta.url))

const compile = (src) =>
  ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
  }).outputText

const loadModule = async (src) =>
  import(`data:text/javascript;base64,${Buffer.from(compile(src)).toString('base64')}`)

// goldStar.ts imports './star_rules.json' and '@/types/job'. Inline the JSON and
// drop the type-only import, exactly as goldStar.test.mjs does — a data: URL
// cannot resolve either one.
const goldSrc = readFileSync(join(here, '../goldStar.ts'), 'utf8')
const rulesJson = readFileSync(join(here, '../star_rules.json'), 'utf8')
const gold = await loadModule(
  goldSrc
    .replace(/import type \{ Job \} from '@\/types\/job'\n/, '')
    .replace(/import rules from '\.\/star_rules\.json'/, `const rules = ${rulesJson}`),
)

// jobView.ts has only a type-only import, which the transpile erases — and that
// is load-bearing, not luck. See the final check in this file.
const viewSrc = readFileSync(join(here, '../jobView.ts'), 'utf8')
const view = await loadModule(viewSrc)

const corpus = JSON.parse(readFileSync(join(here, '../../../fixtures/salary_corpus.json'), 'utf8'))

let pass = 0, fail = 0
const check = (name, condition, detail = '') => {
  if (condition) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? `\n${detail}` : ''}`) }
}

// Medians introduce halves, and 25.45 * 2080 is not bit-identical to
// ((21.80 + 29.10) / 2) * 2080 in IEEE754. Compare money to the cent.
const same = (a, b) =>
  (a === null && b === null) || (a !== null && b !== null && Math.abs(a - b) < 0.01)

const show = (v) => (v === null ? 'null' : String(v))

// ---- the corpus must remain a corpus -------------------------------------
// Without these, the cheapest way to make a failure go away is to delete the
// offending row, and the test quietly stops testing anything.
check('the corpus is large enough to be a corpus', corpus.cases.length >= 250,
      `        only ${corpus.cases.length} entries`)

const FAMILIES = [
  ['hourly', /\/\s*h(?:ou)?r\b|per\s*hour|hourly/i],
  ['weekly', /\/\s*w(?:k|eek)\b|per\s*week|weekly/i],
  ['monthly', /\/\s*mo(?:nth)?\b|per\s*month|monthly/i],
  ['yearly', /\/\s*(?:yr|year)\b|per\s*year|annually|annualiz/i],
  ['a k suffix', /\$\s*[\d,.]+\s*k\b/i],
  ['an adder', /\b(?:plus|additional)\b|\+/i],
  ['a bare second bound', /\$\s*[\d,]+(?:\.\d+)?\s*[-–—]\s*\d/],
  ['a non-USD currency', /\b(?:CAD|AUD|NZD|SGD|HKD|MXN)\b|CA\$/i],
  ['no figure at all', /^[^$]*$/],
]
for (const [family, re] of FAMILIES) {
  const n = corpus.cases.filter((c) => re.test(c.salary)).length
  check(`the corpus covers ${family}`, n >= 5, `        only ${n} entries match`)
}

// ---- 1. the two shipped TypeScript copies must agree ---------------------
const drift = corpus.cases
  .map((c) => ({ s: c.salary, g: gold.annualSalary(c.salary), v: view.annualSalary(c.salary) }))
  .filter((d) => !same(d.g, d.v))
check('goldStar.ts and jobView.ts agree on every corpus string', drift.length === 0,
  `        ${drift.length} of ${corpus.cases.length} disagree — the gold star and the ` +
  `Salary column would tell the user different things about the same row:\n` +
  drift.slice(0, 10).map((d) =>
    `          ${JSON.stringify(d.s)}\n` +
    `            goldStar.ts ${show(d.g)}\n` +
    `            jobView.ts  ${show(d.v)}` +
    (d.g && d.v ? `   (${(Math.max(d.g, d.v) / Math.min(d.g, d.v)).toFixed(2)}x apart)` : ''),
  ).join('\n') + (drift.length > 10 ? `\n          ...and ${drift.length - 10} more` : ''))

// ---- 2 & 3. and both must match the fixture, which is what binds Python ---
for (const [label, mod] of [['goldStar.ts', gold], ['jobView.ts', view]]) {
  const wrong = corpus.cases
    .map((c) => ({ s: c.salary, want: c.annual, got: mod.annualSalary(c.salary) }))
    .filter((d) => !same(d.got, d.want))
  check(`${label} matches every number in the fixture`, wrong.length === 0,
    `        ${wrong.length} of ${corpus.cases.length} differ. If the parser change was ` +
    `deliberate, regenerate with scraper/dump_salary_corpus.py and READ the diff:\n` +
    wrong.slice(0, 10).map((d) =>
      `          ${JSON.stringify(d.s)}  fixture ${show(d.want)}  ${label} ${show(d.got)}`,
    ).join('\n') + (wrong.length > 10 ? `\n          ...and ${wrong.length - 10} more` : ''))
}

// ---- the constraint that forces the duplication in the first place -------
// If this ever fails, the three copies are not a choice any more — the whole
// reason jobView.ts holds its own copy instead of importing one is that
// statusMutations.test.mjs transpiles it into a data: URL, and a data: URL
// cannot resolve a relative value import. A type-only import erases and is fine.
check('jobView.ts still has no value imports',
  !/^\s*import\s+(?!type\b)/m.test(viewSrc),
  '        statusMutations.test.mjs transpiles jobView.ts into a data: URL, which\n' +
  '        cannot resolve a relative value import. Keep type-only imports only.')

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
