/**
 * Duplicate grouping regressions, built from pairs observed on Ifiok's live
 * dashboard. The TypeScript source is transpiled in memory so these tests run
 * the shipped implementation without adding a test framework.
 *
 * Run: node lib/__tests__/dupes.test.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import ts from 'typescript'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, '../dupes.ts'), 'utf8')
const compiled = ts.transpileModule(src, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText
const mod = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
const { canonicalTargetKey, groupNearDuplicates, mergeGroupedJobs } = mod

let pass = 0, fail = 0
const check = (name, condition, detail = '') => {
  if (condition) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}

let sequence = 0
const job = (overrides = {}) => ({
  id: `fixture:${++sequence}`,
  title: 'Software Engineer Intern',
  company: 'Example',
  location: 'New York, NY',
  url: `https://www.linkedin.com/jobs/view/example-${1000000 + sequence}`,
  search_term: '',
  description: null,
  norm_key: '',
  tier: 'APPLY',
  reason: '',
  status: 'new',
  posted_at: null,
  found_at: new Date(1_700_000_000_000 - sequence * 1000).toISOString(),
  logo_url: null,
  apply_url: null,
  is_easy_apply: false,
  salary: null,
  ...overrides,
})

const grouped = (a, b) => {
  const out = groupNearDuplicates([a, b])
  return out.length === 1 && out[0].duplicates?.length === 1
}

console.log('-- canonical application targets --')
const continentalA = job({
  url: 'https://clr.wd5.myworkdayjobs.com/en-US/clr_careers/job/Oklahoma-City-OK/Data-Analyst-Intern--Summer-2027-_R02591-1',
})
const continentalB = job({
  url: 'https://clr.wd5.myworkdayjobs.com/CLR_Careers/job/Oklahoma-City-OK/Data-Analyst-Intern--Summer-2027-_R02591-1?utm_source=Simplify&ref=Simplify',
})
check('Workday locale and tracking variants share a target',
  canonicalTargetKey(continentalA) === canonicalTargetKey(continentalB))

const chevronA = job({
  url: 'https://chevron.wd5.myworkdayjobs.com/en-US/university/job/Houston/XMLNAME-2026-2027-Software-Engineer-Intern_R000072398-1',
})
const chevronB = job({
  url: 'https://chevron.wd5.myworkdayjobs.com/en-US/jobs/job/Houston/XMLNAME-2026-2027-Software-Engineer-Intern_R000072398',
})
check('Workday generated -1 suffix does not create a new requisition',
  canonicalTargetKey(chevronA) === canonicalTargetKey(chevronB))

const lpl = job({
  url: 'https://lplfinancial.wd1.myworkdayjobs.com/university/job/Fort-Mill/Summer-Intern-2027---Data_R-052914',
})
check('a hyphen inside a Workday requisition is retained as identity',
  canonicalTargetKey(lpl).endsWith(':r052914'), canonicalTargetKey(lpl))

const trackedA = job({ url: 'https://example.com/jobs/42?utm_source=x&ref=Simplify&team=data' })
const trackedB = job({ url: 'https://example.com/jobs/42?team=data&utm_campaign=y' })
check('tracking params are stripped but functional params remain',
  canonicalTargetKey(trackedA) === canonicalTargetKey(trackedB))

console.log('-- confirmed live duplicate families --')
const generalNew = job({
  id: 'gh:4ce8193bde7d3775', company: 'General Matter',
  title: 'Summer 2027 Internship - Software Engineering', location: 'Los Angeles, CA',
  url: 'https://job-boards.greenhouse.io/generalmatter/jobs/5377118008', status: 'new',
})
const generalApplied = job({
  id: 'gh:f933d8b6cce48531', company: 'General Matter',
  title: 'Software Engineering Intern', location: 'LA',
  url: 'https://job-boards.greenhouse.io/generalmatter/jobs/5377118008', status: 'applied',
})
const generalGroup = groupNearDuplicates([generalNew, generalApplied])
check('same Greenhouse target collapses across sections', generalGroup.length === 1)
check('Applied wins over New so the group cannot reappear in To apply',
  generalGroup[0]?.status === 'applied')
check('both source rows remain reachable', generalGroup[0]?.duplicates?.[0]?.id === generalApplied.id)

const liveMerge = mergeGroupedJobs(groupNearDuplicates([generalApplied]), [generalNew])
check('a live LinkedIn duplicate merges into the existing group',
  liveMerge.length === 1 && liveMerge[0]?.duplicates?.length === 1)
check('a live update cannot move an applied group back to To apply',
  liveMerge[0]?.status === 'applied')

const staleSameId = mergeGroupedJobs(
  groupNearDuplicates([{ ...generalApplied, status: 'applied' }]),
  [{ ...generalApplied, status: 'new', title: 'Updated title from stale response' }],
)
check('a stale response for the same id cannot undo Applied',
  staleSameId[0]?.status === 'applied')

check('Continental Workday variants group', grouped(
  job({ ...continentalA, company: 'Continental Resources', title: 'Data Analyst Intern - Summer 2027', location: 'Oklahoma City, OK' }),
  job({ ...continentalB, company: 'Continental Resources', title: 'Data Analyst Intern', location: 'Oklahoma City, OK' }),
))

check('GE direct and LinkedIn title variants group', grouped(
  job({ company: 'GE Aerospace', title: 'Digital Technology Intern - Summer 2027', location: 'Livonia, MI',
    url: 'https://geaerospace.wd5.myworkdayjobs.com/ge_externalsite/job/Livonia/Digital-Technology-Intern---US---Livonia--MI---Summer-2027_R5038079-1' }),
  job({ company: 'GE Aerospace', title: 'Digital Technology Intern – US – Livonia, MI – Summer 2027', location: 'Livonia, MI',
    url: 'https://www.linkedin.com/jobs/view/digital-technology-intern-at-ge-aerospace-4455245573' }),
))

check('Sanmina legal-name variants with the same LinkedIn role group', grouped(
  job({ company: 'SANMINA-SCI TECHNOLOGY INDIA PRIVATE LIMITED', title: 'Platform Engineering Intern', location: 'Huntsville, AL',
    url: 'https://www.linkedin.com/jobs/view/platform-engineering-intern-at-sanmina-sci-4451270277' }),
  job({ company: 'Sanmina', title: 'Platform Engineering Intern', location: 'Huntsville, AL',
    url: 'https://www.linkedin.com/jobs/view/platform-engineering-intern-at-sanmina-4454536970' }),
))

check('WEC/We Energies aliases and overlapping locations group', grouped(
  job({ company: 'We Energies', title: 'Intern - Renewables Data Analytics', location: 'Green Bay, WI',
    url: 'https://www.linkedin.com/jobs/view/intern-renewables-data-analytics-at-we-energies-4453729436' }),
  job({ company: 'WEC Energy Group', title: 'Renewables Data Analytics Intern', location: 'Milwaukee, WI · Green Bay, WI',
    url: 'https://careers.wecenergygroup.com/We_Energies/job/Milwaukee-Intern-Renewables-Data-Analytics-WI-53203/1419740100/?utm_source=Simplify' }),
))

check('Regions compact +1 location can use the Workday path city', grouped(
  job({ company: 'Regions', title: '2027 ETP Intern - Technology - Operations - Digital - and Data - Analytics', location: 'Hoover, AL +1',
    url: 'https://regions.wd5.myworkdayjobs.com/regions_careers/job/Hoover-AL---Riverchase-Operations-Center-Birmingham-AL/ETP_R105426' }),
  job({ company: 'Regions Bank', title: '2027 ETP Intern - Technology, Operations, Digital, and Data - Technology', location: 'Birmingham, AL',
    url: 'https://www.linkedin.com/jobs/view/regions-etp-intern-4456469040' }),
))

console.log('-- false-merge guards --')
check('KeyBank Security and Technology tracks stay separate', !grouped(
  job({ company: 'KeyBank', title: '2027 Summer Key Technology & Services: Security, Business & Strategy Track Internship- Cleveland', location: 'Brooklyn, OH',
    url: 'https://www.linkedin.com/jobs/view/key-security-4454285454' }),
  job({ company: 'KeyBank', title: '2027 Summer Key Technology & Services: Technology Track Internship- Cleveland', location: 'Brooklyn, OH',
    url: 'https://www.linkedin.com/jobs/view/key-technology-4454299332' }),
))

check('American Express AI and Software roles stay separate', !grouped(
  job({ company: 'American Express', title: 'Campus Undergraduate Summer Internship Program - 2027 AI Engineer, Enterprise Technology Services', location: 'Phoenix, AZ',
    url: 'https://www.linkedin.com/jobs/view/amex-ai-4454450161' }),
  job({ company: 'American Express', title: 'Campus Undergraduate Summer Internship Program - 2027 Software Engineer, Enterprise Technology Services', location: 'Phoenix, AZ',
    url: 'https://www.linkedin.com/jobs/view/amex-software-4454456120' }),
))

check('Vanguard Security and Application Development stay separate', !grouped(
  job({ company: 'Vanguard', title: 'College to Corporate IT Internship - Risk & Security - Engineer (NC)', location: 'Charlotte, NC',
    url: 'https://www.linkedin.com/jobs/view/vanguard-security-4455559836' }),
  job({ company: 'Vanguard', title: 'College to Corporate IT Internship - Application Development (NC)', location: 'Charlotte, NC',
    url: 'https://www.linkedin.com/jobs/view/vanguard-app-4455567828' }),
))

check('TikTok Ads and E-Commerce teams stay separate', !grouped(
  job({ company: 'TikTok USDS Joint Venture', title: 'Machine Learning Engineer Intern (Ads) - 2027 Summer', location: 'San Jose, CA',
    url: 'https://www.linkedin.com/jobs/view/tiktok-ads-4456409802' }),
  job({ company: 'TikTok USDS Joint Venture', title: 'Machine Learning Engineer Intern (E-Commerce) - 2027 Summer', location: 'San Jose, CA',
    url: 'https://www.linkedin.com/jobs/view/tiktok-commerce-4456438172' }),
))

check('IBM roles in different cities stay separate', !grouped(
  job({ company: 'IBM', title: 'Data and AI Intern 2027', location: 'Armonk, NY',
    url: 'https://www.linkedin.com/jobs/view/ibm-armonk-4450000001' }),
  job({ company: 'IBM', title: 'Data and AI Intern 2027', location: 'Buffalo, NY',
    url: 'https://www.linkedin.com/jobs/view/ibm-buffalo-4450000002' }),
))

check('Palantir SWE and Forward Deployed SWE stay separate without one target', !grouped(
  job({ company: 'Palantir', title: 'Software Engineer Internship', location: 'Washington, DC',
    url: 'https://jobs.lever.co/palantir/11111111-1111-1111-1111-111111111111' }),
  job({ company: 'Palantir', title: 'Forward Deployed Software Engineer Internship - USG', location: 'Washington, DC',
    url: 'https://www.linkedin.com/jobs/view/palantir-fdse-4450000003' }),
))

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
