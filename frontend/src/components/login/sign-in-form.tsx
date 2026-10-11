"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ArrowLeft, Eye, EyeOff, LogIn, Mail, Lock } from "lucide-react";
import { useAuthStore } from "@/lib/auth";
import type { KnownAccount } from "@/lib/known-accounts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CardContent, CardFooter } from "@/components/ui/card";
import { AccountAvatar, AuthCard } from "./auth-card";

/**
 * Email and password, or, for an *account* chosen from the list, its
 * password only.
 */
export function SignInForm({
  account,
  redirectTo,
  showSignup,
  onChooseAccount,
}: {
  account?: KnownAccount;
  redirectTo: string;
  /** Offer "Create an account". Not shown for a chosen account. */
  showSignup: boolean;
  /** Back to the account list; absent when there is no list to go back to. */
  onChooseAccount?: () => void;
}) {
  const router = useRouter();
  const login = useAuthStore((s) => s.login);
  const [typedEmail, setTypedEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const email = account ? account.email : typedEmail;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email || !password) {
      toast.error(
        account ? "Please enter your password" : "Please fill in all fields",
      );
      return;
    }
    setIsSubmitting(true);
    try {
      await login(email, password);
      toast.success("Welcome back!");
      router.push(redirectTo);
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Invalid credentials";
      toast.error(message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthCard
      title={account ? account.name : "Welcome back"}
      description={account ? account.email : "Sign in to your MegooCI account"}
      badge={
        account && (
          <AccountAvatar
            name={account.name}
            email={account.email}
            className="mx-auto mb-2 h-14 w-14 text-lg ring-4 ring-primary/10"
          />
        )
      }
    >
      <form onSubmit={handleSubmit}>
        <CardContent className="space-y-5 px-8">
          {account ? (
            // Not shown, but there for password managers, which pick the
            // saved password by the username in the form.
            <input
              type="email"
              name="email"
              value={account.email}
              autoComplete="username"
              readOnly
              tabIndex={-1}
              aria-hidden="true"
              className="sr-only"
            />
          ) : (
            <div className="space-y-2">
              <label htmlFor="login-email" className="text-sm font-medium">
                Email
              </label>
              <div className="relative">
                <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="login-email"
                  type="email"
                  placeholder="you@example.com"
                  value={typedEmail}
                  onChange={(e) => setTypedEmail(e.target.value)}
                  autoComplete="email"
                  autoFocus
                  className="h-10 pl-9"
                />
              </div>
            </div>
          )}

          {/* Password field */}
          <div className="space-y-2">
            <label htmlFor="login-password" className="text-sm font-medium">
              Password
            </label>
            <div className="relative">
              <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="login-password"
                type={showPassword ? "text" : "password"}
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                autoFocus={!!account}
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
                Signing in…
              </>
            ) : (
              <>
                <LogIn className="h-4 w-4" />
                Sign in
              </>
            )}
          </Button>

          <Link
            href="/forgot-password"
            className="text-xs text-muted-foreground transition-colors hover:text-primary hover:underline"
          >
            Forgot password?
          </Link>

          {showSignup && !account && (
            <>
              <div className="relative flex w-full items-center justify-center py-1">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t" />
                </div>
                <span className="relative bg-card px-3 text-xs text-muted-foreground">
                  New to MegooCI?
                </span>
              </div>

              <Link href="/signup" className="w-full">
                <Button
                  type="button"
                  variant="outline"
                  className="h-10 w-full text-sm font-medium"
                >
                  Create an account
                </Button>
              </Link>
            </>
          )}

          {onChooseAccount && (
            <button
              type="button"
              onClick={onChooseAccount}
              className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-primary hover:underline"
            >
              <ArrowLeft className="h-3 w-3" />
              {account ? "Choose another account" : "Choose an account"}
            </button>
          )}
        </CardFooter>
      </form>
    </AuthCard>
  );
}
