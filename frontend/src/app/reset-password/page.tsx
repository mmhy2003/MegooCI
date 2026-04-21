"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { authApi } from "@/lib/api";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { CheckCircle2 } from "lucide-react";

function ResetPasswordForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  const [password, setPassword] = React.useState("");
  const [confirmPassword, setConfirmPassword] = React.useState("");
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [success, setSuccess] = React.useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) {
      toast.error("Missing reset token");
      return;
    }
    if (!password || !confirmPassword) {
      toast.error("Please fill in all fields");
      return;
    }
    if (password !== confirmPassword) {
      toast.error("Passwords do not match");
      return;
    }
    if (password.length < 8) {
      toast.error("Password must be at least 8 characters");
      return;
    }
    setIsSubmitting(true);
    try {
      await authApi.resetPassword(token, password);
      setSuccess(true);
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ??
        (err instanceof Error ? err.message : "Failed to reset password");
      toast.error(detail);
    } finally {
      setIsSubmitting(false);
    }
  }

  if (!token) {
    return (
      <Card className="relative z-10 w-full max-w-md shadow-xl">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl">Invalid link</CardTitle>
          <CardDescription>
            This password reset link is invalid or has expired. Please request a
            new one.
          </CardDescription>
        </CardHeader>
        <CardFooter className="flex flex-col gap-4">
          <Link href="/forgot-password" className="w-full">
            <Button className="w-full">Request new link</Button>
          </Link>
        </CardFooter>
      </Card>
    );
  }

  if (success) {
    return (
      <Card className="relative z-10 w-full max-w-md shadow-xl">
        <CardHeader className="text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-green-100 dark:bg-green-900/30">
            <CheckCircle2 className="h-6 w-6 text-green-600 dark:text-green-400" />
          </div>
          <CardTitle className="text-2xl">Password reset</CardTitle>
          <CardDescription>
            Your password has been updated successfully. You can now sign in
            with your new password.
          </CardDescription>
        </CardHeader>
        <CardFooter className="flex flex-col gap-4">
          <Button className="w-full" onClick={() => router.push("/login")}>
            Sign in
          </Button>
        </CardFooter>
      </Card>
    );
  }

  return (
    <Card className="relative z-10 w-full max-w-md shadow-xl">
      <CardHeader className="text-center">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/icons/icon.svg"
          alt="MegooCI"
          className="mx-auto mb-4 h-12 w-12 rounded-xl shadow-lg"
        />
        <CardTitle className="text-2xl">Set new password</CardTitle>
        <CardDescription>
          Choose a new password for your account.
        </CardDescription>
      </CardHeader>

      <form onSubmit={handleSubmit}>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <label htmlFor="password" className="text-sm font-medium">
              New password
            </label>
            <Input
              id="password"
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              autoFocus
            />
          </div>

          <div className="space-y-2">
            <label htmlFor="confirmPassword" className="text-sm font-medium">
              Confirm new password
            </label>
            <Input
              id="confirmPassword"
              type="password"
              placeholder="••••••••"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
            />
          </div>
        </CardContent>

        <CardFooter className="flex flex-col gap-4">
          <Button type="submit" className="w-full" disabled={isSubmitting}>
            {isSubmitting ? "Resetting..." : "Reset password"}
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            Remember your password?{" "}
            <Link
              href="/login"
              className="font-medium text-primary hover:underline"
            >
              Sign in
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  );
}

export default function ResetPasswordPage() {
  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-gradient-to-br from-violet-50 via-fuchsia-50/40 to-cyan-50/50 dark:from-[#0a0118] dark:via-[#0d0026] dark:to-[#001a1f] px-4">
      <div className="pointer-events-none absolute -top-32 -left-32 h-96 w-96 rounded-full bg-primary/10 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-32 -right-32 h-96 w-96 rounded-full bg-fuchsia-500/10 blur-3xl" />

      <div className="absolute right-4 top-4 z-20">
        <ThemeToggle variant="icon" />
      </div>

      <React.Suspense
        fallback={
          <Card className="relative z-10 w-full max-w-md shadow-xl">
            <CardHeader className="text-center">
              <CardTitle className="text-2xl">Loading...</CardTitle>
            </CardHeader>
          </Card>
        }
      >
        <ResetPasswordForm />
      </React.Suspense>
    </div>
  );
}
