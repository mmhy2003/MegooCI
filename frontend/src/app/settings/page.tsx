"use client";

import * as React from "react";
import { toast } from "sonner";
import {
  User,
  Monitor,
  Sparkles,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  KeyRound,
  HardDrive,
  Shield,
  Package,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { useAuthStore } from "@/lib/auth";
import { systemApi, type AiInfo, type SystemInfo } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

function AiStatusBadge({ ai }: { ai: AiInfo }) {
  if (ai.status === "ready") {
    return (
      <Badge variant="success" className="gap-1">
        <CheckCircle2 className="h-3 w-3" /> Ready
      </Badge>
    );
  }
  if (ai.status === "disabled") {
    return (
      <Badge variant="cancelled" className="gap-1">
        <XCircle className="h-3 w-3" /> Disabled
      </Badge>
    );
  }
  if (ai.status === "missing_api_key") {
    return (
      <Badge variant="failed" className="gap-1">
        <KeyRound className="h-3 w-3" /> Missing API key
      </Badge>
    );
  }
  return (
    <Badge variant="failed" className="gap-1">
      <AlertTriangle className="h-3 w-3" /> Misconfigured
    </Badge>
  );
}

function ConfigRow({
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

export default function SettingsPage() {
  const { user } = useAuthStore();
  const [info, setInfo] = React.useState<SystemInfo | null>(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    systemApi
      .info()
      .then(setInfo)
      .catch(() => toast.error("Failed to load system configuration"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <AppLayout>
      <div className="mx-auto max-w-3xl space-y-8">
        <div>
          <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
            Settings
          </h1>
          <p className="text-sm text-muted-foreground sm:text-base">
            Manage your profile and view the current system configuration.
          </p>
        </div>

        {/* Profile */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <User className="h-5 w-5" />
              Profile
            </CardTitle>
            <CardDescription>
              Your personal account information.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Full name</label>
              <Input value={user?.name || ""} readOnly className="bg-muted" />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Email</label>
              <Input value={user?.email || ""} readOnly className="bg-muted" />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Role</label>
              <div>
                <Badge variant="secondary">
                  {user?.is_admin ? "Admin" : "User"}
                </Badge>
              </div>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Account status</label>
              <div>
                <Badge variant={user?.is_active ? "success" : "failed"}>
                  {user?.is_active ? "Active" : "Inactive"}
                </Badge>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* AI Configuration */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5" />
              AI Configuration
            </CardTitle>
            <CardDescription>
              Detected from the controller&apos;s environment.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {loading || !info ? (
              <div className="space-y-3">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : (
              <>
                <div className="space-y-3">
                  <ConfigRow label="Status">
                    <AiStatusBadge ai={info.ai} />
                  </ConfigRow>
                  <ConfigRow label="Enabled (MEGOOCI_AI_ENABLED)">
                    <Badge
                      variant={info.ai.enabled ? "success" : "cancelled"}
                    >
                      {info.ai.enabled ? "Yes" : "No"}
                    </Badge>
                  </ConfigRow>
                  <ConfigRow label="Provider">
                    <code className="rounded bg-muted px-2 py-0.5 text-xs">
                      {info.ai.provider || "—"}
                    </code>
                  </ConfigRow>
                  <ConfigRow label="Model">
                    <code className="rounded bg-muted px-2 py-0.5 text-xs">
                      {info.ai.model || "—"}
                    </code>
                  </ConfigRow>
                  <ConfigRow label="API Key">
                    <Badge
                      variant={info.ai.has_api_key ? "success" : "cancelled"}
                    >
                      {info.ai.has_api_key ? "Set" : "Not set"}
                    </Badge>
                  </ConfigRow>
                  {info.ai.base_url && (
                    <ConfigRow label="Base URL">
                      <code className="rounded bg-muted px-2 py-0.5 text-xs">
                        {info.ai.base_url}
                      </code>
                    </ConfigRow>
                  )}
                </div>
                <p
                  className={`mt-4 text-xs ${
                    info.ai.status === "ready"
                      ? "text-muted-foreground"
                      : "text-amber-600 dark:text-amber-400"
                  }`}
                >
                  {info.ai.status_detail}
                </p>
                {info.ai.status !== "ready" && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Update <code>MEGOOCI_AI_*</code> in your{" "}
                    <code>.env</code> file and restart the backend to change
                    these values.
                  </p>
                )}
              </>
            )}
          </CardContent>
        </Card>

        {/* System */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Monitor className="h-5 w-5" />
              System
            </CardTitle>
            <CardDescription>
              Current runtime configuration from the backend.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {loading || !info ? (
              <div className="space-y-3">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : (
              <div className="space-y-3">
                <ConfigRow label="Version">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.version}
                  </code>
                </ConfigRow>
                <ConfigRow label="Public URL">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.public_url}
                  </code>
                </ConfigRow>
                <ConfigRow label="API URL (browser)">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {process.env.NEXT_PUBLIC_API_URL || "/api"}
                  </code>
                </ConfigRow>
                <ConfigRow label="Log level">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.log_level}
                  </code>
                </ConfigRow>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Authentication */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Shield className="h-5 w-5" />
              Authentication
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading || !info ? (
              <div className="space-y-3">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : (
              <div className="space-y-3">
                <ConfigRow label="Public signup">
                  <Badge
                    variant={info.auth.signup_enabled ? "success" : "cancelled"}
                  >
                    {info.auth.signup_enabled ? "Enabled" : "Disabled"}
                  </Badge>
                </ConfigRow>
                <ConfigRow label="Default role for new signups">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.auth.default_role}
                  </code>
                </ConfigRow>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Storage */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <HardDrive className="h-5 w-5" />
              Storage
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading || !info ? (
              <div className="space-y-3">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : (
              <div className="space-y-3">
                <ConfigRow label="Storage root">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.storage.storage_root}
                  </code>
                </ConfigRow>
                <ConfigRow label="Retention (builds)">
                  <span>{info.storage.retention_builds} builds</span>
                </ConfigRow>
                <ConfigRow label="Retention (days)">
                  <span>{info.storage.retention_days} days</span>
                </ConfigRow>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Registry */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Package className="h-5 w-5" />
              Container Registry
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading || !info ? (
              <div className="space-y-3">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : (
              <div className="space-y-3">
                <ConfigRow label="Status">
                  <Badge
                    variant={info.registry.enabled ? "success" : "cancelled"}
                  >
                    {info.registry.enabled ? "Enabled" : "Disabled"}
                  </Badge>
                </ConfigRow>
                <ConfigRow label="Host">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.registry.host}
                  </code>
                </ConfigRow>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}
