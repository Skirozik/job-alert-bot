// Conservative duplicate grouping for the dashboard.
//
// The same application reaches us through LinkedIn, a tracker, and a company
// ATS. Those copies often have different row ids, slightly different titles,
// and tracking-decorated URLs. Deleting one is unsafe: a company can publish
// several similar internships at the same time. Instead, this module groups
// only high-confidence matches and keeps every source link in `duplicates`.
//
// Matching is intentionally layered:
//
//   1. A canonical application target (ATS requisition or cleaned URL) is
//      definitive. This is O(n) and catches URL variants without fuzzy text.
//   2. Remaining rows are compared only inside small company buckets. Same-
//      source rows need an identical role signature; cross-source rows may use
//      guarded title containment. Location, season, year, and role-family
//      conflicts all veto a fuzzy match.
//
// Descriptions are deliberately not used. They account for roughly 90% of
// Supabase egress and employers routinely reuse the same boilerplate for
// genuinely different roles. Compact metadata is both cheaper and safer.

import type { Job, Status } from '@/types/job'

const COMPANY_NOISE = new Set([
  'a', 'an', 'the', 'and', 'inc', 'incorporated', 'llc', 'corp',
  'corporation', 'co', 'company', 'international', 'electronics',
  'financial', 'technologies', 'technology', 'labs', 'group', 'holdings',
  'solutions', 'software', 'ltd', 'limited', 'plc', 'industries', 'services',
  'systems', 'digital', 'global', 'ventures', 'private', 'pvt', 'careers',
])

const TITLE_NOISE = new Set([
  'a', 'an', 'and', 'at', 'for', 'in', 'of', 'on', 'opportunities',
  'opportunity', 'program', 'students', 'student', 'the', 'to', 'university',
  'us', 'usa',
])

const TRACKING_PARAMS = new Set([
  'ats', 'fr', 'gh_src', 'lever-origin', 'lever-source', 'nl', 'ref', 'source',
  'src', 'trackingid', 'trk', 'utm_campaign', 'utm_content', 'utm_medium',
  'utm_source', 'utm_term',
])

const SEASON = /\b(spring|summer|fall|autumn|winter)\b/gi
const YEAR = /\b(20\d\d)\b/g
const STATE = /\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b/g

const SPECIALIZATIONS: Array<[string, RegExp]> = [
  ['ai', /\b(ai|artificial intelligence)\b/i],
  ['analytics', /\b(analytics?|analyst)\b/i],
  ['application', /\bapplication\b/i],
  ['backend', /\b(back[ -]?end|backend)\b/i],
  ['business', /\bbusiness\b/i],
  ['cloud', /\bcloud\b/i],
  ['data', /\bdata\b/i],
  ['devops', /\bdevops\b/i],
  ['digital', /\bdigital\b/i],
  ['embedded', /\bembedded\b/i],
  ['enterprise-systems', /\benterprise systems?\b/i],
  ['firmware', /\bfirmware\b/i],
  ['forward-deployed', /\b(forward deployed|fdse)\b/i],
  ['frontend', /\b(front[ -]?end|frontend)\b/i],
  ['fullstack', /\b(full[ -]?stack|fullstack)\b/i],
  ['hardware', /\bhardware\b/i],
  ['infrastructure', /\b(infrastructure|infra)\b/i],
  ['machine-learning', /\b(machine learning|ml)\b/i],
  ['mobile', /\bmobile\b/i],
  ['mlops', /\bmlops\b/i],
  ['operations', /\boperations?\b/i],
  ['platform', /\bplatform\b/i],
  ['product', /\bproduct\b/i],
  ['qa-test', /\b(automation|quality assurance|qa|test)\b/i],
  ['research', /\bresearch\b/i],
  ['security', /\b(cyber|security)\b/i],
  ['site-reliability', /\b(site reliability|sre)\b/i],
  ['software', /\bsoftware\b/i],
  ['strategy', /\bstrategy\b/i],
]

// Which status represents a duplicate group when its members disagree: highest
// rank wins. The outcome states all sit past `applied`, ordered by how far the
// application actually got. `rejected` ranks just above `applied` because it
// does mean somebody replied, but it is the least advanced way that can happen.
//
// Divergence inside a group should be rare -- setWholeGroupStatus writes every
// member at once -- so this only decides the tie when server rows for the same
// group arrive carrying different statuses.
const STATUS_RANK: Record<Status, number> = {
  new: 0,
  saved: 1,
  dismissed: 2,
  applied: 3,
  rejected: 4,
  heard_back: 5,
  interview: 6,
  offer: 7,
}

