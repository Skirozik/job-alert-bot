export type Tier = 'APPLY' | 'MAYBE' | 'SKIP'
export type Status = 'new' | 'saved' | 'applied' | 'dismissed'

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
  suggested_resume: string
  status: Status
  posted_at: string | null
  found_at: string
  logo_url: string | null
}
