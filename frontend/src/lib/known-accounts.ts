/**
 * The accounts that have signed in on this browser, for the login page's
 * account list. Only what the list shows is kept: never a password or a token.
 */

const KEY = "megooci_known_accounts";
const MAX_ACCOUNTS = 5;

export interface KnownAccount {
  id: string;
  email: string;
  name: string;
  /** When it last signed in or was loaded here, in ms since the epoch. */
  lastUsed: number;
}

function isKnownAccount(value: unknown): value is KnownAccount {
  if (typeof value !== "object" || value === null) return false;
  const account = value as Record<string, unknown>;
  return (
    typeof account.id === "string" &&
    typeof account.email === "string" &&
    typeof account.name === "string" &&
    typeof account.lastUsed === "number"
  );
}

function save(accounts: KnownAccount[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(accounts));
  } catch {
    // localStorage can throw in private mode / quota-exceeded; ignore.
  }
}

/** Most recently used first. */
export function listKnownAccounts(): KnownAccount[] {
  if (typeof window === "undefined") return [];
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(KEY) || "[]");
    if (!Array.isArray(stored)) return [];
    return stored
      .filter(isKnownAccount)
      .sort((a, b) => b.lastUsed - a.lastUsed)
      .slice(0, MAX_ACCOUNTS);
  } catch {
    return [];
  }
}

/** Put an account at the top of the list, with its current name and email. */
export function rememberAccount(user: {
  id: string;
  email: string;
  name: string;
}): void {
  if (typeof window === "undefined") return;
  const others = listKnownAccounts().filter(
    (account) => account.id !== user.id && account.email !== user.email,
  );
  const account: KnownAccount = {
    id: user.id,
    email: user.email,
    name: user.name,
    lastUsed: Date.now(),
  };
  save([account, ...others].slice(0, MAX_ACCOUNTS));
}

/** Take an account off the list. Returns the accounts that are left. */
export function forgetAccount(id: string): KnownAccount[] {
  const rest = listKnownAccounts().filter((account) => account.id !== id);
  save(rest);
  return rest;
}
