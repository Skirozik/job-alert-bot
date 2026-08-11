// Near-duplicate detection for the dashboard.
//
// The same posting reaches us from up to three sources (LinkedIn, the GitHub
// tracker, the direct-ATS watcher) with different title wording, so the
// scrapers' exact norm_key dedup cannot catch it. Real example:
//
//   LinkedIn : "Data Lake Infrastructure & Data Analytics Research Engineer
//               Intern (AML-Ark-US) - 2027 Summer"
//   GitHub   : "Data Lake Infrastructure and Data Analytics Research Engineer
//               Intern - Applied Machine Learning Ark"
//
// Same job, same company, same $45/hr — but only 0.47 token overlap in the
// titles, because one spells out an acronym the other abbreviates. Their
// DESCRIPTIONS are 91% similar, which is the signal that identifies them.
//
// IMPORTANT — this GROUPS, it never HIDES.
//
// Auto-merging was backtested against ~40,000 stored jobs and rejected. Many
// employers reuse one boilerplate description across every posting, so
// description similarity hits 1.000 for genuinely different roles:
//
//   vanguard : "...Data Science - NC"        vs "...Data Science - PA"
//   todd     : "SWE Intern, Fullstack (Winter)" vs "(Summer)" vs "(Fall)"
//   svig     : "Unity Engine Design Intern"  vs "Spring Framework Java Intern"
//   palantir : "Software Engineer, Internship" vs "Forward Deployed Software
//               Engineer, Internship - USG"
//
// Guards for season / year / state / level / location / title-overlap removed
// most of those but never all — Palantir survived every variant, because a
// short title is always "contained" in a longer one. Since a false merge
// permanently hides a real job and a duplicate merely costs one extra card,
// grouping is the correct trade. Both postings stay reachable.

import type { Job } from '@/types/job'

const SHINGLE = 5
const DESC_THRESHOLD = 0.97
const TITLE_THRESHOLD = 0.6

const COMPANY_NOISE = new Set([
  'inc', 'llc', 'corp', 'co', 'company', 'international', 'electronics',
  'financial', 'technologies', 'technology', 'labs', 'group', 'holdings',
  'solutions', 'software', 'ltd', 'plc', 'industries', 'services', 'systems',
  'digital', 'global', 'ventures',
])

// Mirrors scraper/db.py norm_company so grouping agrees with the pipeline.
function normCompany(raw: string | null | undefined): string {
  const c = (raw ?? '').toLowerCase().replace(/\(yc.*?\)/g, '').replace(/'s\b/g, '').replace(/[^a-z0-9 ]/g, ' ')
  let toks = c.split(/\s+/).filter(Boolean)
  if (toks[0] === 'the') toks = toks.slice(1)
  const stripped = toks.filter((t) => !COMPANY_NOISE.has(t))
  return (stripped.length ? stripped : toks).join(' ')
}

function words(text: string | null | undefined): string[] {
  return (text ?? '').toLowerCase().match(/[a-z0-9]+/g) ?? []
}

function shingles(text: string | null | undefined): Set<string> {
  const w = words(text)
  const out = new Set<string>()
  for (let i = 0; i + SHINGLE <= w.length; i++) out.add(w.slice(i, i + SHINGLE).join(' '))
  return out
}

/** intersection / smaller set — robust when one posting is a longer variant. */
function containment(a: Set<string>, b: Set<string>): number {
  if (!a.size || !b.size) return 0
  let hit = 0
  const [small, large] = a.size <= b.size ? [a, b] : [b, a]
  small.forEach((s) => { if (large.has(s)) hit++ })
  return hit / small.size
}

const SEASON = /\b(spring|summer|fall|autumn|winter)\b/gi
const YEAR = /\b(20\d\d)\b/g
const STATE = /\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b/g

function facets(title: string) {
  return {
    season: new Set((title.match(SEASON) ?? []).map((s) => s.toLowerCase())),
    year: new Set(title.match(YEAR) ?? []),
    state: new Set(title.toUpperCase().match(STATE) ?? []),
  }
}

/** Two titles conflict when both name a value on the same axis and the values
 *  differ. A facet present in only one title is not a conflict — "… Intern"
 *  and "… Intern - Summer 2027" are normally one posting. */
function conflicting(a: string, b: string): boolean {
  const fa = facets(a), fb = facets(b)
  for (const k of ['season', 'year', 'state'] as const) {
    const x = fa[k], y = fb[k]
    if (x.size && y.size) {
      let shared = false
      x.forEach((v) => { if (y.has(v)) shared = true })
      if (!shared) return true
    }
  }
  return false
}

function titleOverlap(a: string, b: string): number {
  const wa = new Set(words(a)), wb = new Set(words(b))
  if (!wa.size || !wb.size) return 0
  let hit = 0
  const [small, large] = wa.size <= wb.size ? [wa, wb] : [wb, wa]
  small.forEach((w) => { if (large.has(w)) hit++ })
  return hit / small.size
}

export type Grouped = Job & { duplicates?: Job[] }

/** Returns one entry per group. The first-seen job leads; near-identical
 *  siblings hang off `duplicates` so the UI can show them without a
 *  second card. Nothing is discarded. */
export function groupNearDuplicates(jobs: Job[]): Grouped[] {
  const byCompany = new Map<string, number[]>()
  jobs.forEach((j, i) => {
    const key = normCompany(j.company)
    if (!key) return
    const arr = byCompany.get(key)
    if (arr) arr.push(i); else byCompany.set(key, [i])
  })

  const leaderOf = new Map<number, number>()
  const shingleCache = new Map<number, Set<string>>()
  const sh = (i: number) => {
    let s = shingleCache.get(i)
    if (!s) { s = shingles(jobs[i].description); shingleCache.set(i, s) }
    return s
  }

  byCompany.forEach((idxs) => {
    if (idxs.length < 2) return
    // Companies posting at huge volume are exactly the boilerplate-description
    // case; skip them rather than risk grouping distinct roles.
    if (idxs.length > 60) return
    for (let a = 0; a < idxs.length; a++) {
      for (let b = a + 1; b < idxs.length; b++) {
        const i = idxs[a], j = idxs[b]
        if (leaderOf.has(j)) continue
        const ji = jobs[i], jj = jobs[j]
        if (!ji.description || !jj.description) continue
        if ((ji.location ?? '').trim().toLowerCase() !== (jj.location ?? '').trim().toLowerCase()) continue
        if (conflicting(ji.title ?? '', jj.title ?? '')) continue
        if (titleOverlap(ji.title ?? '', jj.title ?? '') < TITLE_THRESHOLD) continue
        if (containment(sh(i), sh(j)) < DESC_THRESHOLD) continue
        leaderOf.set(j, leaderOf.get(i) ?? i)
      }
    }
  })

  const out: Grouped[] = []
  const attached = new Map<number, Job[]>()
  leaderOf.forEach((lead, follower) => {
    const arr = attached.get(lead)
    if (arr) arr.push(jobs[follower]); else attached.set(lead, [jobs[follower]])
  })
  jobs.forEach((j, i) => {
    if (leaderOf.has(i)) return
    const dupes = attached.get(i)
    out.push(dupes?.length ? { ...j, duplicates: dupes } : j)
  })
  return out
}
