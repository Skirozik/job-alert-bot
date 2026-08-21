import type { Metadata, Viewport } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Job Dashboard',
}

/* Without this the browser assumes a ~980px desktop canvas and scales the
   whole page down to fit, which is why every control read as unusably tiny on
   a phone regardless of the CSS. Nothing else in the mobile work has any
   effect until this exists.

   Zoom is deliberately NOT capped (no maximumScale / userScalable: false).
   Blocking pinch-zoom is an accessibility failure, and with the layout
   actually responsive there is no longer a reason to reach for it. */
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',   // let content use the full width on notched devices;
                          // safe-area insets are honoured in globals.css
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-gray-950 antialiased">{children}</body>
    </html>
  )
}
