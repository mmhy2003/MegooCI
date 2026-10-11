"use client";

import * as React from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export interface PasswordPrompt {
  title: string;
  description: string;
  confirmLabel: string;
  destructive?: boolean;
  /** Called with the typed password. Throw to keep the dialog open. */
  action: (password: string) => Promise<void>;
}

/**
 * Asks for the account's password before something that weakens how the
 * account signs in: removing a method, or replacing the recovery codes.
 */
export function PasswordDialog({
  prompt,
  onClose,
}: {
  prompt: PasswordPrompt | null;
  onClose: () => void;
}) {
  const [password, setPassword] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    setPassword("");
  }, [prompt]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!prompt || !password) return;
    setBusy(true);
    try {
      await prompt.action(password);
      onClose();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={prompt !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        {prompt && (
          <form onSubmit={submit}>
            <DialogHeader>
              <DialogTitle>{prompt.title}</DialogTitle>
              <DialogDescription>{prompt.description}</DialogDescription>
            </DialogHeader>
            <div className="space-y-2 py-4">
              <label htmlFor="two-step-password" className="text-sm font-medium">
                Your password
              </label>
              <Input
                id="two-step-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                autoFocus
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose}>
                Cancel
              </Button>
              <Button
                type="submit"
                variant={prompt.destructive ? "destructive" : "default"}
                disabled={busy || !password}
              >
                {prompt.confirmLabel}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
