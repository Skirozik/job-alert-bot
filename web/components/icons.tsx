/* Inline SVG only — emoji are not interface icons. All 14px, currentColor,
   so a single colour token drives them. */
const P = { width: 14, height: 14, viewBox: '0 0 16 16', fill: 'none', 'aria-hidden': true } as const

/** Easy Apply: FILLED. Applying happens in-place on LinkedIn — genuinely
 *  faster, and worth seeing before the click rather than on hover. */
export const IconApplyFilled = () => (
  <svg {...P}><path d="M9 1H3.5A1.5 1.5 0 0 0 2 2.5v11A1.5 1.5 0 0 0 3.5 15h9a1.5 1.5 0 0 0 1.5-1.5V6L9 1Z" fill="currentColor"/></svg>
)
/** External Apply: OUTLINE. Same glyph family, same column, no width cost. */
export const IconApplyOutline = () => (
  <svg {...P} stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
    <path d="M6.5 2.5H3.2A1.2 1.2 0 0 0 2 3.7v9.1A1.2 1.2 0 0 0 3.2 14h9.1a1.2 1.2 0 0 0 1.2-1.2V9.5"/>
    <path d="M9.5 2.5H14v4.4M14 2.5 7.6 8.9"/>
  </svg>
)
export const IconApplied = () => (
  <svg {...P} stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2.5 8.5 6 12l7.5-8"/>
  </svg>
)
export const IconSave = () => (
  <svg {...P} stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round">
    <path d="M4 2h8v12l-4-3-4 3V2Z"/>
  </svg>
)
export const IconDismiss = () => (
  <svg {...P} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
    <path d="M4 4l8 8M12 4l-8 8"/>
  </svg>
)
/** Reset — undo. Reachable from the row AND the drawer whenever a job has a
 *  status; 390 jobs were dismissed in one action, so misfires are expected. */
export const IconReset = () => (
  <svg {...P} stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2.5 8a5.5 5.5 0 1 0 1.7-4"/><path d="M2 2.5V6h3.5"/>
  </svg>
)
export const IconBolt = () => (
  <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor" aria-hidden><path d="M9 1 3 9h4l-1 6 6-8H8l1-6Z"/></svg>
)
export const IconSearch = () => (
  <svg {...P} stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
    <circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5 14 14"/>
  </svg>
)
export const IconRefresh = () => (
  <svg {...P} stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
    <path d="M13.5 8a5.5 5.5 0 1 1-1.7-4"/><path d="M14 2.5V6h-3.5"/>
  </svg>
)
export const IconChevron = ({ dir = 'down' }: { dir?: 'up' | 'down' }) => (
  <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden
       style={{ transform: dir === 'up' ? 'rotate(180deg)' : undefined }}>
    <path d="M4 6l4 4 4-4"/>
  </svg>
)
export const IconClose = () => (
  <svg {...P} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg>
)
/** Opens the view rail on mobile, where the rail is an overlay rather than a
 *  column. Never rendered on desktop — the rail is already on screen there. */
export const IconMenu = () => (
  <svg {...P} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
    <path d="M2.5 4h11M2.5 8h11M2.5 12h11"/>
  </svg>
)
