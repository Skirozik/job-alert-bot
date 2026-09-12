import type { Job } from '@/types/job'

/* ── Filters ─────────────────────────────────────────────────────────────
   A single view key drives BOTH the sidebar selection and the table, so the
   two cannot drift apart. It is also what round-trips through the URL. */

export type ViewKey =
  | 'to-apply' | 'caveat' | 'my-list'          // REVIEW
  | 'applied' | 'saved' | 'dismissed'          // TRACKING
  | 'heard-back' | 'interview' | 'offer' | 'rejected'   // OUTCOMES

export type RoleFilter = 'all' | 'internships' | 'entry-level'
export type SourceFilter = 'all' | 'direct' | 'linkedin'
export type DateFilter = 'all' | '24h' | '7d' | '30d'

const INTERN_RE = /intern|internship|co[\s-]?op|apprentice|summer analyst|summer associate|trainee/i

export const isActive = (j: Job) => {
  const s = j.status ?? 'new'
  // Review and Tracking are mutually exclusive queues. Saving is an action,
  // not a second badge layered onto To Apply; a saved job stays reachable in
  // Saved and returns to review only through Reset to new.
  return s === 'new'
}

/** The view predicate. REVIEW views are implicitly active-only — a job you've
 *  already acted on is not still "to apply". TRACKING views are status lookups.
 *
 *  There is deliberately no INELIGIBLE or ALL view. The Ineligible one could
 *  only ever show 518 of 51,151 rows, with search running client-side over
 *  just those — it looked like a way to catch a wrongly-rejected job and was
 *  not one. Removing both also drops a 0.52 MB fetch from every refresh. */
export function matchesView(j: Job, v: ViewKey): boolean {
  switch (v) {
    case 'to-apply':   return j.tier === 'APPLY' && isActive(j)
    case 'caveat':     return j.tier === 'APPLY_CAVEAT' && isActive(j)
    case 'my-list':    return (j.tier === 'APPLY' || j.tier === 'APPLY_CAVEAT') && isActive(j)
    case 'applied':    return j.status === 'applied'
    case 'saved':      return j.status === 'saved'
    case 'dismissed':  return j.status === 'dismissed'
    // 'applied' above is the still-waiting bucket, not every application ever.
    // Once something progresses it belongs in its own view, not both.
    case 'heard-back': return j.status === 'heard_back'
    case 'interview':  return j.status === 'interview'
    case 'offer':      return j.status === 'offer'
    case 'rejected':   return j.status === 'rejected'
  }
}

export const matchesRole = (j: Job, r: RoleFilter) =>
  r === 'all' ? true : r === 'internships' ? INTERN_RE.test(j.title) : !INTERN_RE.test(j.title)

export const isDirect = (j: Job) => j.id.startsWith('ats:')

export const matchesSource = (j: Job, s: SourceFilter) =>
  s === 'all' ? true : s === 'direct' ? isDirect(j) : !isDirect(j)

/* ── Site ────────────────────────────────────────────────────────────────
   WHERE THE APPLY LINK GOES, not where the row was found. Source above is the
   provenance axis (caught by the ATS watcher vs. everything else), and its
   "LinkedIn only" sweeps in GitHub-tracker rows whose link lands on Workday.
   This filter answers the question the user actually asks at the Apply
   button: "which site am I about to be sent to?" */
// The type is DERIVED from the runtime list, not declared beside it. readUrl
// validates ?site= against SITE_FILTERS, so a key that reached the union and
// the dropdown but not this list would type-check, render, write itself into
// the URL, and then be thrown away on reload. Deriving makes that impossible.
export const SITE_FILTERS = ['all', 'linkedin', 'workday', 'ashby', 'greenhouse', 'icims', 'other'] as const
export type SiteFilter = typeof SITE_FILTERS[number]
export type LinkSite = Exclude<SiteFilter, 'all'>

/** The href the Apply button opens. Easy Apply stays on LinkedIn; otherwise
 *  the ATS link wins when the scraper captured one. JobTable and JobDrawer
 *  read this so the filter and the button cannot disagree. dupes.ts keeps a
 *  private copy: both files are transpiled into data: URLs by tests and a
 *  data: URL cannot resolve a relative value import. */
