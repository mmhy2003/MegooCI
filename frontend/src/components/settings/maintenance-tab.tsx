"use client";

import * as React from "react";
import { toast } from "sonner";
import { AlertTriangle, Save, Wrench } from "lucide-react";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { systemApi, type MaintenanceInfo, type SystemInfo } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

export function MaintenanceCard({
  info,
  loading,
  onUpdated,
}: {
  info: SystemInfo | null;
  loading: boolean;
  onUpdated: (m: MaintenanceInfo) => void;
}) {
  const [saving, setSaving] = React.useState(false);
  const [message, setMessage] = React.useState("");
  const confirm = useConfirm();

  const enabled = info?.maintenance?.enabled ?? false;
  const currentMessage = info?.maintenance?.message ?? "";

  React.useEffect(() => {
    setMessage(currentMessage);
  }, [currentMessage]);

  async function handleToggle(newEnabled: boolean) {
    if (newEnabled) {
      const ok = await confirm({
        title: "Enable Maintenance Mode",
        description:
          "All pending builds will be paused. New builds will still be queued but won't execute until maintenance mode is disabled. Continue?",
        confirmText: "Enable",
      });
      if (!ok) return;
    }

    setSaving(true);
    try {
      const result = await systemApi.setMaintenance({
        enabled: newEnabled,
        message: newEnabled ? message || null : null,
      });
      onUpdated(result);
      toast.success(
        newEnabled
          ? "Maintenance mode enabled — builds are paused"
          : "Maintenance mode disabled — queued builds will resume"
      );
    } catch (err: unknown) {
      toast.error(
        err instanceof Error ? err.message : "Failed to toggle maintenance mode"
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleUpdateMessage() {
    setSaving(true);
    try {
      const result = await systemApi.setMaintenance({
        enabled: true,
        message: message || null,
      });
      onUpdated(result);
      toast.success("Maintenance message updated");
    } catch {
      toast.error("Failed to update message");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className={enabled ? "border-amber-500/50 dark:border-amber-400/30" : ""}>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Wrench className={`h-5 w-5 ${enabled ? "text-amber-500" : ""}`} />
              Maintenance Mode
            </CardTitle>
            <CardDescription>
              Pause all build execution while performing system maintenance.
            </CardDescription>
          </div>
          <div className="flex items-center gap-3">
            {!loading && info && (
              <>
                <Badge variant={enabled ? "warning" : "success"}>
                  {enabled ? "Active" : "Off"}
                </Badge>
                <button
                  type="button"
                  role="switch"
                  aria-checked={enabled}
                  disabled={saving}
                  onClick={() => handleToggle(!enabled)}
                  className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${
                    enabled ? "bg-amber-500" : "bg-muted"
                  }`}
                >
                  <span
                    className={`pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg ring-0 transition-transform ${
                      enabled ? "translate-x-5" : "translate-x-0"
                    }`}
                  />
                </button>
              </>
            )}
          </div>
        </div>
      </CardHeader>
      {enabled && (
        <CardContent>
          <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-4">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
              <div className="flex-1 space-y-3">
                <p className="text-sm text-amber-700 dark:text-amber-300">
                  Build execution is paused. New builds are queued as{" "}
                  <span className="font-medium">pending</span> and will start
                  automatically once maintenance mode is disabled.
                </p>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">
                    Maintenance message (optional)
                  </label>
                  <div className="flex gap-2">
                    <Input
                      value={message}
                      onChange={(e) => setMessage(e.target.value)}
                      placeholder="e.g. Database migration in progress..."
                      className="h-8 text-xs"
                    />
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-8 shrink-0"
                      disabled={saving || message === currentMessage}
                      onClick={handleUpdateMessage}
                    >
                      Save
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </CardContent>
      )}
    </Card>
  );
}
