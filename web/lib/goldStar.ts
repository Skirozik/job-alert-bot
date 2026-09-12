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
// RANGE-AWARE, and that is not a nicety. The second bound of a range often
// carries no dollar sign of its own — "$20-71/hr" — and a pattern that requires
// one finds a single amount, whose median is itself. The band then ranks at its
// FLOOR, silently reverting the median rule for the commonest way a range is
// written: 123 distinct live strings, 253 rows.
//
// The shape comes from classifier.py's tool schema, which tells the model to
// emit "e.g. '$20-30/hr'". Do NOT go fix it there: that schema sits inside a
// ~10K-token cached prefix, so editing it invalidates the cache for every job,
// and the model would produce the shape anyway.
//
// The k suffix is captured on BOTH bounds: "$200k-$260k" otherwise reads as 200,
// falls to the magnitude branch as an hourly rate, and annualises to $416,000.
const MONEY = /\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(k\b)?(?:\s*[-–—]\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(k\b)?)?/gi
// A "k" written once governs BOTH bounds: "$39.7–72.8k/yr" is 39,700 to 72,800,
// not 39.70 to 72,800. Only propagated onto a bound below 1000, which is what
// stops "$100,000 - 401k" inheriting it.
const K_MAX = 1000
// The slash form is its OWN alternative, outside the \b...\b wrapper. Inside
// it, the leading \b demanded a word character before the "/", so "$45.00 / hr"
// never matched and fell through to the magnitude branch — dead for 199 live
// rows, and it let the artifact guard below be bypassed. "/hour" must be spelt
// out too. jobView.ts has always written it this way; now all three agree.
const HOURLY_HINT = /\b(?:per\s*hour|hourly|an\s*hour)\b|\/\s*(?:hr|hour)\b/i
// Biweekly is listed BEFORE weekly or "bi-weekly" reads as weekly and doubles.
const BIWEEKLY = /\bbi-?weekly\b|\bevery\s+two\s+weeks\b/i
const PER_WEEK = /\b(?:per\s*week|weekly)\b|\/\s*w(?:k|eek)\b/i
const PER_MONTH = /\b(?:per\s*month|monthly)\b|\/\s*mo(?:nth)?\b/i
const PER_YEAR = /\b(?:per\s*year|annually|annualized|annualised|a\s*year)\b|\/\s*(?:yr|year)\b/i
// A bonus written after the band, never part of it.
const ADDER = /\b(?:plus|additional)\b|\+/i
// Text that looks like money or a unit and is neither. All stripped before the
// band is read:
//   "20 hrs/week"  — a WORKLOAD. PER_WEEK matches the "/week" inside it, and an
//                    hourly rate then gets multiplied by 52 instead of 2080:
//                    "$29.32 - $43.99/hr (part-time, 20 hrs/week)" came out at
//                    $1,906 a year.
//   "401k", "403b" — a retirement plan. The range-aware MONEY above reads the
//                    bare second operand, so "$60,000 - 401k match" became a
//                    $60,000–$401,000 band with a median of $230,500.
//   "15%"          — a bonus percentage, read as the number 15 and wrecking the
//                    band's span. Stripped rather than excluded by a lookahead:
//                    a (?!\s*%) guard makes the engine backtrack the NUMBER to
//                    satisfy it, so "$55,000 - 15% bonus" matches the "1" and
//                    yields [55000, 1] instead of failing cleanly.
const NOISE = /\d[\d\s–—.-]*\s*(?:hrs?|hours)\s*(?:\/|per\s+|a\s+)\s*(?:wk|week)s?\b|\b40[13]\s*\(?[kb]\)?|\d[\d.,]*\s*%/gi
// A figure we cannot compare to a USD bar. Returning null — "this posting states
// no pay we can judge" — is the honest answer; converting would need a rate
// table that goes stale silently and tells nobody. Switching to the median is
// what pushed marginal CAD bands over the line: "$68,250–$78,000 CAD" medians to
// 73,125, clears a 72,800 USD bar, and is really about US$53,000. A Canadian
// role at a listed company still stars on COMPANY; it just stops claiming high
// stated pay. (Non-dollar currencies never parsed anyway — no "$".)
//
// The symbol forms need a guard on the character BEFORE them, or "S$" matches
// inside "US$120,000" and every American posting written that way vanishes. A
// lookbehind would be the obvious tool and is the wrong one: JS lookbehind is a
// PARSE-time SyntaxError on Safari < 16.4, so this file would take down the
// whole client bundle rather than just the salary column. (?:^|[^A-Za-z]) is
// equivalent here and universally supported.
const NONUSD = /\b(?:CAD|AUD|NZD|SGD|HKD|MXN|EUR|GBP|INR)\b|(?:^|[^A-Za-z])(?:CA|A|NZ|S|HK)\$/i

