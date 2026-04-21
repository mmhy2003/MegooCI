"use client";

import * as React from "react";
import Link from "next/link";
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
import { MailCheck } from "lucide-react";

export default function ForgotPasswordPage() {
  const [email, setEmail] = React.useState("");
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [submitted, setSubmitted] = React.useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email) {
      toast.error("Please enter your email address");
      return;
    }
    setIsSubmitting(true);
    try {
      await authApi.forgotPassword(email);
      setSubmitted(true);
    } catch {
      setSubmitted(true);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-gradient-to-br from-violet-50 via-fuchsia-50/40 to-cyan-50/50 dark:from-[#0a0118] dark:via-[#0d0026] dark:to-[#001a1f] px-4">
      <div className="pointer-events-none absolute -top-32 -left-32 h-96 w-96 rounded-full bg-primary/10 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-32 -right-32 h-96 w-96 rounded-full bg-fuchsia-500/10 blur-3xl" />

      <div className="absolute right-4 top-4 z-20">
        <ThemeToggle variant="icon" />
      </div>

      <Card className="relative z-10 w-full max-w-md shadow-xl">
        {submitted ? (
          <>
            <CardHeader className="text-center">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
                <MailCheck className="h-6 w-6 text-primary" />
              </div>
              <CardTitle className="text-2xl">Check your email</CardTitle>
              <CardDescription>
                If an account with that email exists, we&apos;ve sent a
                password reset link. Check your inbox (and spam folder).
              </CardDescription>
            </CardHeader>
            <CardFooter className="flex flex-col gap-4">
              <Link href="/login" className="w-full">
                <Button variant="outline" className="w-full">
                  Back to sign in
                </Button>
              </Link>
            </CardFooter>
          </>
        ) : (
          <>
            <CardHeader className="text-center">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/icons/icon.svg"
                alt="MegooCI"
                className="mx-auto mb-4 h-12 w-12 rounded-xl shadow-lg"
              />
              <CardTitle className="text-2xl">Forgot password?</CardTitle>
              <CardDescription>
                Enter your email and we&apos;ll send you a link to reset your
                password.
              </CardDescription>
            </CardHeader>

            <form onSubmit={handleSubmit}>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <label htmlFor="email" className="text-sm font-medium">
                    Email
                  </label>
                  <Input
                    id="email"
                    type="email"
                    placeholder="you@example.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    autoComplete="email"
                    autoFocus
                  />
                </div>
              </CardContent>

              <CardFooter className="flex flex-col gap-4">
                <Button
                  type="submit"
                  className="w-full"
                  disabled={isSubmitting}
                >
                  {isSubmitting ? "Sending..." : "Send reset link"}
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
          </>
        )}
      </Card>
    </div>
  );
}
