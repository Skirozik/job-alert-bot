'use client'

import { useSyncExternalStore } from 'react'

/* One breakpoint for the whole app, matching the 768px query in globals.css.
   Both must move together — JobTable's row windowing depends on agreeing with
   CSS about which layout is on screen, and a mismatch there does not look like
   a breakpoint bug, it looks like the scroll position drifting. */
export const MOBILE_QUERY = '(max-width: 768px)'

/* useSyncExternalStore rather than useState+useEffect, because this value is
   read during render by JobTable to pick a row height. The effect version
   renders once with the desktop value and then corrects on mount, which shows
   up as a visible reflow on every mobile load and, worse, makes the first
   windowing pass compute its visible slice against the wrong row height.

   getServerSnapshot returns false: there is no viewport during SSR, so the
   markup is generated desktop-shaped and the client corrects it on hydration.
   Guessing "mobile" server-side would be wrong just as often and would make
   the desktop path the one that flashes. */
function subscribe(onChange: () => void) {
  if (typeof window === 'undefined') return () => {}
  const mql = window.matchMedia(MOBILE_QUERY)
  // addEventListener over the deprecated addListener; Safari < 14 is the only
  // casualty and it is well below this app's floor.
  mql.addEventListener('change', onChange)
  return () => mql.removeEventListener('change', onChange)
}

export function useIsMobile(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(MOBILE_QUERY).matches,
    () => false,
  )
}
