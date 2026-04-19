"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/auth";
import { Sidebar } from "./sidebar";
import { Header } from "./header";

export function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { accessToken, isLoading, loadUser } = useAuthStore();

  React.useEffect(() => {
    loadUser();
  }, [loadUser]);

  React.useEffect(() => {
    if (!isLoading && !accessToken) {
      router.replace("/login");
    }
  }, [isLoading, accessToken, router]);

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
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-auto bg-background p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
