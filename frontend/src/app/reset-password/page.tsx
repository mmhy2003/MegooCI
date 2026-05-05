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
import { CheckCircle2, Eye, EyeOff, Lock, KeyRound } from "lucide-react";

function ResetPasswordForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  const [password, setPassword] = React.useState("");
  const [confirmPassword, setConfirmPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [showConfirm, setShowConfirm] = React.useState(false);
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
      <Card className="relative z-10 w-full max-w-md overflow-hidden border-0 shadow-2xl dark:border dark:border-white/[0.08]">
        <div className="h-1 w-full bg-gradient-to-r from-primary via-fuchsia-500 to-cyan-400" />
        <CardHeader className="text-center">
          <CardTitle className="text-2xl">Invalid link</CardTitle>
          <CardDescription>
            This password reset link is invalid or has expired. Please request a
            new one.
          </CardDescription>
        </CardHeader>
        <CardFooter className="flex flex-col gap-4 px-8 pb-8">
          <Link href="/forgot-password" className="w-full">
            <Button className="w-full">Request new link</Button>
          </Link>
        </CardFooter>
      </Card>
    );
  }

  if (success) {
    return (
      <Card className="relative z-10 w-full max-w-md overflow-hidden border-0 shadow-2xl dark:border dark:border-white/[0.08]">
        <div className="h-1 w-full bg-gradient-to-r from-primary via-fuchsia-500 to-cyan-400" />
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
        <CardFooter className="flex flex-col gap-4 px-8 pb-8">
          <Button className="w-full" onClick={() => router.push("/login")}>
            Sign in
          </Button>
        </CardFooter>
      </Card>
    );
  }

  return (
    <Card className="relative z-10 w-full max-w-md overflow-hidden border-0 shadow-2xl dark:border dark:border-white/[0.08]">
      {/* Decorative top accent bar */}
      <div className="h-1 w-full bg-gradient-to-r from-primary via-fuchsia-500 to-cyan-400" />

      <CardHeader className="space-y-3 pb-2 pt-8 text-center">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/icons/icon.svg"
          alt="MegooCI"
          className="mx-auto mb-2 h-14 w-14 rounded-2xl shadow-lg ring-4 ring-primary/10"
        />
        <CardTitle className="text-2xl font-bold tracking-tight">
          Set new password
        </CardTitle>
        <CardDescription className="text-sm">
          Choose a new password for your account.
        </CardDescription>
      </CardHeader>

      <form onSubmit={handleSubmit}>
        <CardContent className="space-y-5 px-8">
          {/* New password */}
          <div className="space-y-2">
            <label htmlFor="reset-password" className="text-sm font-medium">
              New password
            </label>
            <div className="relative">
              <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="reset-password"
                type={showPassword ? "text" : "password"}
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                autoFocus
                className="h-10 pl-9 pr-10"
              />
              <button
                type="button"
                tabIndex={-1}
                onClick={() => setShowPassword((prev) => !prev)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground focus:outline-none"
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>

          {/* Confirm new password */}
          <div className="space-y-2">
            <label htmlFor="reset-confirmPassword" className="text-sm font-medium">
              Confirm new password
            </label>
            <div className="relative">
              <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="reset-confirmPassword"
                type={showConfirm ? "text" : "password"}
                placeholder="••••••••"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                autoComplete="new-password"
                className="h-10 pl-9 pr-10"
              />
              <button
                type="button"
                tabIndex={-1}
                onClick={() => setShowConfirm((prev) => !prev)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground focus:outline-none"
                aria-label={showConfirm ? "Hide password" : "Show password"}
              >
                {showConfirm ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>
        </CardContent>

        <CardFooter className="flex flex-col gap-4 px-8 pb-8">
          <Button
            type="submit"
            className="h-10 w-full gap-2 text-sm font-semibold shadow-md transition-shadow hover:shadow-lg"
            disabled={isSubmitting}
          >
            {isSubmitting ? (
              <>
                <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                Resetting…
              </>
            ) : (
              <>
                <KeyRound className="h-4 w-4" />
                Reset password
              </>
            )}
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
      {/* Ambient glow blobs */}
      <div className="pointer-events-none absolute -top-40 -left-40 h-[30rem] w-[30rem] rounded-full bg-primary/10 blur-[100px]" />
      <div className="pointer-events-none absolute -bottom-40 -right-40 h-[30rem] w-[30rem] rounded-full bg-fuchsia-500/10 blur-[100px]" />
      <div className="pointer-events-none absolute top-1/3 left-1/2 h-64 w-64 -translate-x-1/2 rounded-full bg-cyan-400/5 blur-[80px]" />

      <div className="absolute right-4 top-4 z-20">
        <ThemeToggle variant="icon" />
      </div>

      <React.Suspense
        fallback={
          <Card className="relative z-10 w-full max-w-md overflow-hidden border-0 shadow-2xl dark:border dark:border-white/[0.08]">
            <div className="h-1 w-full bg-gradient-to-r from-primary via-fuchsia-500 to-cyan-400" />
            <CardHeader className="text-center">
              <CardTitle className="text-2xl">Loading…</CardTitle>
            </CardHeader>
          </Card>
        }
      >
        <ResetPasswordForm />
      </React.Suspense>
    </div>
  );
}
