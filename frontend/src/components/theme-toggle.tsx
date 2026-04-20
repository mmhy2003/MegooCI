"use client";

import * as React from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { cn } from "@/lib/utils";
import { useTheme } from "@/components/providers";

type ThemeValue = "light" | "dark" | "system";

const OPTIONS: {
  value: ThemeValue;
  label: string;
  icon: React.ElementType;
}[] = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
];

/**
 * Three-way theme picker (Light / Dark / System). The "System" option tracks
 * the OS-level `prefers-color-scheme` media query and updates automatically
 * when the user flips their OS theme, as long as the user hasn't explicitly
 * chosen Light or Dark here.
 *
 * Variants:
 * - `segmented` (default): a horizontal pill-shaped segmented control.
 *   Best for settings pages where the label + full control fits.
 * - `icon`: a compact single-icon button that cycles on click. Best for
 *   corners and tight spaces (e.g. top-right of the auth pages).
 */
export function ThemeToggle({
  variant = "segmented",
  className,
}: {
  variant?: "segmented" | "icon";
  className?: string;
}) {
  const { theme, setTheme, resolvedTheme } = useTheme();

  if (variant === "icon") {
    // Cycle light -> dark -> system -> light. We show the icon of the
    // *next* state so clicking feels predictable: users see "what will I
    // get if I click?".
    const currentIdx = OPTIONS.findIndex((o) => o.value === theme);
    const nextIdx = (currentIdx + 1) % OPTIONS.length;
    const next = OPTIONS[nextIdx];
    // The visible icon is whichever matches the *currently resolved* mode
    // so the button reflects reality (especially important for "system").
    const VisibleIcon =
      theme === "system"
        ? Monitor
        : resolvedTheme === "dark"
          ? Moon
          : Sun;

    return (
      <button
        type="button"
        onClick={() => setTheme(next.value)}
        title={`Theme: ${theme === "system" ? "System" : theme === "dark" ? "Dark" : "Light"} (click for ${next.label})`}
        aria-label="Toggle theme"
        className={cn(
          "inline-flex h-9 w-9 items-center justify-center rounded-md border border-input bg-background text-foreground shadow-sm transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          className,
        )}
      >
        <VisibleIcon className="h-4 w-4" />
      </button>
    );
  }

  return (
    <div
      className={cn(
        "inline-flex items-center rounded-md border border-input bg-background p-0.5 shadow-sm",
        className,
      )}
      role="radiogroup"
      aria-label="Theme"
    >
      {OPTIONS.map((opt) => {
        const Icon = opt.icon;
        const isActive = theme === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={isActive}
            onClick={() => setTheme(opt.value)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-sm transition-colors",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
              isActive
                ? "bg-accent text-accent-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            <span>{opt.label}</span>
          </button>
        );
      })}
    </div>
  );
}
