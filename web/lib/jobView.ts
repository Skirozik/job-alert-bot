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
   verbatim — normalising is for scanning, not a replacement for the source. */
export function compactSalary(raw: string | null): string {
  if (!raw) return ''
  const unit = /\/?\s*(hr|hour|wk|week|mo|month|yr|year)/i.exec(raw)?.[1]?.toLowerCase() ?? ''
  const u = unit.startsWith('hr') || unit.startsWith('hour') ? '/hr'
          : unit.startsWith('wk') || unit.startsWith('week') ? '/wk'
          : unit.startsWith('mo') ? '/mo'
          : unit.startsWith('yr') || unit.startsWith('year') ? '/yr' : ''
  const nums: number[] = []
  const re = /\$\s*([\d,]+(?:\.\d+)?)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(raw)) !== null) nums.push(parseFloat(m[1].replace(/,/g, '')))
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
const SALARY_MONEY = /\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(k\b)?/gi
const PER_HOUR = /\b(?:per\s*hour|hourly|an\s*hour)\b|\/\s*(?:hr|hour)\b/i
const BIWEEKLY = /\bbi-?weekly\b|\bevery\s+two\s+weeks\b/i
const PER_WEEK = /\b(?:per\s*week|weekly)\b|\/\s*w(?:k|eek)\b/i
const PER_MONTH = /\b(?:per\s*month|monthly)\b|\/\s*mo(?:nth)?\b/i
const PER_YEAR = /\b(?:per\s*year|annually|annualized|annualised|a\s*year)\b|\/\s*(?:yr|year)\b/i

// A bonus or differential written after the band, never part of it.
const ADDER = /\b(?:plus|additional)\b|\+/i

const HOURS_PER_YEAR = 2080

/* Sanity band for an internship, in annualised dollars. Outside it the number
 * is a scraper artifact, not pay, and the honest answer is "unknown" -- which
 * sorts last -- rather than a ranking built on nonsense.
 *
 * Every one of these is live data, and without the band they were the entire
 * top of "highest first":
 *   Intel        "$91,198-$91,202/hr"      annual figures mislabelled /hr -> $189,691,840
 *   Clearwater   "$0.00 - $10,000,000.00"  placeholder range
 *   Morningstar  "$500-$2,000 annually"    a stipend, not a salary
 *   Gen Dynamics "$0.01-$0.02/yr"          placeholder
 * The floor also keeps a flat "$1,000" total stipend from ranking against real
 * pay. A row rejected here still DISPLAYS its raw string; only ranking ignores it. */
const MIN_PLAUSIBLE = 10_000
const MAX_PLAUSIBLE = 500_000

export function annualSalary(raw: string | null | undefined): number | null {
  const text = (raw ?? '').trim()
  if (!text) return null

  // Read amounts only from the part BEFORE any adder. "$21.80-$29.10/hr plus
  // $5.09/hr differential" is a band and a bonus, and a global minimum takes
  // the $5.09 as the salary, ranking a $45k co-op at $10,587. Counting the
  // first two figures instead would fix that case and break "$30/hr plus
  // $2/hr", which is one figure and an adder rather than a range. Cutting at
  // the adder is what the sentence actually means, so it handles both.
  //
  // Units are still read from the WHOLE string: "plus" clauses sometimes carry
  // the only "/hr" in the text.
  const band = text.split(ADDER)[0]
  const amounts: number[] = []
  for (const m of band.matchAll(SALARY_MONEY)) {
    const n = Number(m[1].replace(/,/g, '')) * (m[2] ? 1000 : 1)
    // Zero is KEPT, not filtered. Dropping it turned "$0.00 - $10,000,000.00"
    // into a ten-million-dollar internship instead of the placeholder it is.
    if (Number.isFinite(n)) amounts.push(n)
  }
  if (!amounts.length) return null          // "Not mentioned", "Competitive"
  if (Math.min(...amounts) === 0) return null   // placeholder band, see above

  // Median, so a two-figure band ranks at its midpoint. Sorted first, because
  // a band is not guaranteed to be written low-to-high.
  const sorted = [...amounts].sort((x, y) => x - y)
  const mid = sorted.length % 2
    ? sorted[(sorted.length - 1) / 2]
    : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2

  // Order matters twice over. Biweekly before weekly, or "biweekly" reads as
  // weekly and doubles. An explicit unit before the magnitude fallback, or
  // "$500-$2,000 annually" falls through and is multiplied by 2080.
  let annual: number
  if (BIWEEKLY.test(text)) annual = mid * 26
  else if (PER_WEEK.test(text)) annual = mid * 52
  else if (PER_MONTH.test(text)) annual = mid * 12
  else if (PER_HOUR.test(text)) annual = mid * HOURS_PER_YEAR
  else if (PER_YEAR.test(text)) annual = mid
  // No unit stated. A four-figure-plus number is never an hourly rate. Tested
  // on the band's FLOOR, not its median: a "$900 - $1,200" band medians above
  // 1000 and would flip to annual while its floor says hourly.
  else annual = sorted[0] >= 1000 ? mid : mid * HOURS_PER_YEAR

  return annual >= MIN_PLAUSIBLE && annual <= MAX_PLAUSIBLE ? annual : null
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
