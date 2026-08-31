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
const MONEY = /\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/g
const HOURLY_HINT = /\b(per\s*hour|\/\s*hr|hourly|an\s*hour)\b/i

/** True when the LOWER bound of a stated range clears the threshold.
 *
 * Lower bound, not upper and not the average: "$20 - $70/hr" is a $20/hr job
 * with a ceiling, and starring it on that ceiling is exactly the false positive
 * that turns the badge into noise. */
function salaryClearsBar(salary: string | null | undefined): boolean {
  const text = (salary ?? '').trim()
  if (!text) return false

  const amounts: number[] = []
  for (const m of text.matchAll(MONEY)) {
    const n = Number(m[1].replace(/,/g, ''))
    if (Number.isFinite(n)) amounts.push(n)
  }
  if (!amounts.length) return false

  const low = Math.min(...amounts)
  const { hourly, annual } = rules.thresholds
  // Unit from the text where it says so; otherwise from magnitude — a
  // four-figure-plus number is never an hourly rate.
  if (HOURLY_HINT.test(text)) return low >= hourly
  if (low >= 1000) return low >= annual
  return low >= hourly
}

/** Why this job is starred, or [] when it is not.
 *
 * Reasons rather than a boolean so the drawer can say WHY — a badge with no
 * explanation is one you learn to ignore. Order is stable so the shared parity
 * fixture can compare lists directly against the Python side. */
export function starReasons(job: Job): StarReason[] {
  // The gate, checked first so it cannot be accidentally reordered below a signal.
  if (job.is_easy_apply) return []

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
