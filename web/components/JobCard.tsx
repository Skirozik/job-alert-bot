'use client'

import { useState } from 'react'
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

  async function setStatus(status: Status) {
    setPending(true)
    try {
      const res = await fetch(`/api/jobs/${job.id}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        console.error('Status update failed:', res.status, body)
        setPending(false)
        return
      }
      onStatusChange(job.id, status)
    } catch (err) {
      console.error('Status update error:', err)
    }
    setPending(false)
  }

  const isSkip = job.tier === 'SKIP'
  const currentStatus = job.status ?? 'new'

  return (
    <div className={`rounded-xl border p-5 transition-opacity ${
      isSkip
        ? 'bg-gray-900/50 border-gray-800/50 opacity-40 hover:opacity-70'
        : 'bg-gray-900 border-gray-800'
    }`}>
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 mb-2">
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-md ${TIER_BADGE[job.tier]}`}>
              {job.tier}
            </span>
            <span className={`text-xs px-2 py-0.5 rounded-md ${STATUS_BADGE[currentStatus]}`}>
              {currentStatus}
            </span>
            {job.suggested_resume && job.suggested_resume !== 'General' && job.suggested_resume !== 'N/A' && (
              <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded-md">
                {job.suggested_resume} resume
              </span>
            )}
          </div>

          <p className="text-gray-400 text-sm">
            {job.company}
            {job.location && <span className="text-gray-600"> · {job.location}</span>}
          </p>
          <a
            href={job.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-white text-lg font-bold leading-snug hover:text-blue-400 transition-colors"
          >
            {job.title}
          </a>
        </div>

        <p className="text-gray-600 text-xs shrink-0">
          {new Date(job.found_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
        </p>
      </div>

      {/* Reason */}
      {job.reason && !isSkip && (
        <p className="text-gray-500 text-sm mt-3 leading-relaxed">{job.reason}</p>
      )}

      {/* Actions */}
      {!isSkip && (
        <div className="flex gap-2 mt-4">
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
