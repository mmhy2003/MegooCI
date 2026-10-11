import * as React from "react";
import { ThemeToggle } from "@/components/theme-toggle";

/** The full-screen backdrop the sign-in and sign-up cards are centred on. */
export function AuthBackdrop({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-gradient-to-br from-violet-50 via-fuchsia-50/40 to-cyan-50/50 dark:from-[#0a0118] dark:via-[#0d0026] dark:to-[#001a1f] px-4">
      {/* Ambient glow blobs */}
      <div className="pointer-events-none absolute -top-40 -left-40 h-[30rem] w-[30rem] rounded-full bg-primary/10 blur-[100px]" />
      <div className="pointer-events-none absolute -bottom-40 -right-40 h-[30rem] w-[30rem] rounded-full bg-fuchsia-500/10 blur-[100px]" />
      <div className="pointer-events-none absolute top-1/3 left-1/2 h-64 w-64 -translate-x-1/2 rounded-full bg-cyan-400/5 blur-[80px]" />

      {/* Theme toggle floats in the top-right so pre-login users can match
          the app to their OS preference without signing in. */}
      <div className="absolute right-4 top-4 z-20">
        <ThemeToggle variant="icon" />
      </div>

      {children}
    </div>
  );
}
