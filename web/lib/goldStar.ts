import type { Job } from '@/types/job'
import rules from './star_rules.json'

/* Gold star: which postings are worth a hand-curated resume.
 *
 * A star does NOT mean "good job" — the list is already filtered to jobs worth
 * applying to. It means high marginal return on 30–60 minutes of tailoring:
 * P(curation flips the decision) × value of the job.
 *
 *   (top-tier company OR high stated pay OR mobile role) AND NOT is_easy_apply
 *
 * The Easy Apply term is a GATE, not another OR. LinkedIn Easy Apply reuses
 * whatever resume is already on file, so one curated for it is effort that
 * never reaches a human. As an OR it would also star every ATS job — thousands
 * — and a star on everything is a star on nothing.
 *
 * Derived, never stored: no column, no migration, no backfill. Every input is
 * already in COLS_BASE (app/page.tsx), so tuning star_rules.json is an edit and
 * a redeploy.
 *
 * The rules are shared with scraper/gold_star.py, which drives the ntfy push,
 * and both assert every `cases` entry in star_rules.json — the phone and the
 * dashboard must not disagree about what is starred. */

export type StarReason = 'company' | 'salary' | 'mobile'

/* Mirrors scraper/db.py norm_company. Keeps "Regions" and "Regions Bank" from
 * being two different employers, and stops "Applebee's" inheriting Apple's star. */
const COMPANY_NOISE = new Set([
  'inc', 'llc', 'corp', 'co', 'company', 'international', 'electronics',
  'financial', 'technologies', 'technology', 'labs', 'group', 'holdings',
  'solutions', 'software', 'ltd', 'plc', 'industries', 'services', 'systems',
  'digital', 'global', 'ventures',
])

