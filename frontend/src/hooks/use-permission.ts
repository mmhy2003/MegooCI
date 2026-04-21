"use client";

import { useAuthStore } from "@/lib/auth";

/**
 * Check if the current user has a specific permission.
 * Admins implicitly have all permissions.
 */
export function usePermission(permission: string): boolean {
  const { user } = useAuthStore();
  if (!user) return false;
  if (user.is_admin) return true;
  return user.permissions?.includes(permission) ?? false;
}

/**
 * Check multiple permissions — returns true if the user has ANY of them.
 */
export function useAnyPermission(...permissions: string[]): boolean {
  const { user } = useAuthStore();
  if (!user) return false;
  if (user.is_admin) return true;
  return permissions.some((p) => user.permissions?.includes(p));
}

/**
 * Returns a stable checker function for use in event handlers.
 */
export function usePermissionCheck(): (permission: string) => boolean {
  const { user } = useAuthStore();
  return (permission: string) => {
    if (!user) return false;
    if (user.is_admin) return true;
    return user.permissions?.includes(permission) ?? false;
  };
}
