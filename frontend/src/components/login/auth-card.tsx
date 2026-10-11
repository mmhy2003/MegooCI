import * as React from "react";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** A round badge with an account's initials. */
export function AccountAvatar({
  name,
  email,
  className,
}: {
  name: string;
  email: string;
  className?: string;
}) {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const initials =
    words.length > 0
      ? words
          .slice(0, 2)
          .map((word) => word[0])
          .join("")
      : email.slice(0, 1);

  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex h-9 w-9 shrink-0 items-center justify-center rounded-full border bg-primary/10 text-xs font-semibold uppercase text-primary",
        className,
      )}
    >
      {initials}
    </span>
  );
}

/**
 * The card every sign-in screen sits in: the accent bar, then the logo (or
 * what is given as *badge*), a title and a line under it.
 */
export function AuthCard({
  title,
  description,
  badge,
  children,
}: {
  title: string;
  description: string;
  badge?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Card className="relative z-10 w-full max-w-md overflow-hidden border-0 shadow-2xl dark:border dark:border-white/[0.08]">
      {/* Decorative top accent bar */}
      <div className="h-1 w-full bg-gradient-to-r from-primary via-fuchsia-500 to-cyan-400" />

      <CardHeader className="space-y-3 pb-2 pt-8 text-center">
        {badge ?? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src="/icons/icon.svg"
            alt="MegooCI"
            className="mx-auto mb-2 h-14 w-14 rounded-2xl shadow-lg ring-4 ring-primary/10"
          />
        )}
        <CardTitle className="text-2xl font-bold tracking-tight">
          {title}
        </CardTitle>
        <CardDescription className="break-words text-sm">
          {description}
        </CardDescription>
      </CardHeader>

      {children}
    </Card>
  );
}
