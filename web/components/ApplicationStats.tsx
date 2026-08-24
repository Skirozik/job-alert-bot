'use client'

import { useMemo } from 'react'
import type { Job } from '@/types/job'
import { APPLIED_OR_LATER, GOT_A_REPLY } from '@/types/job'

/* 560 applications had been sent with nothing recorded about what came back, so
   the questions that actually decide where the next hundred go -- does applying
   direct-to-ATS beat LinkedIn Easy Apply, does the AI resume convert better
   than General -- could not be answered at all.

   Everything here derives from rows the page already holds. No extra query, no
   extra egress; this dashboard hit 192% of its Supabase quota once and is not
   going back. */

/** Where the application was actually submitted. Derived from the apply URL,
 *  because there is no platform column and adding one would mean a migration
 *  plus a backfill of 63k rows to answer a question this answers for free. */
function platform(j: Job): string {
  const u = (j.apply_url || j.url || '').toLowerCase()
  const known: [string, string][] = [
    ['greenhouse', 'Greenhouse'], ['lever.co', 'Lever'], ['ashbyhq', 'Ashby'],
    ['myworkdayjobs', 'Workday'], ['icims', 'iCIMS'], ['smartrecruiters', 'SmartRecruiters'],
    ['oraclecloud', 'Oracle'], ['avature', 'Avature'], ['successfactors', 'SuccessFactors'],
  ]
  for (const [needle, name] of known) if (u.includes(needle)) return name
  if (u.includes('linkedin.com')) return 'LinkedIn'
  return 'Other'
}

type Row = { key: string; sent: number; replies: number }

function tally(jobs: Job[], keyOf: (j: Job) => string): Row[] {
  const m = new Map<string, Row>()
  for (const j of jobs) {
    const k = keyOf(j)
    const row = m.get(k) ?? { key: k, sent: 0, replies: 0 }
    row.sent += 1
    if (GOT_A_REPLY.includes(j.status)) row.replies += 1
    m.set(k, row)
  }
  return [...m.values()].sort((a, b) => b.sent - a.sent)
}

function Group({ title, rows, total }: { title: string; rows: Row[]; total: number }) {
  return (
    <div style={{ minWidth: 0, flex: '1 1 250px' }}>
      <div style={{ fontSize: 'var(--text-meta)', letterSpacing: '0.06em', textTransform: 'uppercase',
                    color: 'var(--fg-subtle)', marginBottom: 'var(--s2)' }}>
        {title}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--s1)' }}>
        {rows.slice(0, 5).map((r) => {
          const pct = r.sent ? Math.round((r.replies / r.sent) * 100) : 0
          return (
            <div key={r.key} style={{ display: 'flex', alignItems: 'center', gap: 'var(--s2)',
                                      fontSize: 'var(--text-data)' }}>
              <span className="truncate" style={{ flex: '0 0 108px', color: 'var(--fg-muted)' }}>{r.key}</span>
              {/* Bar length is share of volume; the number after it is the reply
                  rate. Two different quantities, so only one of them is a bar. */}
              <span style={{ flex: 1, height: 4, background: 'var(--bg-active)', borderRadius: 2,
                             overflow: 'hidden', minWidth: 32 }}>
                <span style={{ display: 'block', height: '100%', borderRadius: 2,
                               width: (total ? (r.sent / total) * 100 : 0) + '%',
                               background: 'var(--fg-subtle)' }} />
              </span>
              <span style={{ flex: '0 0 88px', textAlign: 'right', color: 'var(--fg-subtle)',
                             fontVariantNumeric: 'tabular-nums' }}>
                {r.sent} sent{r.replies > 0 ? ' · ' + pct + '%' : ''}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function Headline({ n, label }: { n: number; label: string }) {
  return (
    <span style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--s2)' }}>
      <span style={{ fontSize: '19px', fontWeight: 650, letterSpacing: '-0.02em', color: 'var(--fg)',
                     fontVariantNumeric: 'tabular-nums' }}>{n}</span>
      <span style={{ fontSize: 'var(--text-data)', color: 'var(--fg-muted)' }}>{label}</span>
    </span>
  )
}

export function ApplicationStats({ jobs }: { jobs: Job[] }) {
  const stats = useMemo(() => {
    const sent = jobs.filter((j) => APPLIED_OR_LATER.includes(j.status))
    return {
      sent,
      replies: sent.filter((j) => GOT_A_REPLY.includes(j.status)).length,
      interviews: sent.filter((j) => j.status === 'interview' || j.status === 'offer').length,
      offers: sent.filter((j) => j.status === 'offer').length,
      bySource: tally(sent, platform),
      byResume: tally(sent, (j) => j.suggested_resume || 'General'),
    }
  }, [jobs])

  if (!stats.sent.length) return null
  const rate = Math.round((stats.replies / stats.sent.length) * 100)

  return (
    <div style={{ padding: 'var(--s4)', borderBottom: '1px solid var(--border)',
                  background: 'var(--bg-surface)' }}>
      <div style={{ display: 'flex', gap: 'var(--s5)', flexWrap: 'wrap', alignItems: 'baseline',
                    marginBottom: 'var(--s4)' }}>
        <Headline n={stats.sent.length} label="applied" />
        <Headline n={stats.replies} label={'replies · ' + rate + '%'} />
        <Headline n={stats.interviews} label="interviews" />
        <Headline n={stats.offers} label="offers" />
        {stats.replies === 0 && (
          <span style={{ fontSize: 'var(--text-meta)', color: 'var(--fg-subtle)' }}>
            Record what came back in a job&rsquo;s panel and these fill in.
          </span>
        )}
      </div>
      <div style={{ display: 'flex', gap: 'var(--s5)', flexWrap: 'wrap' }}>
        <Group title="By where you applied" rows={stats.bySource} total={stats.sent.length} />
        <Group title="By resume used" rows={stats.byResume} total={stats.sent.length} />
      </div>
    </div>
  )
}
