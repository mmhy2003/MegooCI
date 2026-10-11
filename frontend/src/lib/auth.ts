"use client";

import { create } from "zustand";
import { authApi, onTokensChanged, type User, type AuthTokens } from "./api";

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

interface AuthState {
  user: User | null;
  accessToken: string | null;
  refreshToken: string | null;
  isLoading: boolean;
  hasHydrated: boolean;

  hydrate: () => void;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, name: string) => Promise<void>;
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

  login: async (email: string, password: string) => {
    const tokens = await authApi.login({ email, password });
    get().setTokens(tokens);
    const user = await authApi.getMe();
    set({ user });
  },

  signup: async (email: string, password: string, name: string) => {
    const tokens = await authApi.signup({ email, password, name });
    get().setTokens(tokens);
    const user = await authApi.getMe();
    set({ user });
  },

  logout: () => {
    if (typeof window !== "undefined") {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(REFRESH_KEY);
    }
    setTokenCookie(TOKEN_KEY, null);
    set({ user: null, accessToken: null, refreshToken: null });
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
