"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { ShieldCheck } from "lucide-react";
import { systemApi, type SecuritySettings } from "@/lib/api";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/** Settings → Server → Security, for administrators. */
export function SecurityTab() {
  const [settings, setSettings] = React.useState<SecuritySettings | null>(null);
  const [saving, setSaving] = React.useState(false);
  const confirm = useConfirm();

  React.useEffect(() => {
    systemApi
      .security()
      .then(setSettings)
      .catch((error: unknown) =>
        toast.error(error instanceof Error ? error.message : "Failed to load security settings"),
      );
  }, []);

  async function toggle(required: boolean) {
    if (!settings) return;
    const without = settings.users_total - settings.users_with_two_step;
    if (required) {
      const ok = await confirm({
        title: "Require two-step verification?",
        description:
          without > 0
            ? `${without} of ${settings.users_total} users have not set it up. They see a notice now, and within 12 hours they are signed out and set it up when they sign in again.`
            : "Every user already has it on. New accounts will set it up before their first sign-in.",
        confirmText: "Require",
        tone: "warning",
      });
      if (!ok) return;
    }
    setSaving(true);
    try {
      setSettings(await systemApi.setSecurity(required));
      toast.success(
        required
          ? "Two-step verification is now required"
          : "Two-step verification is no longer required",
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  if (!settings) return <Skeleton className="h-40 w-full" />;

  const required = settings.require_two_step;
  const without = settings.users_total - settings.users_with_two_step;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5" />
          Security
        </CardTitle>
        <CardDescription>How accounts on this server sign in.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div className="text-sm">
            <p className="font-medium">Require two-step verification</p>
            <p className="text-xs text-muted-foreground">
              Everyone must set it up: new accounts before their first sign-in, existing
              ones within 12 hours. You need it on your own account first.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={required}
            aria-label="Require two-step verification"
            disabled={saving}
            onClick={() => toggle(!required)}
            className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${
              required ? "bg-primary" : "bg-muted"
            }`}
          >
            <span
              className={`pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg ring-0 transition-transform ${
                required ? "translate-x-5" : "translate-x-0"
              }`}
            />
          </button>
        </div>

        <div className="flex items-center justify-between gap-4 border-t pt-4">
          <div className="text-sm">
            <p className="font-medium">
              {settings.users_with_two_step} of {settings.users_total} users have it on
            </p>
            <p className="text-xs text-muted-foreground">
              {without === 0
                ? "Nobody is left to set it up."
                : required
                  ? `The other ${without} will be asked to set it up.`
                  : `The other ${without} sign in with a password only.`}
            </p>
          </div>
          <Link href="/admin/users">
            <Button variant="outline" size="sm">
              View users
            </Button>
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