function plain(raw: string | null | undefined): string {
  return (raw ?? '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/&/g, ' and ')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .replace(/\s+/g, ' ')
}

function singular(token: string): string {
  if (token.length > 4 && token.endsWith('ies')) return `${token.slice(0, -3)}y`
  if (token.length > 4 && token.endsWith('s') && !token.endsWith('ss')) return token.slice(0, -1)
  return token
}

function words(raw: string | null | undefined): string[] {
  return plain(raw).split(' ').filter(Boolean).map(singular)
}

// Mirrors scraper/db.py's spirit, while also handling legal suffixes and the
// company-name variants seen in the live audit (Regions/Regions Bank,
// Grainger/W.W. Grainger, Sanmina/Sanmina-SCI).
function companyWords(raw: string | null | undefined): string[] {
  let toks = words((raw ?? '').replace(/\(yc.*?\)/gi, '').replace(/'s\b/gi, ''))
  const stripped = toks.filter((t) => !COMPANY_NOISE.has(t))
  toks = stripped.length ? stripped : toks
  return [...new Set(toks)]
}

function normCompany(raw: string | null | undefined): string {
  return companyWords(raw).join(' ')
}

function setContainment(a: Set<string>, b: Set<string>): number {
  if (!a.size || !b.size) return 0
  const [small, large] = a.size <= b.size ? [a, b] : [b, a]
  let hit = 0
  small.forEach((w) => { if (large.has(w)) hit++ })
  return hit / small.size
}

function setJaccard(a: Set<string>, b: Set<string>): number {
  if (!a.size || !b.size) return 0
  let hit = 0
  a.forEach((w) => { if (b.has(w)) hit++ })
  return hit / (a.size + b.size - hit)
}

function facets(title: string) {
  return {
    season: new Set((title.match(SEASON) ?? []).map((s) => s.toLowerCase() === 'autumn' ? 'fall' : s.toLowerCase())),
    year: new Set(title.match(YEAR) ?? []),
    state: new Set(title.toUpperCase().match(STATE) ?? []),
  }
}

/** A facet named by both titles must agree. A value present on only one side
 * is allowed because sources often shorten "Summer 2027" to just "Intern". */
function conflictingFacets(a: string, b: string): boolean {
  const fa = facets(a), fb = facets(b)
  for (const key of ['season', 'year', 'state'] as const) {
    const x = fa[key], y = fb[key]
    if (!x.size || !y.size) continue
    let shared = false
    x.forEach((v) => { if (y.has(v)) shared = true })
    if (!shared) return true
  }
  return false
}

function titleWords(job: Job): Set<string> {
  const company = new Set(companyWords(job.company))
  const location = new Set(words(job.location))
  const out = words(job.title)
    .filter((w) => !TITLE_NOISE.has(w))
    .filter((w) => !/^20\d\d$/.test(w))
    .filter((w) => !['spring', 'summer', 'fall', 'autumn', 'winter'].includes(w))
    .filter((w) => !company.has(w) && !location.has(w))
    .map((w) => w === 'engineering' ? 'engineer' : w)
    // "Internship" and "Intern" are the same word for this purpose, and the
    // difference is the whole reason two otherwise identical titles missed:
    // "Full-Stack Engineering Internship - Summer 2027" vs "... Intern - ..."
    // scored 0.75 containment against a 0.8 threshold. scraper/db.py's
    // norm_role has always stripped both; this is the display side catching up.
    .map((w) => w === 'internship' ? 'intern' : w)
  return new Set(out)
}

function specializations(title: string): Set<string> {
  return new Set(SPECIALIZATIONS.filter(([, re]) => re.test(title)).map(([name]) => name))
}

/** A specialization explicitly named by both sides must overlap. This blocks
 * the live false-positive families: AI vs software, TikTok Ads vs E-Commerce,
 * Vanguard Security vs Application Development, and SWE vs FDSE. */
function compatibleSpecializations(a: string, b: string): boolean {
  const sa = specializations(a), sb = specializations(b)
  if (!sa.size || !sb.size) return true
  const shared = [...sa].filter((x) => sb.has(x))
  if (!shared.length) return false

  // These labels distinguish parallel postings even when both titles also say
  // "software engineer". If both sides name one, they must name the same one.
  const strong = new Set([
    'backend', 'data', 'embedded', 'enterprise-systems', 'firmware',
    'forward-deployed', 'frontend', 'fullstack', 'infrastructure',
    'machine-learning', 'mobile', 'mlops', 'platform', 'product', 'qa-test',
    'security', 'site-reliability',
  ])
  const xa = [...sa].filter((x) => strong.has(x))
  const xb = [...sb].filter((x) => strong.has(x))
  return !xa.length || !xb.length || xa.some((x) => xb.includes(x))
}

/* Every source writes a place differently, and an exact string match on the
 * whole cell was rejecting pairs that name the SAME city. Live examples, all
 * of them one posting shown twice on the dashboard:
 *
 *   "SF"                          vs "San Francisco, CA"
 *   "NYC"                         vs "Hybrid - New York, NY"
 *   "Cary,North Carolina,United States" vs "Cary, NC"
 *   "Greater Amarillo Area"       vs "Amarillo, TX"
 *   "Las Vegas Metropolitan Area" vs "Las Vegas, NV"
 *   "Middletown, RIManassas, VA"  vs "Middletown, RI"
 *
 * So a location is parsed into places rather than compared as text. */
type Place = { city: string, state: string }

const STATE_CODE: Record<string, string> = {
  alabama: 'al', alaska: 'ak', arizona: 'az', arkansas: 'ar', california: 'ca',
  colorado: 'co', connecticut: 'ct', delaware: 'de', florida: 'fl', georgia: 'ga',
  hawaii: 'hi', idaho: 'id', illinois: 'il', indiana: 'in', iowa: 'ia',
  kansas: 'ks', kentucky: 'ky', louisiana: 'la', maine: 'me', maryland: 'md',
  massachusetts: 'ma', michigan: 'mi', minnesota: 'mn', mississippi: 'ms',
  missouri: 'mo', montana: 'mt', nebraska: 'ne', nevada: 'nv',
  'new hampshire': 'nh', 'new jersey': 'nj', 'new mexico': 'nm', 'new york': 'ny',
  'north carolina': 'nc', 'north dakota': 'nd', ohio: 'oh', oklahoma: 'ok',
  oregon: 'or', pennsylvania: 'pa', 'rhode island': 'ri', 'south carolina': 'sc',
  'south dakota': 'sd', tennessee: 'tn', texas: 'tx', utah: 'ut', vermont: 'vt',
  virginia: 'va', washington: 'wa', 'west virginia': 'wv', wisconsin: 'wi',
  wyoming: 'wy', 'district of columbia': 'dc',
}
const STATE_CODES = new Set(Object.values(STATE_CODE))

/* Only abbreviations that are unambiguous for a US job board. "la" is already
 * here as Los Angeles rather than Louisiana because the existing table chose
 * that, and the state code is stripped separately so the two never collide. */
const CITY_ALIAS: Record<string, string> = {
  sf: 'san francisco', sfo: 'san francisco', 'san fran': 'san francisco',
  nyc: 'new york', 'new york city': 'new york', la: 'los angeles',
  dc: 'washington', 'washington dc': 'washington', philly: 'philadelphia',
}

/* How a posting was arranged, not where it is. "Greater X Area" and "X
 * Metropolitan Area" are how LinkedIn writes a metro; the tracker writes the
 * bare city. Dropped outright — two "Remote" rows are not in the same place. */
const ARRANGEMENT = /\b(?:greater|metropolitan|metro|area|region|remote|hybrid|on-?site|onsite|multiple locations?|\d+\s*locations?)\b/g
/* A country is not a city, but it IS something two rows can share: Honeywell
 * posts "United States" and nothing else, and those rows must still meet. */
const COUNTRY_ONLY = /^(?:united states|usa?)$/
const TRAILING_COUNTRY = /\s+(?:united states|usa?)$/

function parsePlaces(raw: string | null | undefined): Place[] {
  let text = raw ?? ''
  if (!text.trim()) return []
  // "Middletown, RIManassas, VA" -- trackers join a list with no separator, so
  // a state code butted against a capital letter IS the separator.
  text = text.replace(/\b([A-Z]{2})(?=[A-Z][a-z])/g, '$1;')
  text = text.replace(/\+\s*\d+\s*$/, '')

  const out: Place[] = []
  for (const rawPart of text.split(/[;·|\/]|\s+-\s+/)) {
    const full = plain(rawPart)
    if (!full) continue
    // A bare abbreviation IS the city, and it has to be resolved BEFORE any
    // state logic: "LA" is also Louisiana's code, so the state pass would
    // consume it and leave no city at all.
    if (CITY_ALIAS[full]) { out.push({ city: CITY_ALIAS[full], state: '' }); continue }

    const part = full.replace(ARRANGEMENT, ' ').replace(/\s+/g, ' ').trim()
    if (!part) continue                                  // "Hybrid", "Remote"
    if (COUNTRY_ONLY.test(part)) { out.push({ city: part, state: '' }); continue }

    const tokens = part.replace(TRAILING_COUNTRY, '').trim().split(' ')
    // A trailing state, as a code or spelled out ("north carolina" is two
    // words, so the last TWO tokens are tried before the last one).
    let state = ''
    let cityTokens = tokens
    const last1 = tokens[tokens.length - 1]
    const last2 = tokens.slice(-2).join(' ')
    if (STATE_CODE[last2]) { state = STATE_CODE[last2]; cityTokens = tokens.slice(0, -2) }
    else if (STATE_CODE[last1]) { state = STATE_CODE[last1]; cityTokens = tokens.slice(0, -1) }
    else if (last1 && STATE_CODES.has(last1)) { state = last1; cityTokens = tokens.slice(0, -1) }
    let city = cityTokens.join(' ').trim()
    city = CITY_ALIAS[city] ?? city
    if (!city) continue
    out.push({ city, state })
  }
  return out
}

/** Two places are the same when the city matches and the states do not
 * contradict. An UNKNOWN state cannot contradict: "Greater Amarillo Area"
 * names no state and is still Amarillo, TX. Two KNOWN states that differ do
 * contradict, which is what keeps Columbus, OH apart from Columbus, GA. */
function samePlace(a: Place, b: Place): boolean {
  if (a.city !== b.city) return false
  return !a.state || !b.state || a.state === b.state
}

function normalizedLocations(raw: string | null | undefined): Set<string> {
  return new Set(parsePlaces(raw).map((p) => p.city))
}

function compatibleLocations(a: Job, b: Job): boolean {
  const la = parsePlaces(a.location), lb = parsePlaces(b.location)
  if (!la.length || !lb.length) return false
  if (la.some((x) => lb.some((y) => samePlace(x, y)))) return true

  // A compact location cell may say "+1" without storing the second city,
  // while Workday still includes that city in the application path. This is
  // how the Regions duplicate appears live (Hoover +1 vs Birmingham), and how
  // Graco's "French Lake, MN" meets "Dayton, MN" -- one Workday requisition
  // whose URL spells out both.
  //
  // A city that is ALSO the employer's name proves nothing here, because the
  // company name is in every one of that employer's URLs. Without this filter
  // "Corning, NY" matched "Concord, NC" and "Hartford, CT" matched
  // "Charlotte, NC" -- two different postings each, merged on the company name
  // sitting in the path.
  const employer = new Set([...companyWords(a.company), ...companyWords(b.company)])
  const usable = (p: Place) => !p.city.split(' ').every((w) => employer.has(w))
  const ac = targetCorpus(a), bc = targetCorpus(b)
  return la.filter(usable).some((x) => bc.includes(x.city))
    || lb.filter(usable).some((x) => ac.includes(x.city))
}

function applicationHref(job: Job): string {
  return job.is_easy_apply ? job.url : (job.apply_url ?? job.url)
}

function workdayTenant(host: string): string {
  return host.split('.')[0].replace(/[^a-z0-9]/g, '')
}

function normalizedWorkdayReq(raw: string): string {
  const compact = raw.toLowerCase().replace(/^jr-/, 'jr').replace(/^r-/, 'r')
  return compact.replace(/^((?:jr|r)\d{4,})-\d+$/, '$1')
}

/** Stable identity for an application target. Tracking params, locale path
 * variants, and Workday's generated "-1" suffix do not create new jobs. */
export function canonicalTargetKey(job: Job): string {
  const raw = applicationHref(job)
  if (!raw?.trim()) return `row:${job.id}`
  try {
    const url = new URL(raw)
    const host = url.hostname.toLowerCase().replace(/^www\./, '')
    const decodedPath = decodeURIComponent(url.pathname).replace(/\/+$/, '') || '/'

    const linkedInId = /\/jobs\/view\/(?:.*?-)?(\d{7,})(?:\/|$)/i.exec(decodedPath)?.[1]
    if (host.endsWith('linkedin.com') && linkedInId) return `linkedin:${linkedInId}`

    if (/\.myworkdayjobs\.com$/.test(host)) {
      const req = /_((?:JR|R)[-_]?[A-Z0-9-]+)$/i.exec(decodedPath)?.[1]
      if (req) return `workday:${workdayTenant(host)}:${normalizedWorkdayReq(req)}`
    }

    if (host.includes('greenhouse.io')) {
      const pathMatch = /\/([^/]+)\/jobs\/(\d+)/i.exec(decodedPath)
      const queryId = url.searchParams.get('gh_jid')
      if (pathMatch) return `greenhouse:${pathMatch[1].toLowerCase()}:${pathMatch[2]}`
      if (queryId) return `greenhouse:${host}:${queryId}`
    }

    const leverId = /\/([0-9a-f]{8}-[0-9a-f-]{27,})(?:\/apply)?$/i.exec(decodedPath)?.[1]
    if (host.endsWith('lever.co') && leverId) return `lever:${leverId.toLowerCase()}`

    const ashby = /^\/([^/]+)\/([0-9a-f]{8}-[0-9a-f-]{27,})$/i.exec(decodedPath)
    if (host.endsWith('ashbyhq.com') && ashby) return `ashby:${ashby[1].toLowerCase()}:${ashby[2].toLowerCase()}`

    const jobvite = /\/job\/([a-z0-9_-]+)/i.exec(decodedPath)?.[1]
    if (host.includes('jobvite.com') && jobvite) return `jobvite:${jobvite.toLowerCase()}`

    const smartRecruiters = /\/([0-9]{10,})(?:\/|$)/.exec(decodedPath)?.[1]
    if (host.endsWith('smartrecruiters.com') && smartRecruiters) return `smartrecruiters:${smartRecruiters}`

    const icims = /\/jobs\/(\d+)(?:\/|$)/i.exec(decodedPath)?.[1]
    if (host.includes('icims.com') && icims) return `icims:${host.split('.')[0]}:${icims}`

    const params = [...url.searchParams.entries()]
      .filter(([key]) => !TRACKING_PARAMS.has(key.toLowerCase()) && !key.toLowerCase().startsWith('utm_'))
      .sort(([ak, av], [bk, bv]) => ak.localeCompare(bk) || av.localeCompare(bv))
    const query = new URLSearchParams(params).toString()
    return `url:${host}${decodedPath.toLowerCase()}${query ? `?${query}` : ''}`
  } catch {
    // A malformed URL is not evidence that two rows are the same. Keep it
    // unique and let the guarded metadata phase decide instead.
    return `row:${job.id}`
  }
}

function sourceKind(job: Job): 'linkedin' | 'external' {
  try {
    return new URL(applicationHref(job)).hostname.toLowerCase().endsWith('linkedin.com')
      ? 'linkedin' : 'external'
  } catch {
    return 'external'
  }
}

function targetCorpus(job: Job): string {
  let target = ''
  try {
    const url = new URL(applicationHref(job))
    target = `${url.hostname} ${decodeURIComponent(url.pathname)}`
  } catch { /* malformed source URL: return an empty corpus */ }
  return words(target).join(' ')
}

function companyCorpus(job: Job): string {
  return words(`${job.company} ${targetCorpus(job)}`).join(' ')
}

function compatibleCompanies(a: Job, b: Job): boolean {
  const aw = new Set(companyWords(a.company)), bw = new Set(companyWords(b.company))
  if (!aw.size || !bw.size) return false
  if (normCompany(a.company) === normCompany(b.company)) return true
  if (setContainment(aw, bw) === 1) return true

  // A parent brand is sometimes present only in the ATS path. Live example:
  // "WEC Energy Group" links through /We_Energies/, while LinkedIn calls the
  // employer "We Energies". Requiring every company token in the URL corpus
  // avoids treating unrelated companies with one shared word as aliases.
  const ac = new Set(companyCorpus(a).split(' ').filter(Boolean))
  const bc = new Set(companyCorpus(b).split(' ').filter(Boolean))
  return [...aw].every((w) => bc.has(w)) || [...bw].every((w) => ac.has(w))
}

function companyBucketKeys(job: Job): string[] {
  const company = companyWords(job.company)
  return [...new Set([
    `full:${company.join(' ')}`,
    ...company.filter((w) => w.length >= 3).map((w) => `word:${w}`),
  ])]
}

function guardedMetadataMatch(a: Job, b: Job): boolean {
  if (!compatibleCompanies(a, b)) return false
  if (!compatibleLocations(a, b)) return false
  if (conflictingFacets(a.title, b.title)) return false
  if (!compatibleSpecializations(a.title, b.title)) return false

  const ta = titleWords(a), tb = titleWords(b)
  if (!ta.size || !tb.size) return false
  const sameSignature = ta.size === tb.size && setContainment(ta, tb) === 1

  // Within one source, similar titles commonly represent parallel teams or
  // tracks. Only an identical compact signature is safe there. Across sources,
  // punctuation and boilerplate differ, so high containment plus reasonable
  // symmetric overlap is accepted.
  if (sourceKind(a) === sourceKind(b)) return sameSignature
  return sameSignature || (setContainment(ta, tb) >= 0.8 && setJaccard(ta, tb) >= 0.55)
}

class DisjointSet {
  private parent: number[]

  constructor(size: number) {
    this.parent = Array.from({ length: size }, (_, i) => i)
  }

  find(i: number): number {
    let root = i
    while (this.parent[root] !== root) root = this.parent[root]
    while (this.parent[i] !== i) {
      const next = this.parent[i]
      this.parent[i] = root
      i = next
    }
    return root
  }

  union(a: number, b: number) {
    const ra = this.find(a), rb = this.find(b)
    if (ra === rb) return
    // The earliest row leads, preserving the incoming found_at order.
    if (ra < rb) this.parent[rb] = ra
    else this.parent[ra] = rb
  }
}

export type Grouped = Job & { duplicates?: Job[] }

function effectiveStatus(members: Job[]): Status {
  return members.reduce<Status>((best, job) =>
    STATUS_RANK[job.status ?? 'new'] > STATUS_RANK[best] ? (job.status ?? 'new') : best,
  'new')
}

/* The leader is simply the first member, which is the newest by found_at — and
 * newest is not best. Sources disagree about the SAME posting on exactly the
 * fields the row displays: measured live, 61 groups differ on salary, 83 on
 * title, 98 on location and 32 on tier. Showing whichever arrived last means
 * the row can lose a stated salary, or show "SF" when its twin says "San
 * Francisco, CA", for no reason the reader can see.
 *
 * So each displayed field is taken from whichever member has the best value
 * for it, the same way status already is. Every source row stays reachable in
 * `duplicates`, so nothing here hides anything — it only decides what the one
 * visible line says. */

/** A location is better when it names a state: "San Francisco, CA" over "SF",
 * "Cary, NC" over "Cary,North Carolina,United States" once both parse. Among
 * equals the shorter string wins, because the long ones are concatenated lists
 * ("Middletown, RIManassas, VA") rather than extra information. */
function betterLocation(a: string, b: string): string {
  if (!a?.trim()) return b
  if (!b?.trim()) return a
  const sa = parsePlaces(a).some((p) => p.state) ? 1 : 0
  const sb = parsePlaces(b).some((p) => p.state) ? 1 : 0
  if (sa !== sb) return sa > sb ? a : b
  return a.length <= b.length ? a : b
}

function mergeDisplayFields(members: Job[]): Partial<Job> {
  let location = members[0].location
  let salary = members[0].salary
  let tier = members[0].tier
  let suggested = members[0].suggested_resume
  // An apply route that is not Easy Apply reaches a human, so it wins outright.
  let best = members[0]
  for (const m of members.slice(1)) {
    location = betterLocation(location, m.location)
    // A stated figure beats a blank one. Between two figures keep the leader's:
    // they are the same pay written twice, and salary parsing already handles
    // the wording differences.
    if (!salary?.trim() && m.salary?.trim()) salary = m.salary
    if (tier !== 'APPLY' && m.tier === 'APPLY') tier = m.tier
    if (!suggested && m.suggested_resume) suggested = m.suggested_resume
    if (best.is_easy_apply && !m.is_easy_apply) best = m
  }
  return {
    location, salary, tier, suggested_resume: suggested,
    is_easy_apply: best.is_easy_apply, apply_url: best.apply_url, url: best.url,
  }
}

/** Returns one entry per group. Every source row remains attached and visible
 * in the drawer. The most advanced status wins so an already-applied copy can
 * never reappear in To apply merely because a newer duplicate was inserted. */
export function groupNearDuplicates(jobs: Job[]): Grouped[] {
  if (jobs.length < 2) return jobs

  const sets = new DisjointSet(jobs.length)

  // Phase 1: definitive application-target matches.
  const byTarget = new Map<string, number>()
  jobs.forEach((job, i) => {
    const key = canonicalTargetKey(job)
    const prior = byTarget.get(key)
    if (prior === undefined) byTarget.set(key, i)
    else if (!key.startsWith('url:') || guardedMetadataMatch(jobs[prior], job)) {
      // Recognised ATS requisitions are definitive. A generic exact URL still
      // passes the metadata guard because some employers point every posting
      // at the same careers homepage.
      sets.union(prior, i)
    }
  })

  // Phase 2: conservative metadata matches inside company-token buckets.
  // Pair ids prevent the same pair being checked once per shared token.
  const buckets = new Map<string, number[]>()
  jobs.forEach((job, i) => {
    for (const key of companyBucketKeys(job)) {
      const bucket = buckets.get(key)
      if (bucket) bucket.push(i)
      else buckets.set(key, [i])
    }
  })
  const checked = new Set<string>()
  buckets.forEach((idxs) => {
    // Huge brands have many genuinely parallel roles. Exact targets still
    // grouped in phase 1; skip fuzzy matching rather than quadratic guessing.
    if (idxs.length > 60) return
    for (let a = 0; a < idxs.length; a++) {
      for (let b = a + 1; b < idxs.length; b++) {
        const i = idxs[a], j = idxs[b]
        const pair = i < j ? `${i}:${j}` : `${j}:${i}`
        if (checked.has(pair)) continue
        checked.add(pair)
        if (guardedMetadataMatch(jobs[i], jobs[j])) sets.union(i, j)
      }
    }
  })

  const groups = new Map<number, Job[]>()
  jobs.forEach((job, i) => {
    const root = sets.find(i)
    const members = groups.get(root)
    if (members) members.push(job)
    else groups.set(root, [job])
  })

  return [...groups.entries()]
    .sort(([a], [b]) => a - b)
    .map(([, members]) => {
      if (members.length === 1) return members[0]
      const [leader, ...duplicates] = members
      return {
        ...leader,
        ...mergeDisplayFields(members),
        status: effectiveStatus(members),
        duplicates,
      }
    })
}

/** Merge a tiny live-update batch back into the already-grouped dashboard.
 *
 * Existing groups are flattened first so a new LinkedIn row can group with an
 * ATS/tracker row that is currently tucked inside ``duplicates``. For the same
 * id, the more advanced local status wins: an in-flight delta response that
 * still says ``new`` must never undo an optimistic Applied click.
 */
export function mergeGroupedJobs(current: Grouped[], incoming: Job[]): Grouped[] {
  if (!incoming.length) return current

  const byId = new Map<string, Job>()
  for (const group of current) {
    const { duplicates = [], ...leader } = group
    byId.set(leader.id, leader as Job)
    for (const job of duplicates) byId.set(job.id, job)
  }

  for (const job of incoming) {
    const existing = byId.get(job.id)
    if (!existing) {
      byId.set(job.id, job)
      continue
    }
    const oldStatus = existing.status ?? 'new'
    const newStatus = job.status ?? 'new'
    byId.set(job.id, {
      ...existing,
      ...job,
      status: STATUS_RANK[oldStatus] >= STATUS_RANK[newStatus] ? oldStatus : newStatus,
    })
  }

  return groupNearDuplicates(
    [...byId.values()].sort(
      (a, b) => new Date(b.found_at).getTime() - new Date(a.found_at).getTime(),
    ),
  )
}