const HOURS_PER_YEAR = 2080

// Above this the figure is a scraper artifact rather than pay. Intel posts
// "$91,198-$91,202/hr" — annual numbers mislabelled hourly — which annualises
// to $189,691,840 and clears any threshold trivially.
const MAX_PLAUSIBLE_ANNUAL = 500_000
// And below this it is a total, not a rate. The cap alone is one-sided: a $3,840
// lump-sum stipend picked up beside a workload clause multiplies to $199,680,
// which is wrong but perfectly plausible, so nothing catches it. jobView.ts has
// carried this floor since the sort shipped; the star copies never did.
const MIN_PLAUSIBLE_ANNUAL = 10_000

// What a figure quoted in each unit can plausibly BE. A posting states its rate
// and then restates it — "$730/week (~$18.25/hr)", "$22.50–$29.00/hr
// ($46,800–$60,320 annualized)" — and a median across both magnitudes is
// meaningless. Filtering to the window of the unit we settled on keeps the band
// and drops the restatement, whichever way round they were written.
//
// Ranges are [low, high). The hourly ceiling of 1000 is not a real constraint:
// 1000 × 2080 already exceeds MAX_PLAUSIBLE_ANNUAL, so the effective hourly
// ceiling was always ~$240/hr and no genuine rate is lost.
const UNIT_WINDOW: Record<string, [number, number]> = {
  hour: [1, 1_000],
  week: [50, 20_000],
  biweek: [100, 40_000],
  month: [200, 100_000],
  year: [1_000, Infinity],
}
const UNIT_MULT: Record<string, number> = {
  hour: HOURS_PER_YEAR, week: 52, biweek: 26, month: 12, year: 1,
}

// A band whose floor is at hourly scale and whose ceiling is at annual scale is
// two units written as one range, not a generous employer: "$17.98-$135,700",
// "$37.22 - $150,000". All three conditions must hold, so a merely wide band
// survives — the widest legitimate one measured is 80x ("$1,500–$2,500/month
// part-time; $80,000–$120,000/yr full-time"), and a bare ratio test at 100x
// leaves only 1.25x of margin before it starts eating real postings silently.
const CROSS_SCALE_LOW = 1_000
const CROSS_SCALE_HIGH = 10_000
const CROSS_SCALE_RATIO = 100

/** Which pay unit this text states, or null — the one written FIRST.
 *
 * Not a fixed precedence. Every mixed-unit posting in the wild is written
 * "<rate> (<restatement>)", so the headline unit is the earlier one, and any
 * fixed order gets half of them backwards: hourly-before-weekly reads
 * "$730/week (~$18.25/hr)" as a $374/hr job, and weekly-before-hourly reads
 * "$22.50/hr ($46,800 annualized)" as annual.
 *
 * Ties are impossible between different units at the same offset except for
 * biweekly, where "bi-weekly" also contains "weekly" three characters in — and
 * position already resolves that correctly (0 < 3). The explicit ordering of
 * the pairs below is only there to document the hazard. */
function firstUnit(text: string): string | null {
  let best: string | null = null
  let at: number | null = null
  const pairs: [string, RegExp][] = [
    ['biweek', BIWEEKLY], ['week', PER_WEEK], ['month', PER_MONTH],
    ['hour', HOURLY_HINT], ['year', PER_YEAR],
  ]
  for (const [unit, pattern] of pairs) {
    const m = pattern.exec(text)
    if (m && (at === null || m.index < at)) { best = unit; at = m.index }
  }
  return best
}

/** The posting's pay as one annual number, or null when it states none.
 *
 * THE MEDIAN OF THE BAND, not its floor (changed 2026-09-11). The floor was
 * chosen to stop a high ceiling creating false stars, and it did — but it also
 * sank every wide band regardless of its midpoint, and a wide band is how the
 * best-paying employers post.
 *
 * EVERY UNIT ANNUALISES. The old rule knew only hourly and treated anything
 * else as a yearly figure, so Composio's "$10,000/mo" read as a $10,000-a-year
 * job and earned no star against a $120,000 reality.
 *
 * The order of the steps below IS the fix for most of what was wrong here.
 * Each one is commented where it happens. Mirrors scraper/gold_star.py exactly;
 * salaryParity.test.mjs asserts that against a corpus of real salary strings. */