function normCompany(raw: string | null | undefined): string {
  let c = (raw ?? '').toLowerCase().trim()
  c = c.replace(/\(yc.*?\)/g, '')
  c = c.replace(/'s\b/g, '')
  c = c.replace(/[^a-z0-9 ]/g, ' ')
  let toks = c.split(/\s+/).filter(Boolean)
  if (toks[0] === 'the') toks = toks.slice(1)
  const stripped = toks.filter((t) => !COMPANY_NOISE.has(t))
  return (stripped.length ? stripped : toks).join(' ').trim()
}

const STARRED_COMPANIES = new Set(rules.companies.map(normCompany))

const MOBILE_TITLE = /\b(ios|swift|swiftui|android|mobile|react native)\b/i
// Mirror of scraper/gold_star.py. Both sides assert every `cases` entry in
// star_rules.json, so a change here that is not made there turns a test red in
// both suites -- which is the point.
//
// The k suffix is captured: "$200k-$260k" otherwise reads as 200, falls to the
// magnitude branch as an hourly rate, and annualises to $416,000.
const MONEY = /\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(k\b)?/gi
const HOURLY_HINT = /\b(per\s*hour|\/\s*hr|hourly|an\s*hour)\b/i
// Biweekly is tested BEFORE weekly or "biweekly" reads as weekly and doubles.
const BIWEEKLY = /\bbi-?weekly\b|\bevery\s+two\s+weeks\b/i
const PER_WEEK = /\b(per\s*week|weekly)\b|\/\s*w(k|eek)\b/i
const PER_MONTH = /\b(per\s*month|monthly)\b|\/\s*mo(nth)?\b/i
const PER_YEAR = /\b(per\s*year|annually|annualized|annualised|a\s*year)\b|\/\s*(yr|year)\b/i
// A bonus written after the band, never part of it.
const ADDER = /\b(plus|additional)\b|\+/i

const HOURS_PER_YEAR = 2080

// Above this the figure is a scraper artifact rather than pay. Intel posts
// "$91,198-$91,202/hr" -- annual numbers mislabelled hourly -- which
// annualises to $189,691,840 and clears any threshold trivially.
const MAX_PLAUSIBLE_ANNUAL = 500_000

/** The posting's pay as one annual number, or null when it states none.
 *
 * THE MEDIAN OF THE BAND, not its floor (changed 2026-09-11). The floor was
 * chosen to stop a high ceiling creating false stars, and it did — but it also
 * sank every wide band regardless of its midpoint, and a wide band is how the
 * best-paying employers post. IBM's "$61,200–$138,600" and Cisco's
 * "$44,000–$185,000" both failed the bar on a floor that is the
 * rising-sophomore end of the range. Measured on the live table: 110 open APPLY
 * rows clear the bar on the median that the floor denied.
 *
 * EVERY UNIT ANNUALISES. The old rule knew only hourly and treated anything
 * else as a yearly figure, so Composio's "$10,000/mo" read as a $10,000-a-year
 * job and earned no star against a $120,000 reality. */
function annualSalary(salary: string | null | undefined): number | null {
  const text = (salary ?? '').trim()
  if (!text) return null

  // Amounts come from the part BEFORE any adder: "$21.80–$29.10/hr plus
  // $5.09/hr differential" is a band and a bonus, and counting the bonus drags
  // the median down. Units are read from the whole string, since a "plus"
  // clause sometimes carries the only "/hr" in the text.
  const band = text.split(ADDER)[0]
  const amounts: number[] = []
  for (const m of band.matchAll(MONEY)) {
    const n = Number(m[1].replace(/,/g, '')) * (m[2] ? 1000 : 1)
    if (Number.isFinite(n)) amounts.push(n)
  }
  if (!amounts.length) return null
  // A $0 floor is a placeholder, and the median hides it: "$0 - $200,000"
  // medians to a perfectly plausible $100,000. No real posting floors at zero.
  if (Math.min(...amounts) === 0) return null

  const sorted = [...amounts].sort((x, y) => x - y)
  const n = sorted.length
  const mid = n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2

  if (BIWEEKLY.test(text)) return mid * 26
  if (PER_WEEK.test(text)) return mid * 52
  if (PER_MONTH.test(text)) return mid * 12
  if (HOURLY_HINT.test(text)) return mid * HOURS_PER_YEAR
  if (PER_YEAR.test(text)) return mid
  // No unit stated. Decided on the band's FLOOR, not its median: a
  // "$900 - $1,200" band medians above 1000 and would flip to annual on the
  // midpoint alone. A four-figure-plus number is never an hourly rate.
  return sorted[0] >= 1000 ? mid : mid * HOURS_PER_YEAR
}

/** True when the annualised median clears the threshold. */
function salaryClearsBar(salary: string | null | undefined): boolean {
  const annual = annualSalary(salary)
  if (annual === null) return false
  // Garbage clears any floor trivially, and switching to a median made that
  // worse rather than better, so it is rejected rather than starred.
  if (annual > MAX_PLAUSIBLE_ANNUAL) return false
  return annual >= rules.thresholds.annual
}

/** Why this job is starred, or [] when it is not.
 *
 * Reasons rather than a boolean so the drawer can say WHY — a badge with no
 * explanation is one you learn to ignore. Order is stable so the shared parity
 * fixture can compare lists directly against the Python side. */
export function starReasons(job: Job): StarReason[] {
  // TWO gates, both before any signal, so neither can be reordered below one.
  //
  // 1. Easy Apply reuses the resume already on file, so one curated for it is
  //    effort that never reaches a human.
  // 2. APPLY only, never APPLY_CAVEAT. A caveat job already carries a known
  //    reservation -- that is what the tier MEANS -- so it is a strange
  //    candidate for an hour of tailoring, and reserving the star for clean
  //    fits is what keeps it scarce.
  if (job.is_easy_apply) return []
  if (job.tier !== 'APPLY') return []

  const out: StarReason[] = []
  if (STARRED_COMPANIES.has(normCompany(job.company))) out.push('company')
  if (salaryClearsBar(job.salary)) out.push('salary')
  if (job.suggested_resume === 'Mobile' || MOBILE_TITLE.test(job.title ?? '')) out.push('mobile')
  return out
}

export function isStarred(job: Job): boolean {
  return starReasons(job).length > 0
}

export const REASON_LABEL: Record<StarReason, string> = {
  company: 'Top-tier company',
  salary: 'High stated pay',
  mobile: 'Mobile role — your App Store app is the differentiator',
}

export type StarFilter = 'all' | 'starred'

/* A filter, deliberately not a ViewKey. A new view would touch six places (the
   union, matchesView, Sidebar GROUPS, VIEWS, EMPTY, TRACKING_VIEWS) and add an
   O(n) count pass per render for the same result. With ~1,550 rows in To apply
   the stars are unfindable by scrolling, so SOME filter is required.

   It lives HERE rather than in jobView.ts on purpose: jobView is transpiled
   into a data: URL by statusMutations.test.mjs, and a data: URL cannot resolve
   a relative value import. A type-only import is erased and would be fine; an
   `import { isStarred }` is not. Keeping star logic in the star module avoids
   the coupling entirely, which is the better structure regardless. */
export const matchesStar = (job: Job, f: StarFilter) => f === 'all' || isStarred(job)
