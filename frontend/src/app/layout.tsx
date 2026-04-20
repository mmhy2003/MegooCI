import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import { Providers, ThemedToaster } from "@/components/providers";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "MegooCI",
  description: "A simpler, modern open-source alternative to Jenkins",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0f1e" },
  ],
};

/**
 * Inline script that runs before React hydrates so the correct `dark` class
 * is on <html> on the very first paint. Without this, a user who has
 * explicitly chosen dark mode (or whose OS prefers dark in "system" mode)
 * would briefly see a light-themed page during hydration. Keep the script
 * tiny and self-contained — it's inlined into the initial HTML.
 */
const THEME_INIT_SCRIPT = `(function(){try{var k='megooci_theme';var s=localStorage.getItem(k);var t=s||'system';var d=t==='dark'||(t==='system'&&window.matchMedia('(prefers-color-scheme: dark)').matches);if(d){document.documentElement.classList.add('dark');}}catch(e){}})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className={`${inter.variable} font-sans antialiased`}>
        <Providers>
          {children}
          <ThemedToaster />
        </Providers>
      </body>
    </html>
  );
}
