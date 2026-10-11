"use client";

import { create } from "zustand";
import {
  authApi,
  onTokensChanged,
  type AuthTokens,
  type SignInResult,
  type TwoStepMethod,
  type User,
} from "./api";
import { rememberAccount } from "./known-accounts";

const TOKEN_KEY = "megooci_access_token";
const REFRESH_KEY = "megooci_refresh_token";

function setTokenCookie(name: string, value: string | null) {
  if (typeof document === "undefined") return;
  if (value) {
    document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=${60 * 60 * 24 * 30}; SameSite=Lax`;
  } else {
    document.cookie = `${name}=; path=/; max-age=0`;
  }
}

/**
 * A sign-in that has got as far as a correct password and now waits for the
 * second step ("verify"), or for two-step verification to be set up because
 * the server requires it ("setup"). Kept in memory only: a reload forgets it
 * and starts again from the password.
 */
export interface PendingTwoStep {
  kind: "verify" | "setup";
  ticket: string;
  methods: TwoStepMethod[];
  passkeyOrigin: string;
  email: string;
}

interface AuthState {
  user: User | null;
  accessToken: string | null;
  refreshToken: string | null;
  isLoading: boolean;
  hasHydrated: boolean;
  pending: PendingTwoStep | null;

  hydrate: () => void;
  login: (email: string, password: string) => Promise<SignInResult>;
  signup: (email: string, password: string, name: string) => Promise<SignInResult>;
  /**
   * Take what the server answered to a password, a second step or a finished
   * setup: a session is stored and the user loaded; anything else becomes
   * `pending`. Returns the answer, so the caller can tell which it was.
   */
  acceptSignIn: (result: SignInResult, email: string) => Promise<SignInResult>;
  clearPending: () => void;
  logout: () => void;
  refreshAccessToken: () => Promise<void>;
  loadUser: () => Promise<void>;
  setTokens: (tokens: AuthTokens) => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  accessToken: null,
  refreshToken: null,
  isLoading: true,
  hasHydrated: false,
  pending: null,

  hydrate: () => {
    if (typeof window === "undefined") return;
    if (get().hasHydrated) return;
    const accessToken = localStorage.getItem(TOKEN_KEY);
    const refreshToken = localStorage.getItem(REFRESH_KEY);
    if (accessToken) {
      setTokenCookie(TOKEN_KEY, accessToken);
    }
    set({ accessToken, refreshToken, hasHydrated: true });
  },

  setTokens: (tokens: AuthTokens) => {
    if (typeof window !== "undefined") {
      localStorage.setItem(TOKEN_KEY, tokens.access_token);
      localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
    }
    setTokenCookie(TOKEN_KEY, tokens.access_token);
    set({
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
    });
  },

  acceptSignIn: async (result: SignInResult, email: string) => {
    if (result.status === "signed_in") {
      get().setTokens(result);
      const user = await authApi.getMe();
      set({ user, pending: null });
    } else {
      set({
        pending: {
          kind: result.status === "two_step_required" ? "verify" : "setup",
          ticket: result.ticket,
          methods: result.status === "two_step_required" ? result.methods : [],
          passkeyOrigin: result.passkey_origin,
          email,
        },
      });
    }
    return result;
  },

  clearPending: () => set({ pending: null }),

  login: async (email: string, password: string) =>
    get().acceptSignIn(await authApi.login({ email, password }), email),

  signup: async (email: string, password: string, name: string) =>
    get().acceptSignIn(await authApi.signup({ email, password, name }), email),

  logout: () => {
    if (typeof window !== "undefined") {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(REFRESH_KEY);
    }
    setTokenCookie(TOKEN_KEY, null);
    set({ user: null, accessToken: null, refreshToken: null, pending: null });
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
  },

  refreshAccessToken: async () => {
    const { refreshToken } = get();
    if (!refreshToken) {
      get().logout();
      return;
    }
    try {
      const tokens = await authApi.refresh(refreshToken);
      get().setTokens(tokens);
    } catch {
      get().logout();
    }
  },

  loadUser: async () => {
    get().hydrate();
    const { accessToken } = get();
    if (!accessToken) {
      set({ isLoading: false });
      return;
    }
    try {
      const user = await authApi.getMe();
      set({ user, isLoading: false });
    } catch {
      try {
        await get().refreshAccessToken();
        const user = await authApi.getMe();
        set({ user, isLoading: false });
      } catch {
        set({ user: null, accessToken: null, refreshToken: null, isLoading: false });
      }
    }
  },
}));

// Whoever is signed in here is offered again on the login page. Wherever the
// user is set (sign-in, sign-up, an accepted invitation, a page load, a
// profile change), the list gets the current name and email.
useAuthStore.subscribe((state, previous) => {
  if (state.user && state.user !== previous.user) rememberAccount(state.user);
});

// The API client renews an expired access token on its own, and gives up the
// session when the refresh token is refused. Keep the store, and the cookie
// the middleware reads, in step with it.
onTokensChanged((tokens) => {
  if (tokens) {
    setTokenCookie(TOKEN_KEY, tokens.access_token);
    useAuthStore.setState({
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
    });
  } else {
    setTokenCookie(TOKEN_KEY, null);
    useAuthStore.setState({ user: null, accessToken: null, refreshToken: null });
  }
});

// Another tab signed in, signed out or renewed its tokens: follow it, so this
// tab does not carry on with tokens that are no longer the stored ones.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    // A null key means the whole storage was cleared.
    if (event.key !== null && event.key !== TOKEN_KEY && event.key !== REFRESH_KEY) return;
    if (!useAuthStore.getState().hasHydrated) return;
    const accessToken = localStorage.getItem(TOKEN_KEY);
    const refreshToken = localStorage.getItem(REFRESH_KEY);
    useAuthStore.setState(
      accessToken
        ? { accessToken, refreshToken }
        : { user: null, accessToken: null, refreshToken: null },
    );
  });
}