export function annualSalary(salary: string | null | undefined): number | null {
  const text = (salary ?? '').trim()
  if (!text || NONUSD.test(text)) return null

  const clean = text.replace(NOISE, ' ')
  // Amounts come from the part BEFORE any adder: "$21.80–$29.10/hr plus
  // $5.09/hr differential" is a band and a bonus, and counting the bonus drags
  // the median down.
  const band = clean.split(ADDER)[0]
  let amounts: number[] = []
  for (const m of band.matchAll(MONEY)) {
    const pair: ([number, boolean] | null)[] = []
    for (const [raw, k] of [[m[1], m[2]], [m[3], m[4]]] as const) {
      if (!raw) { pair.push(null); continue }
      const n = Number(raw.replace(/,/g, '')) * (k ? 1000 : 1)
      pair.push(Number.isFinite(n) ? [n, Boolean(k)] : null)
    }
    // "$39.7–72.8k" states the k once and means it twice. Only a bound still
    // under 1000 inherits it, which is what stops "$100,000 - 401k" — were it
    // not already stripped as noise — from becoming a $401,000 ceiling.
    const [a, b] = pair
    if (a && b) {
      if (b[1] && !a[1] && a[0] < K_MAX) a[0] *= 1000
      else if (a[1] && !b[1] && b[0] < K_MAX) b[0] *= 1000
    }
    for (const v of [a, b]) if (v) amounts.push(v[0])
  }
  if (!amounts.length) return null
  // A $0 floor is a placeholder, and the median hides it: "$0 - $200,000"
  // medians to a perfectly plausible $100,000. No real posting floors at zero.
  if (Math.min(...amounts) === 0) return null

  // THE UNIT COMES FROM THE BAND, falling back to the cleaned string. Reading
  // the whole string unconditionally let an adder clause's unit win over one
  // the band had already stated: "$25.00/hr + $2,000/month housing stipend"
  // multiplied $25 by 12 and returned $300. The one fallback is still needed
  // for the reason the old comment gave — a "plus" clause sometimes carries the
  // only "/hr" in the text.
  //
  // There is deliberately NO further fallback to the RAW text. It would let a
  // workload clause act as the pay unit of last resort, which sounds reasonable
  // and is not: every live string whose only unit sits inside a workload clause
  // is a lump sum or states no rate at all — "$3,840 stipend (14-16 weeks, 20
  // hrs/week)" would become $199,680 a year, and "Paid (15-25 hrs/week); rate
  // not specified" quotes no rate to annualise. Measured: 4 such strings live,
  // 4 of them wrong under that fallback, 0 helped.
  const unit = firstUnit(band) ?? firstUnit(clean)

  // A floor at hourly scale under a ceiling at annual scale is two units
  // written as one range. Checked on the figures AS WRITTEN, before the window
  // below removes half the evidence: for "$37.22 - $150,000/yr" the yearly
  // window drops the $37.22 and what is left looks like an ordinary salary.
  //
  // Any string carrying an hourly token is exempt, not merely one whose unit
  // RESOLVED to hourly. A rate and its annualised restatement legitimately span
  // 2080x, and they are written in both orders: "$45/hr ($93,600/yr)" resolves
  // to hourly, "$93,600/yr ($45/hr)" resolves to yearly, and both are the same
  // well-formed posting. The presence of "/hr" anywhere is what says a sub-1000
  // figure is a rate rather than a malformed bound — and the strings this guard
  // exists for carry no hourly token at all.
  const loV = Math.min(...amounts)
  const hiV = Math.max(...amounts)
  if (!HOURLY_HINT.test(clean) && loV < CROSS_SCALE_LOW && hiV >= CROSS_SCALE_HIGH
      && hiV / loV >= CROSS_SCALE_RATIO) return null

  if (unit) {
    const [low, high] = UNIT_WINDOW[unit]
    const kept = amounts.filter((v) => v >= low && v < high)
    // Everything fell outside the window this unit can plausibly hold, so the
    // unit and the figures contradict each other and neither can be trusted:
    // "$91,198-$91,202/hr" is annual pay mislabelled hourly.
    if (!kept.length) return null
    amounts = kept
  }

  const sorted = [...amounts].sort((x, y) => x - y)
  const n = sorted.length
  const mid = n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2

  // No unit stated. Decided on the band's FLOOR, not its median: a
  // "$900 - $1,200" band medians above 1000 and would flip to annual on the
  // midpoint alone. A four-figure-plus number is never an hourly rate.
  const annual = unit ? mid * UNIT_MULT[unit]
                      : (sorted[0] >= 1000 ? mid : mid * HOURS_PER_YEAR)

  // The plausibility band lives HERE, not in the caller, so that all three
  // copies of this function return the same value for the same string and
  // salaryParity.test.mjs can compare them directly.
  if (annual < MIN_PLAUSIBLE_ANNUAL || annual > MAX_PLAUSIBLE_ANNUAL) return null
  return annual
}

/** True when the annualised median clears the threshold. */
function salaryClearsBar(salary: string | null | undefined): boolean {
  const annual = annualSalary(salary)
  // The plausibility band is applied inside annualSalary now, so anything that
  // reaches here is already a number worth comparing.
  if (annual === null) return false
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
