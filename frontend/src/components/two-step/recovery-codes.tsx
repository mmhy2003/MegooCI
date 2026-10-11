"use client";

import * as React from "react";
import { toast } from "sonner";
import { Copy, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function asText(codes: string[]): string {
  return [
    "MegooCI recovery codes",
    "Each code works once, in place of a code from your app or a passkey.",
    "",
    ...codes,
    "",
  ].join("\n");
}

/**
 * Recovery codes at the one moment they can be seen, with Copy and Download.
 * Used on the sign-in card and in the Settings dialog.
 */
export function RecoveryCodes({
  codes,
  className,
}: {
  codes: string[];
  className?: string;
}) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      toast.success("Recovery codes copied");
    } catch {
      toast.error("Could not copy. Select the codes and copy them yourself.");
    }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([asText(codes)], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "megooci-recovery-codes.txt";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className={cn("space-y-3", className)}>
      <ul className="grid select-all grid-cols-2 gap-x-6 gap-y-1.5 rounded-lg bg-muted px-4 py-3 font-mono text-sm">
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ul>
      <div className="flex gap-2">
        <Button type="button" variant="outline" className="h-9 flex-1 gap-2" onClick={copy}>
          <Copy className="h-4 w-4" />
          Copy
        </Button>
        <Button type="button" variant="outline" className="h-9 flex-1 gap-2" onClick={download}>
          <Download className="h-4 w-4" />
          Download
        </Button>
      </div>
    </div>
  );
}
