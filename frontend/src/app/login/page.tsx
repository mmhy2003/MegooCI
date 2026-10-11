"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { authApi } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import {
  forgetAccount,
  listKnownAccounts,
  type KnownAccount,
} from "@/lib/known-accounts";
import { AccountChooser } from "@/components/login/account-chooser";
import { AuthBackdrop } from "@/components/login/auth-backdrop";
import { SignInForm } from "@/components/login/sign-in-form";

function Login() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirectTo = searchParams.get("redirect") || "/dashboard";
  const { accessToken, hasHydrated } = useAuthStore();

  React.useEffect(() => {
    if (hasHydrated && accessToken) router.replace(redirectTo);
  }, [hasHydrated, accessToken, router, redirectTo]);

  // The accounts this browser remembers are only known in the browser, so
  // nothing is shown until they have been read: a browser with accounts
  // then opens on the list, without first showing the form.
  const [accounts, setAccounts] = React.useState<KnownAccount[] | null>(null);
  React.useEffect(() => {
    setAccounts(listKnownAccounts());
  }, []);

  // "Create an account" is held back until the server has said whether
  // sign-up is open, so it does not appear and then vanish. If the server
  // cannot be asked, it is offered, as it was before the question existed.
  const [showSignup, setShowSignup] = React.useState(false);
  React.useEffect(() => {
    authApi
      .signupStatus()
      .then((status) => setShowSignup(status.signup_enabled))
      .catch(() => setShowSignup(true));
  }, []);

  const [chosen, setChosen] = React.useState<KnownAccount | null>(null);
  const [useAnother, setUseAnother] = React.useState(false);

  if (accounts === null) return null;

  if (chosen) {
    return (
      <SignInForm
        key={chosen.id}
        account={chosen}
        redirectTo={redirectTo}
        showSignup={false}
        onChooseAccount={() => setChosen(null)}
      />
    );
  }

  if (accounts.length > 0 && !useAnother) {
    return (
      <AccountChooser
        accounts={accounts}
        onChoose={setChosen}
        onUseAnother={() => setUseAnother(true)}
        onForget={(account) => setAccounts(forgetAccount(account.id))}
      />
    );
  }

  return (
    <SignInForm
      key="form"
      redirectTo={redirectTo}
      showSignup={showSignup}
      onChooseAccount={
        accounts.length > 0 ? () => setUseAnother(false) : undefined
      }
    />
  );
}

export default function LoginPage() {
  return (
    <AuthBackdrop>
      <React.Suspense fallback={null}>
        <Login />
      </React.Suspense>
    </AuthBackdrop>
  );
}
