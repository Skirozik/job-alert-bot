'use client'

import { useState } from 'react'
import Image from 'next/image'
import type { Job, Status } from '@/types/job'

const TIER_BADGE: Record<string, string> = {
  APPLY: 'bg-green-500/20 text-green-400 border border-green-500/30',
  MAYBE: 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30',
  SKIP: 'bg-gray-700/50 text-gray-500 border border-gray-700',
}

const STATUS_BADGE: Record<string, string> = {
  new: 'bg-gray-800 text-gray-400',
  saved: 'bg-purple-500/20 text-purple-400',
  applied: 'bg-blue-500/20 text-blue-400',
  dismissed: 'bg-red-500/10 text-red-500',
}

export function JobCard({
  job,
  onStatusChange,
}: {
  job: Job
  onStatusChange: (id: string, status: Status) => void
}) {
  const [pending, setPending] = useState(false)
  const [saveError, setSaveError] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  async function setStatus(status: Status) {
    setPending(true)
    setSaveError(false)
    try {
      const res = await fetch(`/api/jobs/${job.id}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        console.error('Status update failed:', res.status, body)
        setSaveError(true)
        setPending(false)
        return
      }
      const label = status === 'applied' ? 'Marked as applied' : status === 'dismissed' ? 'Dismissed' : status === 'saved' ? 'Saved' : 'Reset'
      setToast(label)
      setTimeout(() => {
        setToast(null)
        onStatusChange(job.id, status)
      }, 1200)
    } catch (err) {
      console.error('Status update error:', err)
      setSaveError(true)
    }
    setPending(false)
  }

  const isSkip = job.tier === 'SKIP'
  const currentStatus = job.status ?? 'new'
  // ats_watch.py prefixes ids with "ats:" for jobs caught via the direct
  // company-ATS fast-path (vs. LinkedIn or the "gh:" GitHub-tracker source)
  // — surfaced here since those were found straight from the source, often
  // well before LinkedIn's own syndication would have shown them at all.
  const isAtsSourced = job.id.startsWith('ats:')

  return (
    <div className={`relative rounded-xl border p-5 transition-opacity ${
      isSkip
        ? 'bg-gray-900/50 border-gray-800/50 opacity-40 hover:opacity-70'
        : isAtsSourced
        ? 'bg-gray-900 border-indigo-500/50 ring-1 ring-indigo-500/20'
        : 'bg-gray-900 border-gray-800'
    }`}>
      {toast && (
        <div className="absolute inset-0 rounded-xl bg-gray-900/95 flex items-center justify-center z-10 pointer-events-none">
          <span className="text-green-400 font-semibold text-sm">{toast} ✓</span>
        </div>
      )}
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        {/* Company logo */}
        <div className="shrink-0 mt-0.5">
          {job.logo_url ? (
            <Image
              src={job.logo_url}
              alt={job.company}
              width={40}
              height={40}
              className="rounded-lg object-contain bg-white"
              onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
          ) : (
            <div className="w-10 h-10 rounded-lg bg-gray-800 flex items-center justify-center text-gray-400 font-bold text-sm select-none">
              {job.company[0]?.toUpperCase() ?? '?'}
            </div>
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 mb-2">
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-md ${TIER_BADGE[job.tier]}`}>
              {job.tier}
            </span>
            <span className={`text-xs px-2 py-0.5 rounded-md ${STATUS_BADGE[currentStatus]}`}>
              {currentStatus}
            </span>
            {isAtsSourced && (
              <span
                className="text-xs font-semibold px-2 py-0.5 rounded-md bg-indigo-500/20 text-indigo-400 border border-indigo-500/30"
                title="Caught directly from the company's own ATS, ahead of LinkedIn"
              >
                ⚡ Direct
              </span>
            )}
            {job.suggested_resume && job.suggested_resume !== 'N/A' && (
              <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded-md">
                {job.suggested_resume} resume
              </span>
            )}
          </div>

          <p className="text-gray-400 text-sm">
            {job.company}
            {job.location && <span className="text-gray-600"> · {job.location}</span>}
          </p>
          <div className="flex items-center gap-2 mt-0.5">
            {job.salary && (
              <span className="text-xs bg-green-900/30 text-green-400 px-2 py-0.5 rounded-md border border-green-700/30 shrink-0">
                {job.salary}
              </span>
            )}
            <a
              href={job.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-white text-lg font-bold leading-snug hover:text-blue-400 transition-colors"
            >
              {job.title}
            </a>
          </div>
        </div>

        <p
          className="text-gray-600 text-xs shrink-0"
          title={`Picked up ${new Date(job.found_at).toLocaleString('en-US', {
            month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
          })}`}
        >
          {new Date(job.found_at).toLocaleString('en-US', { month: 'short', day: 'numeric' })}
          <br />
          {new Date(job.found_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}
        </p>
      </div>

      {/* Reason */}
      {job.reason && !isSkip && (
        <p className="text-gray-500 text-sm mt-3 leading-relaxed">{job.reason}</p>
      )}

      {/* Actions */}
      {!isSkip && (
        <div className="flex flex-wrap items-center gap-2 mt-4">
          {saveError && (
            <span className="text-xs text-red-400 w-full">Failed to save — check Vercel env vars (SUPABASE_SERVICE_KEY)</span>
          )}
          <a
            href={job.is_easy_apply ? job.url : (job.apply_url ?? job.url)}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs px-3 py-1.5 rounded-lg bg-indigo-600/20 text-indigo-400 hover:bg-indigo-600/30 transition-colors"
          >
            {job.is_easy_apply ? 'Easy Apply ↗' : 'Apply ↗'}
          </a>
          <button
            onClick={() => setStatus('applied')}
            disabled={pending || currentStatus === 'applied'}
            className="text-xs px-3 py-1.5 rounded-lg bg-blue-600/20 text-blue-400 hover:bg-blue-600/30 disabled:opacity-30 transition-colors"
          >
            Applied
          </button>
          <button
            onClick={() => setStatus('saved')}
            disabled={pending || currentStatus === 'saved'}
            className="text-xs px-3 py-1.5 rounded-lg bg-purple-600/20 text-purple-400 hover:bg-purple-600/30 disabled:opacity-30 transition-colors"
          >
            Save
          </button>
          <button
            onClick={() => setStatus('dismissed')}
            disabled={pending || currentStatus === 'dismissed'}
            className="text-xs px-3 py-1.5 rounded-lg bg-gray-800 text-gray-500 hover:bg-gray-700 disabled:opacity-30 transition-colors"
          >
            Dismiss
          </button>
          {currentStatus !== 'new' && (
            <button
              onClick={() => setStatus('new')}
              disabled={pending}
              className="text-xs px-3 py-1.5 rounded-lg bg-gray-800 text-gray-600 hover:bg-gray-700 disabled:opacity-30 transition-colors"
            >
              Reset
            </button>
          )}
        </div>
      )}
    </div>
  )
}
