"use client";

import { Monitor, HardDrive, Shield, Package } from "lucide-react";
import { type SystemInfo } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfigRow } from "@/components/settings/config-row";

/** Read-only: how this server is configured. */
export function SystemTab({ info, loading }: { info: SystemInfo | null; loading: boolean }) {
  return (
    <div className="space-y-8">
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
              <ConfigRow label="Storage Path">
                <code className="rounded bg-muted px-2 py-0.5 text-xs">
                  {info.registry.storage_path}
                </code>
              </ConfigRow>
              <ConfigRow label="Max Upload">
                <span className="text-sm">
                  {info.registry.max_upload_mb} MB
                </span>
              </ConfigRow>
              <ConfigRow label="GC Schedule">
                <code className="rounded bg-muted px-2 py-0.5 text-xs">
                  {info.registry.gc_cron}
                </code>
              </ConfigRow>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
