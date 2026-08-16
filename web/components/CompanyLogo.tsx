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

  // First letter of the company, chosen so the SERVER and CLIENT always agree.
  //
  // company[0] does not. The GitHub tracker prefixes hot companies with an
  // emoji - "ByteDance", "Meta", "SpaceX", 9 on the current list - and U+1F525
  // is astral, so in UTF-16 it is a surrogate PAIR. company[0] returns the lone
  // high surrogate \uD800-range code unit, which is not a valid standalone code
  // point: Node drops it when serialising the server HTML, the browser
  // substitutes U+FFFD, and React throws
  //   "Text content did not match. Server: "" Client: <U+FFFD>"
  //
  // Dropping surrogate code units first leaves only BMP characters, where
  // indexing is unambiguous and both runtimes agree. It also gives a better
  // avatar - "B" for the ByteDance row rather than half a fire emoji - while
  // keeping non-ASCII initials intact, so "Otsuka" with a macron still yields
  // its own first letter rather than skipping to the "t".
  //
  // Deliberately NOT /u + \p{L}: the type-checker targets ES5 and rejects both.
  const cleaned = (company ?? '').replace(/[\uD800-\uDFFF]/g, '').replace(/^[^\w\u00C0-\uFFFF]+/, '').trim()
  const letter = (cleaned.charAt(0) || '?').toUpperCase()

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
