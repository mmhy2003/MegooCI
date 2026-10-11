"use client";

import * as React from "react";
import { ChevronRight, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { CardContent } from "@/components/ui/card";
import type { KnownAccount } from "@/lib/known-accounts";
import { AccountAvatar, AuthCard } from "./auth-card";

function AccountDetails({ account }: { account: KnownAccount }) {
  return (
    <>
      <AccountAvatar name={account.name} email={account.email} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">
          {account.name}
        </span>
        <span className="block truncate text-xs text-muted-foreground">
          {account.email}
        </span>
      </span>
    </>
  );
}

/**
 * The accounts that have signed in on this browser. Choosing one asks for
 * its password; "Manage accounts" lets each be taken off the list.
 */
export function AccountChooser({
  accounts,
  onChoose,
  onUseAnother,
  onForget,
}: {
  accounts: KnownAccount[];
  onChoose: (account: KnownAccount) => void;
  onUseAnother: () => void;
  onForget: (account: KnownAccount) => void;
}) {
  const [managing, setManaging] = React.useState(false);

  return (
    <AuthCard
      title={managing ? "Manage accounts" : "Welcome back"}
      description={
        managing
          ? "Remove accounts from this browser"
          : "Choose an account to continue"
      }
    >
      <CardContent className="space-y-4 px-8 pb-8 pt-3">
        <ul className="space-y-2">
          {accounts.map((account) => (
            <li key={account.id}>
              {managing ? (
                <div className="flex items-center gap-3 rounded-lg border px-3 py-2.5">
                  <AccountDetails account={account} />
                  <button
                    type="button"
                    onClick={() => onForget(account)}
                    aria-label={`Remove ${account.name} from this browser`}
                    className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => onChoose(account)}
                  className="flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors hover:border-primary hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  <AccountDetails account={account} />
                  <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                </button>
              )}
            </li>
          ))}
        </ul>

        {managing ? (
          <Button
            type="button"
            variant="outline"
            className="h-10 w-full text-sm font-medium"
            onClick={() => setManaging(false)}
          >
            Done
          </Button>
        ) : (
          <>
            <Button
              type="button"
              variant="outline"
              className="h-10 w-full text-sm font-medium"
              onClick={onUseAnother}
            >
              Use another account
            </Button>
            <button
              type="button"
              onClick={() => setManaging(true)}
              className="mx-auto block text-xs text-muted-foreground transition-colors hover:text-primary hover:underline"
            >
              Manage accounts
            </button>
          </>
        )}
      </CardContent>
    </AuthCard>
  );
}