export const applicationHref = (j: Job): string =>
  j.is_easy_apply ? j.url : (j.apply_url ?? j.url)

// Dot-anchored suffix match: "acme.wd1.myworkdayjobs.com" and
// "job-boards.eu.greenhouse.io" hit; "notlinkedin.com" does not.
const SITE_HOSTS: [LinkSite, string][] = [
  ['linkedin', 'linkedin.com'],
  ['workday', 'myworkdayjobs.com'],
  // Workday's other public domain, "wd1.myworkdaysite.com/recruiting/<tenant>/...".
  // The SimplifyJobs tracker links Wells Fargo, Brevan Howard and Devon Energy
  // this way (12 rows on the live README), and each opens a Workday apply page.
  ['workday', 'myworkdaysite.com'],
  ['ashby', 'ashbyhq.com'],
  ['greenhouse', 'greenhouse.io'],
  ['icims', 'icims.com'],
]

/** Which job site the Apply button lands on. A gh: row that links to Workday
 *  is 'workday'; an Easy Apply row is 'linkedin' whatever its apply_url says. */
export function linkSite(j: Job): LinkSite {
  let host: string
  try { host = new URL(applicationHref(j)).hostname.toLowerCase().replace(/^www\./, '') }
  catch { return 'other' }
  for (const [site, d] of SITE_HOSTS) if (host === d || host.endsWith('.' + d)) return site
  return 'other'
}

export const matchesSite = (j: Job, s: SiteFilter) => s === 'all' || linkSite(j) === s

export function matchesDate(j: Job, d: DateFilter): boolean {
  if (d === 'all') return true
  const hours = d === '24h' ? 24 : d === '7d' ? 168 : 720
  return Date.now() - new Date(j.found_at).getTime() <= hours * 3_600_000
}

export function matchesSearch(j: Job, q: string): boolean {
  if (!q.trim()) return true
  const n = q.toLowerCase()
  return j.company.toLowerCase().includes(n) || j.title.toLowerCase().includes(n)
}

/* ── Salary ──────────────────────────────────────────────────────────────
   The column is ~90px. "$4,000.00/wk - $6,000.00/wk" does not fit, so the
   table gets a compact form and the drawer keeps the original string
   verbatim — normalising is for scanning, not a replacement for the source.

   THE FIGURES COME FROM bandFigures(), THE SAME READER THE SORT USES. This
   function used to pull only numbers that carried their own "$", and a range
   is usually written with one: "$20-71/hr" drew as "$20/hr" on 113 live rows,
   and "$39.7–72.8k/yr" as "$40/yr". The column and the sort key then disagreed
   about the same string — sorted as a $20–$71 band, shown as a flat $20 —
   and a row that reads "$20/hr" on screen is exactly the one a reader would
   dismiss as low-paid. The adder split is what stops "$25.00/hr + $2,000/month
   housing stipend" drawing as a "$25–2k/hr" range (32 live rows). */
export function compactSalary(raw: string | null): string {
  if (!raw) return ''
  const unit = /\/?\s*(hr|hour|wk|week|mo|month|yr|year)/i.exec(raw)?.[1]?.toLowerCase() ?? ''
  const u = unit.startsWith('hr') || unit.startsWith('hour') ? '/hr'
          : unit.startsWith('wk') || unit.startsWith('week') ? '/wk'
          : unit.startsWith('mo') ? '/mo'
          : unit.startsWith('yr') || unit.startsWith('year') ? '/yr' : ''
  const nums = bandFigures(raw.replace(NOISE, ' ').split(ADDER)[0])
  if (!nums.length) return raw.length > 12 ? raw.slice(0, 11) + '…' : raw
  // Integers only. "$39.7–72.8k/yr" wrapped to two lines inside a 44px
  // row; the decimal was never doing anything for triage.
  const k = (n: number) => (n >= 1000 ? `${Math.round(n / 1000)}k` : `${Math.round(n)}`)
  const [lo, hi] = [nums[0], nums[1]]
  if (hi != null && hi !== lo) {
    // Share the k-suffix across a range: "$4–6k/wk", not "$4k–$6k/wk".
    const bothK = lo >= 1000 && hi >= 1000
    return bothK ? `$${k(lo).replace('k', '')}–${k(hi)}${u}` : `$${k(lo)}–${k(hi)}${u}`
  }
  return `$${k(lo)}${u}`
}

