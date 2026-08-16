'use client'

import { useState } from 'react'
import Image from 'next/image'

/**
 * Company favicon with a letter-avatar fallback.
 *
 * The fallback previously only fired when logo_url was null. 16 of 54 LinkedIn
 * CDN images fail to LOAD (naturalWidth 0) — expired or hotlink-blocked — and
 * the old handler just set display:none, leaving a blank white square on the
 * dark theme. onError now flips to the avatar so a failed image is
 * indistinguishable from an absent one.
 */
export function CompanyLogo({ src, company }: { src: string | null; company: string }) {
  const [broken, setBroken] = useState(false)
  const letter = company?.[0]?.toUpperCase() ?? '?'

  if (!src || broken) {
    return (
      <span
        aria-hidden
        className="flex items-center justify-center shrink-0 select-none"
        style={{
          width: 20, height: 20, borderRadius: 4,
          background: 'var(--bg-active)', color: 'var(--fg-subtle)',
          fontSize: 'var(--text-meta)', fontWeight: 600,
        }}
      >
        {letter}
      </span>
    )
  }

  return (
    <Image
      src={src}
      alt=""
      width={20}
      height={20}
      className="rounded object-contain shrink-0"
      // No white plate behind it — that is what made a failed load read as a
      // bright square. An image that loads paints its own background.
      onError={() => setBroken(true)}
      unoptimized
    />
  )
}
