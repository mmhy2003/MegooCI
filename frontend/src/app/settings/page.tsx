"use client";

import * as React from "react";
import {
  User,
  Monitor,
  Sparkles,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { useAuthStore } from "@/lib/auth";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

export default function SettingsPage() {
  const { user } = useAuthStore();

  return (
    <AppLayout>
      <div className="mx-auto max-w-3xl space-y-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
          <p className="text-muted-foreground">
            Manage your profile and system configuration.
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
                <Badge variant="secondary">{user?.is_admin ? "Admin" : "User"}</Badge>
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

        {/* System */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Monitor className="h-5 w-5" />
              System
            </CardTitle>
            <CardDescription>
              Current system configuration values.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-3 text-sm">
              <div className="flex items-center justify-between rounded-lg border px-4 py-3">
                <span className="text-muted-foreground">API URL</span>
                <code className="rounded bg-muted px-2 py-0.5 text-xs">
                  {process.env.NEXT_PUBLIC_API_URL || "/api"}
                </code>
              </div>
              <div className="flex items-center justify-between rounded-lg border px-4 py-3">
                <span className="text-muted-foreground">Environment</span>
                <code className="rounded bg-muted px-2 py-0.5 text-xs">
                  {process.env.NODE_ENV || "development"}
                </code>
              </div>
              <div className="flex items-center justify-between rounded-lg border px-4 py-3">
                <span className="text-muted-foreground">Version</span>
                <code className="rounded bg-muted px-2 py-0.5 text-xs">
                  0.1.0
                </code>
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
              AI-powered pipeline optimization settings.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-3 text-sm">
              <div className="flex items-center justify-between rounded-lg border px-4 py-3">
                <span className="text-muted-foreground">AI Enabled</span>
                <Badge variant="pending">Not configured</Badge>
              </div>
              <div className="flex items-center justify-between rounded-lg border px-4 py-3">
                <span className="text-muted-foreground">Provider</span>
                <span className="text-muted-foreground">—</span>
              </div>
              <div className="flex items-center justify-between rounded-lg border px-4 py-3">
                <span className="text-muted-foreground">
                  Smart suggestions
                </span>
                <Badge variant="pending">Disabled</Badge>
              </div>
            </div>
            <p className="mt-4 text-xs text-muted-foreground">
              AI features will be available in a future release. Configure your
              AI provider in the backend settings to enable smart pipeline
              suggestions and failure analysis.
            </p>
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}
