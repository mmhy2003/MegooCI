"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { authApi } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { AuthBackdrop } from "@/components/login/auth-backdrop";
import { AuthCard } from "@/components/login/auth-card";
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
import { Eye, EyeOff, UserPlus, Mail, Lock, User } from "lucide-react";

export default function SignupPage() {
  const router = useRouter();
  const { signup, accessToken, hasHydrated } = useAuthStore();
  const [fullName, setFullName] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [confirmPassword, setConfirmPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [showConfirm, setShowConfirm] = React.useState(false);
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  React.useEffect(() => {
    if (hasHydrated && accessToken) router.replace("/dashboard");
  }, [hasHydrated, accessToken, router]);

  // The server refuses a sign-up it has disabled; say so up front instead of
  // after the form is filled in. If it cannot be asked, the form stays.
  const [signupClosed, setSignupClosed] = React.useState(false);
  React.useEffect(() => {
    authApi
      .signupStatus()
      .then((status) => setSignupClosed(!status.signup_enabled))
      .catch(() => {});
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!fullName || !email || !password) {
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
      await signup(email, password, fullName);
      toast.success("Account created! Welcome to MegooCI.");
      router.push("/dashboard");
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Could not create account";
      toast.error(message);
    } finally {
      setIsSubmitting(false);
    }
  }

  if (signupClosed) {
    return (
      <AuthBackdrop>
        <AuthCard
          title="Sign-up is disabled"
          description="Ask an administrator for an invitation."
        >
          <CardFooter className="px-8 pb-8 pt-4">
            <Link href="/login" className="w-full">
              <Button type="button" className="h-10 w-full text-sm font-semibold">
                Back to sign in
              </Button>
            </Link>
          </CardFooter>
        </AuthCard>
      </AuthBackdrop>
    );
  }

  return (
    <AuthBackdrop>
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
            Create your account
          </CardTitle>
          <CardDescription className="text-sm">
            Get started with MegooCI in seconds
          </CardDescription>
        </CardHeader>

        <form onSubmit={handleSubmit}>
          <CardContent className="space-y-5 px-8">
            {/* Full name */}
            <div className="space-y-2">
              <label htmlFor="signup-fullName" className="text-sm font-medium">
                Full name
              </label>
              <div className="relative">
                <User className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="signup-fullName"
                  placeholder="Jane Doe"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  autoComplete="name"
                  autoFocus
                  className="h-10 pl-9"
                />
              </div>
            </div>

            {/* Email */}
            <div className="space-y-2">
              <label htmlFor="signup-email" className="text-sm font-medium">
                Email
              </label>
              <div className="relative">
                <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="signup-email"
                  type="email"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                  className="h-10 pl-9"
                />
              </div>
            </div>

            {/* Password */}
            <div className="space-y-2">
              <label htmlFor="signup-password" className="text-sm font-medium">
                Password
              </label>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="signup-password"
                  type={showPassword ? "text" : "password"}
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="new-password"
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

            {/* Confirm password */}
            <div className="space-y-2">
              <label htmlFor="signup-confirmPassword" className="text-sm font-medium">
                Confirm password
              </label>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="signup-confirmPassword"
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
                  Creating account…
                </>
              ) : (
                <>
                  <UserPlus className="h-4 w-4" />
                  Create account
                </>
              )}
            </Button>

            <div className="relative flex w-full items-center justify-center py-1">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t" />
              </div>
              <span className="relative bg-card px-3 text-xs text-muted-foreground">
                Already have an account?
              </span>
            </div>

            <Link href="/login" className="w-full">
              <Button
                type="button"
                variant="outline"
                className="h-10 w-full text-sm font-medium"
              >
                Sign in
              </Button>
            </Link>
          </CardFooter>
        </form>
      </Card>
    </AuthBackdrop>
  );
}
