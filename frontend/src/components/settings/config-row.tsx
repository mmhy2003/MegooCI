"use client";

import * as React from "react";

/** One label / value line of a read-only settings card. */
export function ConfigRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border px-3 py-3 text-sm sm:flex-row sm:items-center sm:justify-between sm:px-4">
      <span className="text-muted-foreground">{label}</span>
      <div className="break-all text-left sm:text-right">{children}</div>
    </div>
  );
}
