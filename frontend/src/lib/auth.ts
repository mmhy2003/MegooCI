"use client";

import { create } from "zustand";
import { authApi, type User, type AuthTokens } from "./api";

const TOKEN_KEY = "megooci_access_token";
const REFRESH_KEY = "megooci_refresh_token";

interface AuthState {
  user: User | null;
  accessToken: string | null;
  refreshToken: string | null;
  isLoading: boolean;

  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, name: string) => Promise<void>;
  logout: () => void;
  refreshAccessToken: () => Promise<void>;
  loadUser: () => Promise<void>;
  setTokens: (tokens: AuthTokens) => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  accessToken:
    typeof window !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null,
  refreshToken:
    typeof window !== "undefined" ? localStorage.getItem(REFRESH_KEY) : null,
  isLoading: true,

  setTokens: (tokens: AuthTokens) => {
    localStorage.setItem(TOKEN_KEY, tokens.access_token);
    localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
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
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
    set({ user: null, accessToken: null, refreshToken: null });
    window.location.href = "/login";
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
        set({ isLoading: false });
        get().logout();
      }
    }
  },
}));
