"use client";

import * as React from "react";
import { toast } from "sonner";
import { ArrowLeft, Fingerprint, ShieldCheck } from "lucide-react";
import { errorStatus, twoStepApi, type SignInResult } from "@/lib/api";
import { useAuthStore, type PendingTwoStep } from "@/lib/auth";
import {
  passkeyPromptDismissed,
  passkeyUnavailableReason,
  signInWithPasskey,
} from "@/lib/passkeys";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CardContent } from "@/components/ui/card";
import { AuthCard } from "./auth-card";

const linkClass =
  "text-xs text-muted-foreground transition-colors hover:text-primary hover:underline";

/**
 * The second step of signing in, after a correct password: a code from the
 * authenticator app, a passkey, or a recovery code. Shows only what the
 * account has.
 */
export function SecondStep({ pending }: { pending: PendingTwoStep }) {
  const acceptSignIn = useAuthStore((s) => s.acceptSignIn);
  const clearPending = useAuthStore((s) => s.clearPending);
  const [recovering, setRecovering] = React.useState(false);
  const [code, setCode] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const hasCode = pending.methods.includes("totp");
  const hasPasskey = pending.methods.includes("passkey");
  const passkeyProblem = hasPasskey
    ? passkeyUnavailableReason(pending.passkeyOrigin)
    : null;
  const canUsePasskey = hasPasskey && passkeyProblem === null;

  const finish = React.useCallback(
    async (result: SignInResult) => {
      await acceptSignIn(result, pending.email);
      toast.success("Welcome back!");
      if (result.status === "signed_in" && result.recovery_codes_left !== undefined) {
        const left = result.recovery_codes_left;
        toast.warning(
          left === 0
            ? "That was your last recovery code. Generate new ones in Settings."
            : `You have ${left} recovery code${left === 1 ? "" : "s"} left.`,
        );
      }
    },
    [acceptSignIn, pending.email],
  );

  const refuse = React.useCallback(
    (error: unknown, fallback: string) => {
      toast.error(error instanceof Error ? error.message : fallback);
      // The ticket is used up or too old: only the password gets a new one.
      if (errorStatus(error) === 410) clearPending();
    },
    [clearPending],
  );

  async function submitCode(e: React.FormEvent) {
    e.preventDefault();
    const typed = code.trim();
    if (!typed) return;
    setBusy(true);
    try {
      await finish(
        await twoStepApi.verify(
          pending.ticket,
          recovering ? { recovery_code: typed } : { code: typed },
        ),
      );
    } catch (error) {
      refuse(error, "That code is not right.");
    } finally {
      setBusy(false);
    }
  }

  const startPasskey = React.useCallback(async () => {
    setBusy(true);
    try {
      const { options } = await twoStepApi.passkeyOptions(pending.ticket);
      const credential = await signInWithPasskey(options);
      await finish(await twoStepApi.passkeyVerify(pending.ticket, credential));
    } catch (error) {
      // A dismissed prompt sent nothing: the screen just stays as it is.
      if (!passkeyPromptDismissed(error)) refuse(error, "That passkey was not accepted.");
    } finally {
      setBusy(false);
    }
  }, [pending.ticket, finish, refuse]);

  // An account with passkeys only has nothing to type here, so the browser's
  // own prompt opens straight away. Once: the button is there for another try.
  const prompted = React.useRef(false);
  React.useEffect(() => {
    if (prompted.current || hasCode || !canUsePasskey) return;
    prompted.current = true;
    void startPasskey();
  }, [hasCode, canUsePasskey, startPasskey]);

  function switchTo(recovery: boolean) {
    setRecovering(recovery);
    setCode("");
  }

  return (
    <AuthCard
      title={recovering ? "Use a recovery code" : "Two-step verification"}
      description={recovering ? "One of the codes you saved at setup" : pending.email}
    >
      <CardContent className="space-y-4 px-8 pb-8 pt-3">
        {(recovering || hasCode) && (
          <form onSubmit={submitCode} className="space-y-4">
            <div className="space-y-2">
              <label htmlFor="second-step-code" className="text-sm font-medium">
                {recovering ? "Recovery code" : "Code from your authenticator app"}
              </label>
              <Input
                id="second-step-code"
                key={recovering ? "recovery" : "code"}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                inputMode={recovering ? "text" : "numeric"}
                autoComplete={recovering ? "off" : "one-time-code"}
                autoCapitalize="none"
                spellCheck={false}
                placeholder={recovering ? "xxxxx-xxxxx" : "123456"}
                maxLength={recovering ? 24 : 12}
                autoFocus
                className={
                  recovering
                    ? "h-10 text-center font-mono"
                    : "h-10 text-center font-mono text-base tracking-[0.3em]"
                }
              />
            </div>
            <Button
              type="submit"
              className="h-10 w-full gap-2 text-sm font-semibold"
              disabled={busy || !code.trim()}
            >
              <ShieldCheck className="h-4 w-4" />
              Verify
            </Button>
            {recovering && (
              <p className="text-xs text-muted-foreground">
                Each code works once. After signing in you are told how many are left.
              </p>
            )}
          </form>
        )}

        {!recovering && hasCode && hasPasskey && (
          <div className="relative flex w-full items-center justify-center py-1">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t" />
            </div>
            <span className="relative bg-card px-3 text-xs text-muted-foreground">or</span>
          </div>
        )}

        {!recovering && hasPasskey && (
          canUsePasskey ? (
            <Button
              type="button"
              variant={hasCode ? "outline" : "default"}
              className="h-10 w-full gap-2 text-sm font-medium"
              onClick={startPasskey}
              disabled={busy}
            >
              <Fingerprint className="h-4 w-4" />
              Use a passkey
            </Button>
          ) : (
            <p className="rounded-md border px-3 py-2 text-xs text-muted-foreground">
              {passkeyProblem}
            </p>
          )
        )}

        <div className="flex flex-col items-center gap-3 pt-1">
          {recovering ? (
            <button type="button" className={linkClass} onClick={() => switchTo(false)}>
              Use a code or passkey instead
            </button>
          ) : (
            <button type="button" className={linkClass} onClick={() => switchTo(true)}>
              Use a recovery code
            </button>
          )}
          <button
            type="button"
            className={`flex items-center gap-1 ${linkClass}`}
            onClick={clearPending}
          >
            <ArrowLeft className="h-3 w-3" />
            Back to sign in
          </button>
        </div>
      </CardContent>
    </AuthCard>
  );
}