/* ── Locations ───────────────────────────────────────────────────────────
   Some postings carry 21 locations concatenated without a separator
   ("London, UKParis, France"). Split on comma-runs and on a lower→upper
   boundary, which is where the concatenation seam falls. */
export function splitLocations(loc: string | null): string[] {
  if (!loc) return []
  let t = loc.trim()

  // Shape 1: a bare count with no list behind it — "21 Locations". The list
  // genuinely is not in the data, so there is nothing to expand; return it as
  // the single value it is rather than inventing entries.
  if (/^\d+\s+locations?$/i.test(t)) return [t]

  // Shape 2: a count PREFIX followed by the concatenated list —
  // "21 locationsBoston, MASanta Ana, CA...". Drop the prefix, keep the list.
  t = t.replace(/^\d+\s+locations?/i, '')

  // Shape 3: entries concatenated with no separator. Two seams occur:
  //   a US state code butted against the next city  ("Austin, TXFort Mill, SC")
  //   a lowercase char butted against a capital      ("London, UKParis, France")
  // The state-code rule must come first: "TXFort" has no lowercase before the
  // capital, so the second rule alone never fires on it.
  //
  // The second rule requires a LOWERCASE AFTER the capital. Without that it
  // split "Flexible - Any SpaceX Site" into "...Any Space" + "X Site", and
  // "JPN TOKY 1-3-1 FLR12 BldgJA" into "...Bldg" + "JA" - a lone capital
  // mid-token is not the start of a new city.
  //
  // The marker is NUL rather than "|", because real values contain literal
  // pipes ("US | California | San Francisco") and splitting on those turned one
  // hierarchical location into three.
  t = t
    .replace(/([A-Z]{2})([A-Z][a-z])/g, '$1\u0000$2')
    .replace(/([a-z)])([A-Z][a-z])/g, '$1\u0000$2')

  const parts = t.split(/\u0000|\s*;\s*/).map(x => x.trim()).filter(Boolean)

  // Re-join camelCase city names that the lowercase rule split apart - McLean,
  // DeKalb, LaGrange, St. Louis, O'Fallon. The seam inside those is
  // indistinguishable from a concatenation seam, so they are repaired
  // afterwards rather than guarded against beforehand.
  const out: string[] = []
  for (const p of parts) {
    if (out.length && CAMEL_CITY_PREFIX.test(out[out.length - 1])) out[out.length - 1] += p
    else out.push(p)
  }
  return out
}

const CAMEL_CITY_PREFIX = /^(Mc|Mac|De|Di|Du|La|Le|Van|Von|St\.?|Ste\.?|O'|D')$/i

/** True when the stored value is only a count — the individual locations were
 *  never captured, so neither the table nor the drawer can list them. */
export const isLocationCountOnly = (loc: string | null): boolean =>
  !!loc && /^\d+\s+locations?$/i.test(loc.trim())

/* ── Optional columns ────────────────────────────────────────────────────
   One rule, applied to Resume, Salary and Source alike: a column that has no
   values in the CURRENT filtered set hides itself. This is what lets the same
   table serve three personas — Beyonce and Hassan have no suggested_resume
   column at all — without a per-tenant branch. */
export type OptionalCol = 'source' | 'resume' | 'salary'

export function visibleOptionalColumns(rows: Job[]): Record<OptionalCol, boolean> {
  return {
    source: rows.some(isDirect),
    // No 'N/A' check: classifier.py coerces anything outside
    // Mobile/AI/Frontend/General to General, so the only case left is the
    // column being absent on a persona whose schema omits it.
    resume: rows.some(j => !!j.suggested_resume),
    salary: rows.some(j => !!j.salary),
  }
}

