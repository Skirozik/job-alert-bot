import type { Job } from '@/types/job'
import boards from './greenhouse_boards.json'

/* Greenhouse's own application form for a posting whose link goes to the
 * company's careers site, or null.
 *
 * Some Greenhouse boards (Airbnb, Stripe, Databricks, ...) hand out a link on
 * the company's own site carrying ?gh_jid=<Greenhouse id>. The company's site
 * builds those pages on its own schedule, so a posting found within a minute of
 * listing can still 404 there. Greenhouse's embedded form is live as soon as
 * the board lists the posting, so the dashboard offers it as a second link.
 *
 * Mirrors scraper/notifier.py greenhouse_form_url. Both run every case in
 * fixtures/greenhouse_form_links.json, and the board tokens come from
 * greenhouse_boards.json, which scraper/test_greenhouse_boards_json.py keeps
 * equal to the Greenhouse entries of scraper/ats_config.py. */

const BOARDS: Record<string, string> = boards
const GH_JID = /[?&]gh_jid=(\d+)/

function hostOf(link: string): string {
  try { return new URL(link).hostname } catch { return '' }
}

export function greenhouseFormUrl(j: Pick<Job, 'company' | 'url' | 'apply_url'>): string | null {
  const token = BOARDS[j.company ?? '']
  if (!token) return null
  for (const link of [j.url, j.apply_url]) {
    const m = GH_JID.exec(link ?? '')
    if (m && !hostOf(link ?? '').endsWith('greenhouse.io')) {
      return `https://job-boards.greenhouse.io/embed/job_app?for=${token}&token=${m[1]}`
    }
  }
  return null
}
