"use client";

import * as React from "react";
import QRCode from "qrcode";
import type { TotpSetup } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** A base32 secret in groups of four, as it is easiest to type. */
function inGroups(secret: string): string {
  return secret.match(/.{1,4}/g)?.join(" ") ?? secret;
}

/**
 * What it takes to add an authenticator app: the QR code to scan, the same
 * secret as text for typing, and a field for the code the app then shows.
 * Used on the sign-in card and in the Settings dialog.
 */
export function AuthenticatorSetup({
  setup,
  code,
  onCodeChange,
  className,
}: {
  setup: TotpSetup;
  code: string;
  onCodeChange: (code: string) => void;
  className?: string;
}) {
  const [qr, setQr] = React.useState<string | null>(null);

  React.useEffect(() => {
    let current = true;
    // Drawn in the browser: the secret is never sent anywhere to be turned
    // into a picture.
    QRCode.toDataURL(setup.otpauth_uri, { margin: 1, width: 176 })
      .then((url) => {
        if (current) setQr(url);
      })
      .catch(() => {
        if (current) setQr(null);
      });
    return () => {
      current = false;
    };
  }, [setup.otpauth_uri]);

  return (
    <div className={cn("space-y-4", className)}>
      <div className="flex justify-center">
        {qr ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={qr}
            alt="QR code to scan with your authenticator app"
            width={176}
            height={176}
            className="rounded-md border bg-white p-1"
          />
        ) : (
          <div className="h-[176px] w-[176px] rounded-md border bg-muted" />
        )}
      </div>

      <div className="text-center text-xs text-muted-foreground">
        Can&apos;t scan? Enter this key in the app:
        <div className="mt-1 select-all break-all font-mono text-sm text-foreground">
          {inGroups(setup.secret)}
        </div>
      </div>

      <div className="space-y-2">
        <label htmlFor="two-step-setup-code" className="text-sm font-medium">
          Code from the app
        </label>
        <Input
          id="two-step-setup-code"
          value={code}
          onChange={(e) => onCodeChange(e.target.value)}
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="123456"
          maxLength={12}
          autoFocus
          className="h-10 text-center font-mono text-base tracking-[0.3em]"
        />
      </div>
    </div>
  );
}
