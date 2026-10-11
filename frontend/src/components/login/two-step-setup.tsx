"use client";

import * as React from "react";
import { toast } from "sonner";
import { ArrowLeft, Fingerprint, Smartphone } from "lucide-react";
import { errorStatus, twoStepApi, type PasskeyJson, type TotpSetup } from "@/lib/api";
import { useAuthStore, type PendingTwoStep } from "@/lib/auth";
import {
  createPasskey,
  passkeyAlreadyRegistered,
  passkeyPromptDismissed,
  passkeyUnavailableReason,
} from "@/lib/passkeys";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CardContent } from "@/components/ui/card";
import { AuthenticatorSetup } from "@/components/two-step/authenticator-setup";
import { RecoveryCodes } from "@/components/two-step/recovery-codes";
import { AuthCard } from "./auth-card";

type Step =
  | { name: "choose" }
  | { name: "authenticator"; setup: TotpSetup }
  | { name: "passkey"; credential: PasskeyJson }
  | { name: "codes"; codes: string[] };

const linkClass =
  "flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-primary hover:underline";
const choiceClass =
  "flex w-full items-start gap-3 rounded-lg border px-3 py-3 text-left transition-colors hover:border-primary hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50";

/**
 * Setting two-step verification up while signing in, on a server that
 * requires it: choose a method, set it up, save the recovery codes. The
 * session only comes at the end.
 */
export function TwoStepSetup({ pending }: { pending: PendingTwoStep }) {
  const acceptSignIn = useAuthStore((s) => s.acceptSignIn);
  const clearPending = useAuthStore((s) => s.clearPending);
  const [step, setStep] = React.useState<Step>({ name: "choose" });
  const [code, setCode] = React.useState("");
  const [passkeyName, setPasskeyName] = React.useState("My passkey");
  const [saved, setSaved] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const passkeyProblem = passkeyUnavailableReason(pending.passkeyOrigin);

  function refuse(error: unknown, fallback: string) {
    toast.error(error instanceof Error ? error.message : fallback);
    // The ticket is too old, or the account was set up elsewhere meanwhile.
    if (errorStatus(error) === 410) clearPending();
  }

  async function run(action: () => Promise<void>, fallback: string) {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      if (passkeyAlreadyRegistered(error)) {
        toast.error("This passkey is already registered.");
      } else if (!passkeyPromptDismissed(error)) {
        refuse(error, fallback);
      }
    } finally {
      setBusy(false);
    }
  }

  const chooseAuthenticator = () =>
    run(async () => {
      const setup = await twoStepApi.setupTotp(pending.ticket);
      setCode("");
      setStep({ name: "authenticator", setup });
    }, "Could not start the setup.");

  const confirmAuthenticator = (e: React.FormEvent) => {
    e.preventDefault();
    return run(async () => {
      const { recovery_codes } = await twoStepApi.setupTotpConfirm(pending.ticket, code.trim());
      setStep({ name: "codes", codes: recovery_codes ?? [] });
    }, "That code is not right.");
  };

  const choosePasskey = () =>
    run(async () => {
      const { options } = await twoStepApi.setupPasskeyOptions(pending.ticket);
      const credential = await createPasskey(options);
      setStep({ name: "passkey", credential });
    }, "That passkey was not accepted.");

  const savePasskey = (e: React.FormEvent) => {
    e.preventDefault();
    if (step.name !== "passkey") return;
    const credential = step.credential;
    return run(async () => {
      const { recovery_codes } = await twoStepApi.setupPasskey(
        pending.ticket,
        credential,
        passkeyName.trim(),
      );
      setStep({ name: "codes", codes: recovery_codes ?? [] });
    }, "That passkey was not accepted.");
  };

  const finish = () =>
    run(async () => {
      await acceptSignIn(await twoStepApi.setupFinish(pending.ticket), pending.email);
      toast.success("Two-step verification is on. Welcome to MegooCI!");
    }, "Could not finish signing in.");

  const backToSignIn = (
    <button type="button" className={linkClass} onClick={clearPending}>
      <ArrowLeft className="h-3 w-3" />
      Back to sign in
    </button>
  );

  if (step.name === "authenticator") {
    return (
      <AuthCard title="Scan with your app" description="Then enter the code it shows">
        <CardContent className="px-8 pb-8 pt-3">
          <form onSubmit={confirmAuthenticator} className="space-y-4">
            <AuthenticatorSetup setup={step.setup} code={code} onCodeChange={setCode} />
            <Button
              type="submit"
              className="h-10 w-full text-sm font-semibold"
              disabled={busy || !code.trim()}
            >
              Confirm
            </Button>
            <div className="flex justify-center">
              <button
                type="button"
                className={linkClass}
                onClick={() => setStep({ name: "choose" })}
              >
                <ArrowLeft className="h-3 w-3" />
                Choose another method
              </button>
            </div>
          </form>
        </CardContent>
      </AuthCard>
    );
  }

  if (step.name === "passkey") {
    return (
      <AuthCard
        title="Name this passkey"
        description="Your browser has created it. Give it a name you will recognise."
      >
        <CardContent className="px-8 pb-8 pt-3">
          <form onSubmit={savePasskey} className="space-y-4">
            <div className="space-y-2">
              <label htmlFor="setup-passkey-name" className="text-sm font-medium">
                Name
              </label>
              <Input
                id="setup-passkey-name"
                value={passkeyName}
                onChange={(e) => setPasskeyName(e.target.value)}
                maxLength={100}
                autoFocus
                className="h-10"
              />
            </div>
            <Button type="submit" className="h-10 w-full text-sm font-semibold" disabled={busy}>
              Save
            </Button>
          </form>
        </CardContent>
      </AuthCard>
    );
  }

  if (step.name === "codes") {
    return (
      <AuthCard title="Save your recovery codes" description="They are shown only once">
        <CardContent className="space-y-4 px-8 pb-8 pt-3">
          <p className="text-xs text-muted-foreground">
            Each code works once, in place of a code from your app or a passkey, for
            when you have neither.
          </p>
          <RecoveryCodes codes={step.codes} />
          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={saved}
              onChange={(e) => setSaved(e.target.checked)}
              className="h-4 w-4 rounded border-input accent-primary"
            />
            I have saved these codes
          </label>
          <Button
            type="button"
            className="h-10 w-full text-sm font-semibold"
            onClick={finish}
            disabled={busy || !saved}
          >
            Continue to MegooCI
          </Button>
        </CardContent>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="Set up two-step verification"
      description="Your administrator requires it for every account"
    >
      <CardContent className="space-y-3 px-8 pb-8 pt-3">
        <button type="button" className={choiceClass} onClick={chooseAuthenticator} disabled={busy}>
          <Smartphone className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
          <span>
            <span className="block text-sm font-medium">Authenticator app</span>
            <span className="block text-xs text-muted-foreground">
              Codes from an app such as Google Authenticator, 1Password or Authy
            </span>
          </span>
        </button>
        <button
          type="button"
          className={choiceClass}
          onClick={choosePasskey}
          disabled={busy || passkeyProblem !== null}
        >
          <Fingerprint className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
          <span>
            <span className="block text-sm font-medium">Passkey</span>
            <span className="block text-xs text-muted-foreground">
              {passkeyProblem ?? "Fingerprint, face, device PIN or a security key"}
            </span>
          </span>
        </button>
        <div className="flex justify-center pt-1">{backToSignIn}</div>
      </CardContent>
    </AuthCard>
  );
}
