"use client";

import * as React from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

interface SheetProps {
  /** Controlled open state. */
  open: boolean;
  /** Called when the open state should change (backdrop click, Escape). */
  onOpenChange: (open: boolean) => void;
  children: React.ReactNode;
  /**
   * Maximum width of the sheet panel.
   * @default "max-w-xl" (576 px)
   */
  maxWidth?: string;
}

/**
 * A slide-in drawer that overlays from the right edge of the viewport.
 * Children are kept mounted so internal state (e.g. chat history) persists
 * across open/close cycles.
 */
function Sheet({
  open,
  onOpenChange,
  children,
  maxWidth = "max-w-xl",
}: SheetProps) {
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => {
    setMounted(true);
  }, []);

  /* Close on Escape */
  React.useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onOpenChange]);

  /* Lock body scroll while open */
  React.useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!mounted) return null;

  return createPortal(
    <>
      {/* Backdrop */}
      <div
        className={cn(
          "fixed inset-0 z-50 bg-black/50 transition-opacity duration-300",
          open
            ? "opacity-100"
            : "pointer-events-none opacity-0",
        )}
        onClick={() => onOpenChange(false)}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        role="dialog"
        aria-modal={open}
        className={cn(
          "fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l bg-background shadow-2xl",
          "transition-transform duration-300 ease-out",
          maxWidth,
          open ? "translate-x-0" : "translate-x-full",
        )}
      >
        {children}
      </div>
    </>,
    document.body,
  );
}

export { Sheet };
export type { SheetProps };
