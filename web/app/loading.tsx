/**
 * Shell skeleton, shown while the server component streams.
 *
 * page.tsx is force-dynamic and fetches three Supabase queries per load, so
 * without this the browser holds a blank white page for the whole roundtrip.
 * This paints the frame immediately and lets the real table swap in.
 *
 * Deliberately plain: every colour and dimension comes from the same CSS
 * variables the real UI uses (globals.css), so it can never drift into a
 * different-looking placeholder, and there are no new dependencies.
 */
const ROWS = 12

export default function Loading() {
  return (
    <div className="flex" aria-busy="true" aria-label="Loading jobs">
      {/* Sidebar rail */}
      <div
        className="shrink-0 h-screen"
        style={{
          width: 'var(--rail-w)',
          background: 'var(--bg-rail)',
          borderRight: '1px solid var(--border)',
        }}
      />

      <div className="flex-1 min-w-0">
        {/* Toolbar */}
        <div
          className="flex items-center"
          style={{
            height: 40,
            padding: '0 var(--s4)',
            borderBottom: '1px solid var(--border)',
            background: 'var(--bg)',
          }}
        >
          <div
            style={{
              width: 220, height: 20,
              background: 'var(--bg-active)',
              borderRadius: 'var(--radius)',
            }}
          />
        </div>

        {/* Table header */}
        <div
          style={{
            height: 24,
            borderBottom: '1px solid var(--border)',
            background: 'var(--bg)',
          }}
        />

        {/* Rows — same 44px geometry as the real table, so the swap does not jump */}
        {Array.from({ length: ROWS }).map((_, i) => (
          <div
            key={i}
            className="flex items-center"
            style={{
              height: 'var(--row-h)',
              borderBottom: '1px solid var(--border)',
              padding: '0 var(--s3)',
              gap: 'var(--s3)',
            }}
          >
            <div style={{ width: 20, height: 20, borderRadius: 4, background: 'var(--bg-active)' }} />
            <div style={{ width: 150, height: 10, borderRadius: 3, background: 'var(--bg-active)' }} />
            <div style={{ flex: 1, maxWidth: 320, height: 10, borderRadius: 3, background: 'var(--bg-active)' }} />
            <div style={{ width: 110, height: 10, borderRadius: 3, background: 'var(--bg-active)' }} />
            <div style={{ width: 70, height: 10, borderRadius: 3, background: 'var(--bg-active)' }} />
          </div>
        ))}
      </div>
    </div>
  )
}