/* ── Sorting ─────────────────────────────────────────────────────────────*/
/* Salary as one annual number, for sorting only. Null when the string carries
 * no figure at all.
 *
 * The regex is a deliberate copy of goldStar.ts's MONEY rather than an import:
 * this module is transpiled into a data: URL by statusMutations.test.mjs, and a
 * data: URL cannot resolve a relative value import. Keep the two in step by
 * hand; they answer different questions (does it clear a bar, vs how does it
 * rank) and only share the shape of the money they read.
 *
 * EVERYTHING NORMALISES TO A YEAR, because the column mixes units freely --
 * "$23-$43/hr" sits next to "$37,000 - $82,000 USD" and neither sorts against
 * the other as written. 2080 hours matches star_rules.json, where 35/hr and
 * 72,800/yr are defined as the same pay.
 *
 * BIWEEKLY IS WHY THIS IS NOT JUST goldStar's PARSER. "$1,635.00 - $3,185.00
 * biweekly" appears 18 times in the live table. A magnitude-only fallback reads
 * 1,635 as four figures, calls it annual, and ranks a $42,510/yr job below a
 * $20/hr one. Weekly and monthly are handled for the same reason.
 *
 * THE MEDIAN OF THE BAND RANKS (Zach's call, 2026-09-11). "$20 - $70/hr" ranks
 * at $45/hr. This deliberately differs from salaryClearsBar, which tests the
 * FLOOR against a threshold -- a different question. Deciding whether a job
 * clears a bar must not be fooled by a high ceiling, but deciding where a job
 * sits in a ranked list is better served by the middle of what was advertised:
 * on a floor, every wide band sinks to the bottom regardless of its midpoint,
 * and wide bands are how the best-paying employers post.
 *
 * A $0 FLOOR IS STILL REJECTED OUTRIGHT, because the median would hide it:
 * "$0.00 - $10,000,000.00" medians to a perfectly plausible $5,000,000 and
 * "$0 - $200,000" to $100,000, so both would rank near the top on a midpoint
 * that the plausibility band alone cannot catch. No real posting floors at
 * zero. */
/* The k suffix is captured, not ignored. "$200k-$260k" otherwise reads as 200,
 * falls through to the magnitude fallback as an hourly rate, and annualises to
 * $416,000 -- and the k-form is how the best-paying rows are written, so every
 * one of them led the board at roughly double its real pay. */
const SALARY_MONEY = /\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(k\b)?(?:\s*[-–—]\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(k\b)?)?/gi
// A "k" written once governs BOTH bounds: "$39.7–72.8k/yr" is 39,700 to 72,800,
// not 39.70 to 72,800. Only propagated onto a bound below 1000, which is what
// stops "$100,000 - 401k" inheriting it.
const K_MAX = 1000
// The slash form is its OWN alternative, outside the \b...\b wrapper. Inside
// it, the leading \b demanded a word character before the "/", so "$45.00 / hr"
// never matched and fell through to the magnitude branch — dead for 199 live
// rows, and it let the artifact guard below be bypassed. "/hour" must be spelt
// out too. goldStar.ts and gold_star.py were fixed to match this file.
const PER_HOUR = /\b(?:per\s*hour|hourly|an\s*hour)\b|\/\s*(?:hr|hour)\b/i
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
//   "401k", "403b" — a retirement plan. The range-aware SALARY_MONEY above reads
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
// PARSE-time SyntaxError on Safari < 16.4, so goldStar.ts would take down the
// whole client bundle rather than just the salary column. (?:^|[^A-Za-z]) is
// equivalent here and universally supported.
const NONUSD = /\b(?:CAD|AUD|NZD|SGD|HKD|MXN|EUR|GBP|INR)\b|(?:^|[^A-Za-z])(?:CA|A|NZ|S|HK)\$/i

