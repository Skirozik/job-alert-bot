/* Cross-source identity for a status write.
 *
 * WHY THIS EXISTS. One posting reaches the table through three doors and each
 * hands it a different primary key — LinkedIn a bare numeric id, the ATS
 * watcher "ats:" + sha1(url), the GitHub trackers "gh:" + sha1(apply_url).
 * Marking one row applied left its twins at status 'new', so they drifted back
 * into To apply and every downstream consumer treated them as fresh. Measured
 * on the live table: 60 rows in To apply were twins of jobs already resolved,
 * 18 of them gold-starred.
 *
 * WHY NOT dupes.ts. That module already groups near-duplicates, and the status
 * route already sends the whole group. But its grouping is FUZZY and built for
 * display — it clusters what looks alike on screen. The rows that leaked were
 * precisely the ones it did not cluster: Cloudflare's pair
 * ("Software Engineer Intern (Fall 2026) - Austin, TX" vs "… - Fall 2026 -
 * Austin - TX") reads as two jobs and is provably one requisition. Identity
 * here is decided by target_key and norm_key, the same pair scraper/db.py and
 * the resume builder use, so all three agree on what "the same job" means.
 *
 * WHY SERVER-SIDE. The browser only holds the rows it fetched, and neither
 * target_key nor norm_key is in COLS_BASE. A twin on another page — or one
 * that arrived after the page loaded — is invisible to the client but not to
 * a query. Expanding here also means every caller gets it, not just the ones
 * that remember to send a group.
 *
 * Kept free of value imports on purpose: statusMutations.test.mjs transpiles
 * these modules into a data: URL, which cannot resolve a relative import.
 */

export type SiblingRow = {
  id: string
  target_key?: string | null
  norm_key?: string | null
}

/* Real groups are small — the largest norm_key group in 83,260 rows is 10, and
 * nothing exceeds 20. This ceiling is a runaway guard, not a working limit; a
 * write that hits it is a bug worth seeing in the log rather than a silent
 * truncation. */
export const SIBLING_CAP = 200

/** The identities a set of rows can be joined on.
 *
 * target_key is dropped when NULL, and that is the load-bearing line. 13% of
 * rows have none — target_key.py returns null for any URL it cannot positively
 * identify — so treating NULL as a value would make every unidentifiable row a
 * sibling of every other and one click would resolve thousands.
 *
 * norm_key needs no such guard: it is populated on 100% of rows, and
 * db.norm_role deliberately keeps season, year and location, so Notion's
 * Summer 2027 and Winter 2027 postings stay two distinct keys.
 */
export function siblingIdentities(rows: SiblingRow[]): { targetKeys: string[], normKeys: string[] } {
  const targetKeys = new Set<string>()
  const normKeys = new Set<string>()
  for (const row of rows) {
    const target = (row?.target_key ?? '').trim()
    if (target) targetKeys.add(target)
    const norm = (row?.norm_key ?? '').trim()
    if (norm) normKeys.add(norm)
  }
  return { targetKeys: [...targetKeys], normKeys: [...normKeys] }
}

/** Quote values for a PostgREST `in.(...)` filter.
 *
 * norm_key holds spaces and a literal "|" ("stripe|software engineer summer or
 * winter"), and an unquoted list would split on the wrong characters. Quoting
 * plus escaping is what keeps a company named `Say "Hello"` from ending the
 * literal early. */
export function pgList(values: string[]): string {
  const quoted = values.map((v) => `"${v.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`)
  return `(${quoted.join(',')})`
}

/** Seed ids plus every sibling found, deduped and order-stable.
 *
 * The seeds come first and are never dropped by the cap: the row the user
 * actually clicked must be written even if its family is somehow enormous. */
export function mergeSiblingIds(
  seed: string[],
  found: { id: string }[],
  cap: number = SIBLING_CAP,
): { ids: string[], added: number, truncated: number } {
  const out: string[] = []
  const seen = new Set<string>()
  for (const id of seed) {
    if (id && !seen.has(id)) { seen.add(id); out.push(id) }
  }
  const before = out.length
  let truncated = 0
  for (const row of found) {
    const id = row?.id
    if (!id || seen.has(id)) continue
    if (out.length >= cap) { truncated++; continue }
    seen.add(id)
    out.push(id)
  }
  return { ids: out, added: out.length - before, truncated }
}
