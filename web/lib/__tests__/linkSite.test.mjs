/**
 * The Site filter: which job site the Apply button sends you to. TypeScript is
 * transpiled in memory so the test runs the shipped helpers without adding a
 * test framework.
 *
 * The contract under test is "the filter agrees with the button": linkSite
 * reads the SAME href the Apply link opens (applicationHref), so an Easy Apply
 * row is LinkedIn whatever its apply_url says, and a GitHub-tracker row whose
 * link lands on Workday is Workday even though it was never found on LinkedIn.
 * Hosts below are real shapes from the live table.
 *
 * Run: node lib/__tests__/linkSite.test.mjs
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

const { applicationHref, linkSite, matchesSite, SITE_FILTERS } = await loadTs('../jobView.ts')

let pass = 0, fail = 0
const check = (name, condition, detail = '') => {
  if (condition) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}

// A LinkedIn-found row with no resolved apply link -- the commonest shape.
const job = (overrides = {}) => ({
  id: '4450000001', title: 'Software Engineer Intern', company: 'Acme', location: 'Austin, TX',
  url: 'https://www.linkedin.com/jobs/view/4450000001/', apply_url: null, is_easy_apply: false,
  tier: 'APPLY', status: 'new', found_at: '2026-09-01T00:00:00Z', reason: '', norm_key: 'acme|swe intern',
  logo_url: null, salary: null,
  ...overrides,
})
const lands = (name, j, want) =>
  check(`${name} -> ${want}`, linkSite(j) === want, `got ${linkSite(j)}`)

const WORKDAY = 'https://clr.wd5.myworkdayjobs.com/en-US/clr_careers/job/Austin/Software-Engineer-Intern_JR-12345'
const WORKDAY_SITE = 'https://wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs/job/CHARLOTTE-NC/Software-Engineer_R-574030'
const ASHBY = 'https://jobs.ashbyhq.com/acme/1b2c3d4e-5f60-4a7b-8c9d-0e1f2a3b4c5d'
const ICIMS = 'https://careers-acme.icims.com/jobs/1234/software-engineer-intern/job'

// ---- each host lands where it should -------------------------------------
lands('LinkedIn row, no apply link', job(), 'linkedin')
lands('Workday (tenant.wdN subdomain)', job({ id: 'gh:1', url: WORKDAY, apply_url: WORKDAY }), 'workday')
// Workday's other public domain -- how the SimplifyJobs tracker links Wells
// Fargo. Missed on the first pass; caught in review against the live README.
lands('Workday (wdN.myworkdaysite.com)',
      job({ id: 'gh:3', url: WORKDAY_SITE, apply_url: WORKDAY_SITE }), 'workday')
lands('Ashby', job({ id: 'ats:1', url: ASHBY, apply_url: ASHBY }), 'ashby')
lands('Greenhouse job-boards', job({ url: 'https://job-boards.greenhouse.io/acme/jobs/123', apply_url: 'https://job-boards.greenhouse.io/acme/jobs/123' }), 'greenhouse')
lands('Greenhouse EU', job({ url: 'https://job-boards.eu.greenhouse.io/acme/jobs/123', apply_url: 'https://job-boards.eu.greenhouse.io/acme/jobs/123' }), 'greenhouse')
lands('Greenhouse boards', job({ url: 'https://boards.greenhouse.io/acme/jobs/123', apply_url: 'https://boards.greenhouse.io/acme/jobs/123' }), 'greenhouse')
lands('iCIMS', job({ url: ICIMS, apply_url: ICIMS }), 'icims')

// ---- the button's precedence is the filter's precedence ------------------
lands('Easy Apply row with a Workday apply_url stays on LinkedIn',
      job({ is_easy_apply: true, apply_url: WORKDAY }), 'linkedin')
lands('non-Easy-Apply LinkedIn row whose apply link resolved to Workday',
      job({ apply_url: WORKDAY }), 'workday')
lands('apply_url null falls back to url', job({ url: WORKDAY, apply_url: null }), 'workday')

// ---- host matching is dot-anchored and case-insensitive -------------------
lands('bare linkedin.com', job({ url: 'https://linkedin.com/jobs/view/1/' }), 'linkedin')
lands('upper-case host', job({ url: 'https://WWW.LinkedIn.com/jobs/view/1/' }), 'linkedin')
lands('lookalike notlinkedin.com is not LinkedIn', job({ url: 'https://notlinkedin.com/jobs/1' }), 'other')
lands('myworkdayjobs.com with no tenant is still Workday', job({ url: 'https://myworkdayjobs.com/x' }), 'workday')

// ---- what we cannot place is 'other', never a throw -----------------------
lands('unknown ATS (Lever)', job({ url: 'https://jobs.lever.co/acme/uuid', apply_url: 'https://jobs.lever.co/acme/uuid' }), 'other')
lands('company careers page', job({ url: 'https://careers.acme.com/jobs/1', apply_url: 'https://careers.acme.com/jobs/1' }), 'other')
lands('malformed url', job({ url: 'not a url' }), 'other')
lands('empty url', job({ url: '' }), 'other')
lands('mailto: has no host', job({ url: 'mailto:hr@acme.com' }), 'other')

// ---- matchesSite ----------------------------------------------------------
const sample = [job(), job({ url: WORKDAY }), job({ url: ASHBY }), job({ url: 'not a url' })]
check("'all' keeps every row", sample.every(j => matchesSite(j, 'all')))
check('a row matches its own site', sample.every(j => matchesSite(j, linkSite(j))))
check('a Workday row does not match Ashby', !matchesSite(job({ url: WORKDAY }), 'ashby'))
check("'other' is selectable and catches the unplaceable", matchesSite(job({ url: 'not a url' }), 'other'))
// ---- the pieces that must move together, checked at the source ---------
// rowHeight.test.mjs reads component source for the same reason: these are
// contracts between files, and no single module's exports can assert them.
const toolbar = readFileSync(join(here, '../../components/Toolbar.tsx'), 'utf8')
const siteBlock = /label="Site"[\s\S]*?\]\} \/>/.exec(toolbar)?.[0] ?? ''
const dropdownKeys = [...siteBlock.matchAll(/key: '([a-z]+)'/g)].map(m => m[1])
check('the Site dropdown exists in Toolbar.tsx', dropdownKeys.length > 0)
check('every Site dropdown key is in SITE_FILTERS (what readUrl accepts)',
      dropdownKeys.every(k => SITE_FILTERS.includes(k)), `keys: ${dropdownKeys.join(',')}`)
check('every SITE_FILTERS value has a dropdown option',
      SITE_FILTERS.every(k => dropdownKeys.includes(k)), `keys: ${dropdownKeys.join(',')}`)
check("'all' is the first option, which Dropdown treats as the off state", dropdownKeys[0] === 'all')

// The filter is only honest if the button reads the same href. Both Apply
// buttons must go through applicationHref and carry no inline copy.
for (const file of ['JobTable.tsx', 'JobDrawer.tsx']) {
  const src = readFileSync(join(here, '../../components', file), 'utf8')
  check(`${file} opens applicationHref(job)`, src.includes('applicationHref(job)'))
  check(`${file} has no inline apply_url ?? url copy`, !/apply_url \?\? job\.url/.test(src))
}

// ---- grouped rows: the leader decides, because its link is the button's ---
const leader = { ...job({ id: 'gh:2', url: WORKDAY, apply_url: WORKDAY }),
                 duplicates: [job({ is_easy_apply: true })] }
lands('grouped leader on Workday with an Easy Apply twin', leader, 'workday')

// ---- applicationHref is the button's expression, exactly -----------------
for (const easy of [false, true]) for (const apply of [null, WORKDAY]) {
  const j = job({ is_easy_apply: easy, apply_url: apply })
  check(`applicationHref(easy=${easy}, apply_url=${apply ? 'set' : 'null'}) matches the button`,
        applicationHref(j) === (j.is_easy_apply ? j.url : (j.apply_url ?? j.url)))
}

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
