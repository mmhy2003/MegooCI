"use client";

import * as React from "react";
import { toast } from "sonner";
import { format } from "date-fns";
import { Fingerprint, LifeBuoy, ShieldCheck, Smartphone } from "lucide-react";
import {
  authApi,
  twoStepApi,
  type PasskeyInfo,
  type PasskeyJson,
  type TotpSetup,
  type TwoStepSummary,
} from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import {
  createPasskey,
  passkeyAlreadyRegistered,
  passkeyPromptDismissed,
  passkeyUnavailableReason,
} from "@/lib/passkeys";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { AuthenticatorSetup } from "@/components/two-step/authenticator-setup";
import { PasswordDialog, type PasswordPrompt } from "@/components/two-step/password-dialog";
import { RecoveryCodes } from "@/components/two-step/recovery-codes";

type Open =
  | { kind: "authenticator"; setup: TotpSetup }
  | { kind: "passkey-name"; ticket: string; credential: PasskeyJson }
  | { kind: "rename"; passkey: PasskeyInfo }
  | { kind: "codes"; codes: string[] }
  | null;

function day(value: string | null): string {
  return value ? format(new Date(value), "d MMM yyyy") : "never";
}

function message(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

/** Adding a method changes how the account signs in, so it asks for the password. */
function PasswordField({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-2">
      <label htmlFor="two-step-add-password" className="text-sm font-medium">
        Your password
      </label>
      <Input
        id="two-step-add-password"
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="current-password"
      />
    </div>
  );
}

/** Settings → Account → Two-step verification. */
export function TwoStepTab() {
  const [summary, setSummary] = React.useState<TwoStepSummary | null>(null);
  const [open, setOpen] = React.useState<Open>(null);
  const [prompt, setPrompt] = React.useState<PasswordPrompt | null>(null);
  const [code, setCode] = React.useState("");
  const [name, setName] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    try {
      setSummary(await twoStepApi.summary());
    } catch (error) {
      toast.error(message(error, "Failed to load two-step verification"));
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  /** After a change: the cards, and what the rest of the app knows (the banner). */
  const changed = React.useCallback(
    async (next?: TwoStepSummary) => {
      if (next) setSummary(next);
      else await load();
      try {
        useAuthStore.setState({ user: await authApi.getMe() });
      } catch {
        // The banner catches up on the next page load.
      }
    },
    [load],
  );

  async function run(action: () => Promise<void>, fallback: string) {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      if (passkeyAlreadyRegistered(error)) {
        toast.error("This passkey is already registered.");
      } else if (!passkeyPromptDismissed(error)) {
        toast.error(message(error, fallback));
      }
    } finally {
      setBusy(false);
    }
  }

  /** New recovery codes are shown at once: it is the only time they can be. */
  function showCodesOr(codes: string[] | null, done: string) {
    if (codes && codes.length > 0) setOpen({ kind: "codes", codes });
    else {
      setOpen(null);
      toast.success(done);
    }
  }

  // ── authenticator app ───────────────────────────────────────────────

  const setUpAuthenticator = () =>
    run(async () => {
      const setup = await twoStepApi.beginTotp();
      setCode("");
      setPassword("");
      setOpen({ kind: "authenticator", setup });
    }, "Could not start the setup.");

  const confirmAuthenticator = (e: React.FormEvent) => {
    e.preventDefault();
    return run(async () => {
      const { recovery_codes } = await twoStepApi.confirmTotp(code.trim(), password);
      await changed();
      showCodesOr(recovery_codes, "Authenticator app added");
    }, "That code is not right.");
  };

  function removeAuthenticator() {
    if (!summary) return;
    const passkeys = summary.passkeys.length;
    setPrompt({
      title: "Remove the authenticator app?",
      description:
        passkeys > 0
          ? `You still have ${passkeys} passkey${passkeys === 1 ? "" : "s"}, so two-step verification stays on.`
          : "This turns two-step verification off and deletes your recovery codes.",
      confirmLabel: "Remove",
      destructive: true,
      action: async (password) => {
        await changed(await twoStepApi.removeTotp(password));
        toast.success("Authenticator app removed");
      },
    });
  }

  // ── passkeys ────────────────────────────────────────────────────────

  const addPasskey = () =>
    run(async () => {
      const { options, ticket } = await twoStepApi.passkeyRegistrationOptions();
      const credential = await createPasskey(options);
      setName("My passkey");
      setPassword("");
      setOpen({ kind: "passkey-name", ticket, credential });
    }, "That passkey was not accepted.");

  const savePasskey = (e: React.FormEvent) => {
    e.preventDefault();
    if (open?.kind !== "passkey-name") return;
    const { ticket, credential } = open;
    return run(async () => {
      const { recovery_codes } = await twoStepApi.addPasskey(
        ticket,
        credential,
        name.trim(),
        password,
      );
      await changed();
      showCodesOr(recovery_codes, "Passkey added");
    }, "That passkey was not accepted.");
  };

  const renamePasskey = (e: React.FormEvent) => {
    e.preventDefault();
    if (open?.kind !== "rename") return;
    const { passkey } = open;
    return run(async () => {
      await twoStepApi.renamePasskey(passkey.id, name.trim());
      await load();
      setOpen(null);
    }, "Could not rename the passkey.");
  };

  function removePasskey(passkey: PasskeyInfo) {
    if (!summary) return;
    const others = summary.passkeys.length - 1 + (summary.totp ? 1 : 0);
    setPrompt({
      title: `Remove "${passkey.name}"?`,
      description:
        others > 0
          ? "You have another method, so two-step verification stays on."
          : "This turns two-step verification off and deletes your recovery codes.",
      confirmLabel: "Remove",
      destructive: true,
      action: async (password) => {
        await changed(await twoStepApi.removePasskey(passkey.id, password));
        toast.success("Passkey removed");
      },
    });
  }

  // ── recovery codes ──────────────────────────────────────────────────

  function regenerateCodes() {
    setPrompt({
      title: "Generate new recovery codes?",
      description: "The codes you have now stop working. Save the new ones somewhere safe.",
      confirmLabel: "Generate",
      action: async (password) => {
        const { recovery_codes } = await twoStepApi.regenerateRecoveryCodes(password);
        await load();
        setOpen({ kind: "codes", codes: recovery_codes ?? [] });
      },
    });
  }

  if (!summary) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  const passkeyProblem = passkeyUnavailableReason(summary.passkey_origin);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2">
                <ShieldCheck className="h-5 w-5" />
                Two-step verification
              </CardTitle>
              <CardDescription>
                After your password, sign-in asks for a code or a passkey.
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              {summary.required && (
                <Badge variant="warning">Required by your administrator</Badge>
              )}
              <Badge variant={summary.enabled ? "success" : "pending"}>
                {summary.enabled ? "On" : "Off"}
              </Badge>
            </div>
          </div>
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Smartphone className="h-4 w-4" />
            Authenticator app
          </CardTitle>
          <CardDescription>A 6-digit code from an app on your phone.</CardDescription>
        </CardHeader>
        <CardContent>
          {summary.totp ? (
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm">
                Set up
                <p className="text-xs text-muted-foreground">
                  Added {day(summary.totp.confirmed_at)} · last used{" "}
                  {day(summary.totp.last_used_at)}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="text-destructive"
                onClick={removeAuthenticator}
                disabled={busy}
              >
                Remove
              </Button>
            </div>
          ) : (
            <Button size="sm" onClick={setUpAuthenticator} disabled={busy}>
              Set up
            </Button>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Fingerprint className="h-4 w-4" />
            Passkeys
          </CardTitle>
          <CardDescription>
            Fingerprint, face, device PIN or a security key. You can add several.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {summary.passkeys.map((passkey) => (
            <div
              key={passkey.id}
              className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5"
            >
              <div className="min-w-0 text-sm">
                <p className="truncate font-medium">{passkey.name}</p>
                <p className="text-xs text-muted-foreground">
                  Added {day(passkey.created_at)} · last used {day(passkey.last_used_at)}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setName(passkey.name);
                    setOpen({ kind: "rename", passkey });
                  }}
                  disabled={busy}
                >
                  Rename
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="text-destructive"
                  onClick={() => removePasskey(passkey)}
                  disabled={busy}
                >
                  Remove
                </Button>
              </div>
            </div>
          ))}
          {passkeyProblem ? (
            <p className="text-xs text-muted-foreground">{passkeyProblem}</p>
          ) : (
            <Button size="sm" onClick={addPasskey} disabled={busy}>
              Add a passkey
            </Button>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <LifeBuoy className="h-4 w-4" />
            Recovery codes
          </CardTitle>
          <CardDescription>
            For when you have neither your phone nor a passkey. Each works once.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {summary.recovery_codes ? (
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm">
                {summary.recovery_codes.left} of 10 left
                <p className="text-xs text-muted-foreground">
                  Generated {day(summary.recovery_codes.generated_at)}
                </p>
              </div>
              <Button variant="outline" size="sm" onClick={regenerateCodes} disabled={busy}>
                Generate new codes
              </Button>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              You get ten codes when you add your first method.
            </p>
          )}
        </CardContent>
      </Card>

      <PasswordDialog prompt={prompt} onClose={() => setPrompt(null)} />

      <Dialog
        open={open !== null}
        onOpenChange={(next) => {
          // Recovery codes are closed with their own button, once saved.
          if (!next && open?.kind !== "codes") setOpen(null);
        }}
      >
        <DialogContent>
          {open?.kind === "authenticator" && (
            <form onSubmit={confirmAuthenticator}>
              <DialogHeader>
                <DialogTitle>Set up an authenticator app</DialogTitle>
                <DialogDescription>
                  Scan the code with your app, then enter the 6 digits it shows.
                </DialogDescription>
              </DialogHeader>
              <AuthenticatorSetup
                setup={open.setup}
                code={code}
                onCodeChange={setCode}
                className="pt-4"
              />
              <div className="pb-4 pt-3">
                <PasswordField value={password} onChange={setPassword} />
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setOpen(null)}>
                  Cancel
                </Button>
                <Button type="submit" disabled={busy || !code.trim() || !password}>
                  Confirm
                </Button>
              </DialogFooter>
            </form>
          )}

          {(open?.kind === "passkey-name" || open?.kind === "rename") && (
            <form onSubmit={open.kind === "rename" ? renamePasskey : savePasskey}>
              <DialogHeader>
                <DialogTitle>
                  {open.kind === "rename" ? "Rename passkey" : "Name this passkey"}
                </DialogTitle>
                <DialogDescription>
                  {open.kind === "rename"
                    ? "A name you will recognise it by."
                    : "Your browser has created the passkey. Give it a name you will recognise."}
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4 py-4">
                <div className="space-y-2">
                  <label htmlFor="passkey-name" className="text-sm font-medium">
                    Name
                  </label>
                  <Input
                    id="passkey-name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    maxLength={100}
                    autoFocus
                  />
                </div>
                {open.kind === "passkey-name" && (
                  <PasswordField value={password} onChange={setPassword} />
                )}
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setOpen(null)}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={busy || !name.trim() || (open.kind === "passkey-name" && !password)}
                >
                  Save
                </Button>
              </DialogFooter>
            </form>
          )}

          {open?.kind === "codes" && (
            <>
              <DialogHeader>
                <DialogTitle>Save your recovery codes</DialogTitle>
                <DialogDescription>
                  They are shown only once. Each works once, in place of a code or a
                  passkey.
                </DialogDescription>
              </DialogHeader>
              <RecoveryCodes codes={open.codes} className="py-4" />
              <DialogFooter>
                <Button type="button" onClick={() => setOpen(null)}>
                  I have saved them
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
