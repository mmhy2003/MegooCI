"use client";

import * as React from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/auth";
import { Sidebar } from "./sidebar";
import { Header } from "./header";

export function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { accessToken, isLoading, loadUser } = useAuthStore();
  const [mobileOpen, setMobileOpen] = React.useState(false);

  React.useEffect(() => {
    loadUser();
  }, [loadUser]);

  React.useEffect(() => {
    if (!isLoading && !accessToken) {
      router.replace("/login");
    }
  }, [isLoading, accessToken, router]);

  // Close the mobile drawer whenever the route changes.
  React.useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  // Prevent body scroll while the mobile drawer is open.
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
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        mobileOpen={mobileOpen}
        onCloseMobile={() => setMobileOpen(false)}
      />
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <Header onOpenMobile={() => setMobileOpen(true)} />
        <main className="flex-1 overflow-auto bg-background p-4 sm:p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
