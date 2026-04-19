"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/auth";

export default function HomePage() {
  const router = useRouter();
  const { accessToken, isLoading } = useAuthStore();

  useEffect(() => {
    if (isLoading) return;
    if (accessToken) {
      router.replace("/dashboard");
    } else {
      router.replace("/login");
    }
  }, [accessToken, isLoading, router]);

  return (
    <div className="flex h-screen items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
    </div>
  );
}
