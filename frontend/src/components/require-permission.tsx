"use client";

import type { ReactNode } from "react";
import { useAuthStore } from "@/lib/auth";
import { AppLayout } from "@/components/layout/app-layout";

interface RequirePermissionProps {
  permission?: string;
  adminOnly?: boolean;
  children: ReactNode;
  fallback?: ReactNode;
}

const DEFAULT_FALLBACK = (
  <AppLayout>
    <div className="flex items-center justify-center py-24">
      <p className="text-muted-foreground">
        You don&apos;t have permission to view this page.
      </p>
    </div>
  </AppLayout>
);

export function RequirePermission({
  permission,
  adminOnly = false,
  children,
  fallback = DEFAULT_FALLBACK,
}: RequirePermissionProps) {
  const { user } = useAuthStore();

  if (!user) return null;

  if (adminOnly) {
    if (!user.is_admin) return <>{fallback}</>;
    return <>{children}</>;
  }

  if (permission) {
    const hasPermission =
      user.is_admin || (user.permissions?.includes(permission) ?? false);
    if (!hasPermission) return <>{fallback}</>;
  }

  return <>{children}</>;
}

export function RequireAdmin({
  children,
  fallback,
}: {
  children: ReactNode;
  fallback?: ReactNode;
}) {
  return (
    <RequirePermission adminOnly fallback={fallback}>
      {children}
    </RequirePermission>
  );
}
