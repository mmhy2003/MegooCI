"use client";

import * as React from "react";
import { AlertTriangle, Trash2, Info, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

type ConfirmTone = "default" | "destructive" | "warning" | "success";

export interface ConfirmOptions {
  title: string;
  description?: React.ReactNode;
  confirmText?: string;
  cancelText?: string;
  tone?: ConfirmTone;
}

interface ConfirmContextValue {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
}

const ConfirmContext = React.createContext<ConfirmContextValue | null>(null);

export function useConfirm(): (options: ConfirmOptions) => Promise<boolean> {
  const ctx = React.useContext(ConfirmContext);
  if (!ctx) {
    throw new Error("useConfirm must be used within ConfirmProvider");
  }
  return ctx.confirm;
}

interface PendingConfirm extends ConfirmOptions {
  // Identity counter so a late "clear pending" timer from a previous
  // confirm can't blank out a fresh one that opened during the 150ms
  // exit-animation window.
  id: number;
  resolve: (value: boolean) => void;
}

const toneConfig: Record<
  ConfirmTone,
  {
    icon: React.ElementType;
    iconClass: string;
    iconBg: string;
    confirmVariant: "default" | "destructive";
  }
> = {
  default: {
    icon: Info,
    iconClass: "text-primary",
    iconBg: "bg-primary/10",
    confirmVariant: "default",
  },
  destructive: {
    icon: Trash2,
    iconClass: "text-destructive",
    iconBg: "bg-destructive/10",
    confirmVariant: "destructive",
  },
  warning: {
    icon: AlertTriangle,
    iconClass: "text-amber-600 dark:text-amber-400",
    iconBg: "bg-amber-500/10",
    confirmVariant: "default",
  },
  success: {
    icon: CheckCircle2,
    iconClass: "text-emerald-600 dark:text-emerald-400",
    iconBg: "bg-emerald-500/10",
    confirmVariant: "default",
  },
};

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [pending, setPending] = React.useState<PendingConfirm | null>(null);
  const [visible, setVisible] = React.useState(false);
  const confirmButtonRef = React.useRef<HTMLButtonElement>(null);
  // Counter that mints a fresh id per confirm so we can detect when a
  // queued "clear pending" timer belongs to a now-stale dialog.
  const nextIdRef = React.useRef(0);
  const clearTimerRef = React.useRef<number | null>(null);

  const confirm = React.useCallback<ConfirmContextValue["confirm"]>(
    (options) =>
      new Promise<boolean>((resolve) => {
        // A previous close() may have queued a setPending(null) timer to
        // run after its exit animation. Cancel it before opening — otherwise
        // it will blank out the dialog we're about to show.
        if (clearTimerRef.current !== null) {
          window.clearTimeout(clearTimerRef.current);
          clearTimerRef.current = null;
        }
        const id = ++nextIdRef.current;
        setPending({ ...options, id, resolve });
        setVisible(true);
      }),
    [],
  );

  const close = React.useCallback(
    (result: boolean) => {
      if (!pending) return;
      const closingId = pending.id;
      pending.resolve(result);
      setVisible(false);
      // Delay clearing so the exit transition can play out. Guard against
      // a fresh confirm() opening in the meantime — we only null out
      // `pending` if it still belongs to the dialog we just closed.
      clearTimerRef.current = window.setTimeout(() => {
        clearTimerRef.current = null;
        setPending((current) => (current && current.id === closingId ? null : current));
      }, 150);
    },
    [pending],
  );

  // Esc + Enter keyboard handling
  React.useEffect(() => {
    if (!visible) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close(false);
      } else if (e.key === "Enter") {
        e.preventDefault();
        close(true);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [visible, close]);

  // Autofocus the confirm button when opened for keyboard-first UX
  React.useEffect(() => {
    if (visible) {
      // wait a tick so the element is mounted
      const raf = requestAnimationFrame(() => {
        confirmButtonRef.current?.focus();
      });
      return () => cancelAnimationFrame(raf);
    }
  }, [visible]);

  // Lock body scroll while open
  React.useEffect(() => {
    if (visible) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = prev;
      };
    }
  }, [visible]);

  const tone = pending?.tone ?? "default";
  const toneCfg = toneConfig[tone];
  const Icon = toneCfg.icon;

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      {pending && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-dialog-title"
          className={cn(
            "fixed inset-0 z-[100] flex items-end justify-center p-4 transition-opacity duration-150 sm:items-center",
            visible ? "opacity-100" : "pointer-events-none opacity-0",
          )}
        >
          <div
            className="fixed inset-0 bg-black/70 backdrop-blur-sm"
            onClick={() => close(false)}
            aria-hidden="true"
          />
          <div
            className={cn(
              "relative z-[101] w-full max-w-md overflow-hidden rounded-xl border bg-background shadow-2xl",
              "transition-all duration-150",
              visible
                ? "translate-y-0 opacity-100 scale-100"
                : "translate-y-2 opacity-0 scale-[0.98]",
            )}
          >
            <div className="flex gap-4 p-5 sm:p-6">
              <div
                className={cn(
                  "flex h-10 w-10 shrink-0 items-center justify-center rounded-full",
                  toneCfg.iconBg,
                )}
                aria-hidden="true"
              >
                <Icon className={cn("h-5 w-5", toneCfg.iconClass)} />
              </div>
              <div className="min-w-0 flex-1 pt-0.5">
                <h2
                  id="confirm-dialog-title"
                  className="text-base font-semibold leading-6"
                >
                  {pending.title}
                </h2>
                {pending.description && (
                  <div className="mt-1.5 text-sm text-muted-foreground">
                    {pending.description}
                  </div>
                )}
              </div>
            </div>
            <div className="flex flex-col-reverse gap-2 border-t bg-muted/40 px-5 py-3 sm:flex-row sm:justify-end sm:gap-2 sm:px-6">
              <Button
                variant="outline"
                size="sm"
                onClick={() => close(false)}
                className="sm:w-auto"
              >
                {pending.cancelText ?? "Cancel"}
              </Button>
              <Button
                ref={confirmButtonRef}
                variant={toneCfg.confirmVariant}
                size="sm"
                onClick={() => close(true)}
                className="sm:w-auto"
              >
                {pending.confirmText ?? "Confirm"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}
