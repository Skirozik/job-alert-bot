// All three personas migrated to this vocabulary together. The old
// APPLY/MAYBE/SKIP scheme is gone: MAYBE was never looked at, so it functioned
// as a silent delete, and SKIP hid judgment calls that should have been the
// candidate's to make.
//   APPLY        - clean fit
//   APPLY_CAVEAT - worth applying, one specific reservation; `reason` IS that
//                  caveat, under 12 words. Shown in the SAME list as APPLY.
//   INELIGIBLE   - hard block only. The only tier that is ever hidden.
export type Tier = 'APPLY' | 'APPLY_CAVEAT' | 'INELIGIBLE'

/** Shown in the main list — never hidden or collapsed. */
export const ACTIONABLE_TIERS: Tier[] = ['APPLY', 'APPLY_CAVEAT']
// The application lifecycle is ORDERED, and a later state implies every
// earlier one. Outcomes live in `status` rather than a second column because
// "how many did I apply to" is then "applied or anything after it" -- the 560
// rows already sitting at 'applied' stay correct and mean "sent, no reply yet".
export type Status =
  | 'new' | 'saved' | 'dismissed'
  | 'applied'                                   // sent, nothing heard back
  | 'heard_back' | 'interview' | 'offer'        // it progressed
  | 'rejected'                                  // it ended

/** Every state meaning the application was actually sent. Count these, not
 *  `status === 'applied'` alone, or moving a job to 'interview' silently
 *  decrements the applied total. */
export const APPLIED_OR_LATER: Status[] = ['applied', 'heard_back', 'interview', 'offer', 'rejected']

/** Sent AND resolved either way -- the numerator for a response rate. */
export const GOT_A_REPLY: Status[] = ['heard_back', 'interview', 'offer', 'rejected']

// '1Password' and 'N/A' used to be members here. They are not resume variants --
// they were junk the model returned (39 rows of "N/A", one echoing the company
// name), absorbed into the type instead of fixed, so the type documented the
// bug. classifier.py now coerces anything outside this set to 'General'.
export type SuggestedResume = 'Mobile' | 'AI' | 'Frontend' | 'General'

export interface Job {
  id: string
  title: string
  company: string
  location: string
  url: string
  search_term: string
  description: string | null
  norm_key: string
  tier: Tier
  reason: string
  // Optional on purpose: only the original persona's schema has this column.
  // scraper_beyonce/schema.sql and scraper_hassan/schema.sql omit it (those
  // candidates each have one resume). Every server query uses `select=*`, so
  // rows from those projects simply arrive without the key and the guard in
  // JobCard.tsx renders no badge.
  //
  // RULE: any column not present in ALL personas' schema.sql must be optional
  // here, or the drift stops degrading safely.
  suggested_resume?: SuggestedResume | null
  status: Status
  posted_at: string | null
  found_at: string
  logo_url: string | null
  apply_url: string | null
  is_easy_apply: boolean
  salary: string | null
}
