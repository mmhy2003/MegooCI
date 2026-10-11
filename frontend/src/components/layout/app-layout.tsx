"use client";

import * as React from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/auth";
import { Sidebar } from "./sidebar";
import { Header } from "./header";
import { CommandPalette } from "@/components/command-palette";
import { NotificationProvider } from "@/contexts/notification-context";
import { systemApi } from "@/lib/api";
import { AlertTriangle, ShieldAlert } from "lucide-react";

export function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { accessToken, isLoading, loadUser, user } = useAuthStore();
  const [mobileOpen, setMobileOpen] = React.useState(false);
  const [searchOpen, setSearchOpen] = React.useState(false);
  const [maintenance, setMaintenance] = React.useState<{
    enabled: boolean;
    message: string | null;
  } | null>(null);

  React.useEffect(() => {
    loadUser();
  }, [loadUser]);

  React.useEffect(() => {
    if (!isLoading && !accessToken) {
      const redirectParam = pathname && pathname !== "/" ? `?redirect=${encodeURIComponent(pathname)}` : "";
      router.replace(`/login${redirectParam}`);
    }
  }, [isLoading, accessToken, router, pathname]);

  // Fetch maintenance status once on mount and every 60s
  React.useEffect(() => {
    if (!accessToken) return;
    let cancelled = false;

    async function check() {
      try {
        const info = await systemApi.info();
        if (!cancelled) setMaintenance(info.maintenance);
      } catch {
        // ignore — non-critical
      }
    }

    check();
    const interval = setInterval(check, 60_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [accessToken]);

  React.useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  React.useEffect(() => {
    if (mobileOpen) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = prev;
      };
    }
  }, [mobileOpen]);

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  if (!accessToken) return null;

  return (
    <NotificationProvider>
      <div className="flex h-screen overflow-hidden">
        <Sidebar
          mobileOpen={mobileOpen}
          onCloseMobile={() => setMobileOpen(false)}
        />
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <Header
            onOpenMobile={() => setMobileOpen(true)}
            onOpenSearch={() => setSearchOpen(true)}
          />
          {maintenance?.enabled && (
            <div className="flex items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm text-amber-700 dark:text-amber-300">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span className="font-medium">Maintenance Mode</span>
              {maintenance.message && (
                <span className="text-amber-600 dark:text-amber-400">
                  — {maintenance.message}
                </span>
              )}
              <span className="ml-auto text-xs text-amber-500">
                Builds are paused
              </span>
            </div>
          )}
          {user?.two_step_setup_required && (
            <div className="flex items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm text-amber-700 dark:text-amber-300">
              <ShieldAlert className="h-4 w-4 shrink-0" />
              <span className="font-medium">
                Your administrator requires two-step verification.
              </span>
              <span className="hidden text-amber-600 dark:text-amber-400 sm:inline">
                Set it up now, or you will be signed out and asked to.
              </span>
              {/* A plain link: the settings page reads its tab from the
                  address when it loads, also when it is already open. */}
              {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
              <a
                href="/settings?tab=two-step"
                className="ml-auto shrink-0 font-medium underline underline-offset-2"
              >
                Set it up
              </a>
            </div>
          )}
          <main className="flex-1 overflow-auto bg-background p-4 sm:p-6">
            {children}
          </main>
        </div>

        <CommandPalette open={searchOpen} onOpenChange={setSearchOpen} />
      </div>
    </NotificationProvider>
  );
}