/** Every dollar figure in a band, in written order, with the k-suffix applied.
 *
 * Shared by compactSalary (what the column draws) and annualSalary (how the
 * column sorts), so the two cannot read the same string differently. Callers
 * strip NOISE and split off the ADDER first — that is deliberately not done
 * here, because annualSalary needs the cleaned text afterwards for its unit
 * and cross-scale checks. */
function bandFigures(band: string): number[] {
  const amounts: number[] = []
  for (const m of band.matchAll(SALARY_MONEY)) {
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
  return amounts
}

const HOURS_PER_YEAR = 2080

// Above this the figure is a scraper artifact rather than pay. Intel posts
// "$91,198-$91,202/hr" — annual numbers mislabelled hourly — which annualises
// to $189,691,840 and clears any threshold trivially.
const MAX_PLAUSIBLE = 500_000
// And below this it is a total, not a rate. The cap alone is one-sided: a $3,840
// lump-sum stipend picked up beside a workload clause multiplies to $199,680,
// which is wrong but perfectly plausible, so nothing catches it. this file has
// carried this floor since the sort shipped; the star copies never did.
const MIN_PLAUSIBLE = 10_000

// What a figure quoted in each unit can plausibly BE. A posting states its rate
// and then restates it — "$730/week (~$18.25/hr)", "$22.50–$29.00/hr
// ($46,800–$60,320 annualized)" — and a median across both magnitudes is
// meaningless. Filtering to the window of the unit we settled on keeps the band
// and drops the restatement, whichever way round they were written.
//
// Ranges are [low, high). The hourly ceiling of 1000 is not a real constraint:
// 1000 × 2080 already exceeds MAX_PLAUSIBLE, so the effective hourly
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
    ['hour', PER_HOUR], ['year', PER_YEAR],
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
 * Each one is commented where it happens. Mirrors scraper/gold_star.py and web/lib/goldStar.ts exactly;
 * salaryParity.test.mjs asserts that against a corpus of real salary strings. */
export function annualSalary(raw: string | null | undefined): number | null {
  const text = (raw ?? '').trim()
  if (!text || NONUSD.test(text)) return null

  const clean = text.replace(NOISE, ' ')
  // Amounts come from the part BEFORE any adder: "$21.80–$29.10/hr plus
  // $5.09/hr differential" is a band and a bonus, and counting the bonus drags
  // the median down.
  const band = clean.split(ADDER)[0]
  let amounts = bandFigures(band)
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
  if (!PER_HOUR.test(clean) && loV < CROSS_SCALE_LOW && hiV >= CROSS_SCALE_HIGH
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
  if (annual < MIN_PLAUSIBLE || annual > MAX_PLAUSIBLE) return null
  return annual
}

export type SortKey = 'company' | 'title' | 'location' | 'found_at' | 'salary'
export type SortDir = 'asc' | 'desc'

export function sortJobs(rows: Job[], key: SortKey, dir: SortDir): Job[] {
  const s = [...rows]
  s.sort((a, b) => {
    let r: number
    if (key === 'found_at') r = new Date(a.found_at).getTime() - new Date(b.found_at).getTime()
    else if (key === 'salary') {
      const av = annualSalary(a.salary)
      const bv = annualSalary(b.salary)
      // A row with no figure sorts LAST in BOTH directions, so these returns
      // deliberately skip the dir flip below. 45% of To apply has no salary,
      // and flipping them would bury every paying job under 1,600 blanks the
      // moment you asked for highest first.
      if (av === null && bv === null) {
        return new Date(b.found_at).getTime() - new Date(a.found_at).getTime()
      }
      if (av === null) return 1
      if (bv === null) return -1
      r = av - bv
    }
    else r = (a[key] ?? '').localeCompare(b[key] ?? '', undefined, { sensitivity: 'base' })
    return dir === 'asc' ? r : -r
  })
  return s
}

/** "4h ago" — the table form. Full timestamp goes in the title attribute. */
export function relativeTime(iso: string): string {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const h = Math.round(mins / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.round(h / 24)
  return d < 30 ? `${d}d ago` : `${Math.round(d / 30)}mo ago`
}

export const fullTimestamp = (iso: string) =>
  new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  })
