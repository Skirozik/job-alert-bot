export type Tier = 'APPLY' | 'MAYBE' | 'SKIP'
export type Status = 'new' | 'saved' | 'applied' | 'dismissed'
export type SuggestedResume = 'Mobile' | 'AI' | 'Frontend' | '1Password' | 'General' | 'N/A'

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
