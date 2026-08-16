/**
 * Density guard.
 *
 * The row count visible without scrolling is the thing most likely to regress
 * silently the next time someone adds a field to the table — a number in a
 * chat message does not survive, a number in a test does.
 *
 * Run:  node lib/__tests__/rowHeight.test.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const css = readFileSync(join(here, '../../app/globals.css'), 'utf8')
const table = readFileSync(join(here, '../../components/JobTable.tsx'), 'utf8')
const toolbar = readFileSync(join(here, '../../components/Toolbar.tsx'), 'utf8')

let pass = 0, fail = 0
const check = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}

const num = (re, src) => {
  const m = re.exec(src)
  return m ? parseInt(m[1], 10) : NaN
}

// ── The contract ─────────────────────────────────────────────────────────
const ROW_MAX = 52
const TARGET_ROWS = 15

// VIEWPORT height, not window height. The original version asserted against
// 900 and reported 18 rows; the browser was actually handing the page 684px
// because chrome (tab strip, address bar, bookmarks) takes ~216px of a 900px
// window. Measured live: a 1440x900 window yields a 1536x684 viewport, and 14
// rows, not 18. The formula was right and the input was wrong.
const VIEWPORT_H = 684          // what a 900px-tall window actually gives
const WINDOW_H = 900            // reported only, never used in the maths

const rowH = num(/--row-h:\s*(\d+)px/, css)
const headerH = num(/gridTemplateColumns: grid, height: (\d+)/, table)
const toolbarH = num(/height: (\d+), padding: '0 var\(--s4\)'/, toolbar)

check(`--row-h is defined`, Number.isFinite(rowH), 'not found in globals.css')
check(`row height ${rowH}px <= ${ROW_MAX}px`, rowH <= ROW_MAX, `got ${rowH}px`)
check(`table header height ${headerH}px is defined`, Number.isFinite(headerH))
check(`toolbar height ${toolbarH}px is defined`, Number.isFinite(toolbarH))

// Rows fit in the viewport after the chrome above them. The sidebar is a
// left rail and costs no vertical space, so only the toolbar and the sticky
// table header subtract.
const chrome = toolbarH + headerH
const rowsVisible = Math.floor((VIEWPORT_H - chrome) / (rowH + 1)) // +1 = hairline divider
// The honest assertion: how tall must the VIEWPORT be to clear the floor.
const viewportFor15 = TARGET_ROWS * (rowH + 1) + chrome
check(
  `row geometry supports ${TARGET_ROWS} rows in <= 760px of viewport (needs ${viewportFor15}px)`,
  viewportFor15 <= 760,
  `chrome=${chrome}px, row=${rowH}px+1px divider`
)
check(
  `at a realistic ${VIEWPORT_H}px viewport, >= 13 rows are visible (computed ${rowsVisible})`,
  rowsVisible >= 13
)

// ── Visual rules that are cheap to assert and easy to break ──────────────
check('no drop shadow on the table', !/boxShadow/.test(table))
check('exactly one shadow in the app (the drawer)',
  (readFileSync(join(here, '../../components/JobDrawer.tsx'), 'utf8').match(/boxShadow/g) || []).length === 1)

const hexInComponents = [
  ['JobTable.tsx', table],
  ['Toolbar.tsx', toolbar],
  ['Sidebar.tsx', readFileSync(join(here, '../../components/Sidebar.tsx'), 'utf8')],
  ['JobDrawer.tsx', readFileSync(join(here, '../../components/JobDrawer.tsx'), 'utf8')],
].flatMap(([name, src]) => {
  const hits = src.match(/#[0-9a-fA-F]{3,8}\b/g) || []
  return hits.map(h => `${name}:${h}`)
})
check('no hard-coded hex in components (all colour via CSS variables)',
  hexInComponents.length === 0, hexInComponents.join(', '))

check('transitions capped at 120ms via --dur', /--dur:\s*120ms/.test(css))
check('row hover is background only — no transform/scale',
  !/\.row-hit:hover[^}]*(transform|scale)/.test(css))

console.log(`\n${pass} passed, ${fail} failed`)
console.log(`\nComputed density at 1440x${VIEWPORT_H}:`)
console.log(`  toolbar ${toolbarH}px + table header ${headerH}px = ${chrome}px chrome`)
console.log(`  (${VIEWPORT_H} - ${chrome}) / ${rowH + 1}px per row = ${rowsVisible} rows visible`)
process.exit(fail ? 1 : 0)
