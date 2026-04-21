import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import { Providers, ThemedToaster } from "@/components/providers";
import { PWARegister } from "@/components/pwa-register";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "MegooCI",
  description: "A simpler, modern open-source alternative to Jenkins",
  manifest: "/manifest.webmanifest",
  applicationName: "MegooCI",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "MegooCI",
  },
  icons: {
    icon: [
      { url: "/icons/icon.svg", type: "image/svg+xml" },
      { url: "/icons/favicon-32.png", sizes: "32x32", type: "image/png" },
      { url: "/icons/favicon-16.png", sizes: "16x16", type: "image/png" },
    ],
    shortcut: "/icons/favicon.ico",
    apple: "/icons/apple-touch-icon.png",
  },
  other: {
    "msapplication-TileColor": "#ff2d95",
    "msapplication-TileImage": "/icons/icon-144.png",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  viewportFit: "cover",
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#00fff0" },
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
          <PWARegister />
        </Providers>
      </body>
    </html>
  );
}
