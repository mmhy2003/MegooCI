"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { useAuthStore } from "@/lib/auth";
import { ConfirmProvider } from "@/components/ui/confirm-dialog";

// ------------------------------------------------------------------
// Theme
// ------------------------------------------------------------------
type Theme = "light" | "dark" | "system";

interface ThemeContextValue {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  resolvedTheme: "light" | "dark";
}

const ThemeContext = React.createContext<ThemeContextValue>({
  theme: "system",
  setTheme: () => {},
  resolvedTheme: "light",
});

export function useTheme() {
  return React.useContext(ThemeContext);
}

const THEME_STORAGE_KEY = "megooci_theme";

/**
 * Reads the stored or OS-detected theme synchronously on mount. Matches the
 * logic in the inline `THEME_INIT_SCRIPT` in `layout.tsx` so the initial
 * `document.documentElement.classList` set by the script aligns with React
 * state and we don't get a flash when the first effect runs.
 */
function readInitialResolvedTheme(): "light" | "dark" {
  if (typeof window === "undefined") return "light";
  const stored = localStorage.getItem(THEME_STORAGE_KEY) as Theme | null;
  const theme = stored || "system";
  if (theme === "system") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }
  return theme;
}

function ThemeProvider({ children }: { children: React.ReactNode }) {
  // `theme` is the user's preference ("light" | "dark" | "system"), while
  // `resolvedTheme` is what we actually apply to the DOM. On the client we
  // initialize lazily so a user who stored "dark" doesn't see a light flash
  // between the initial render and the first effect pass.
  const [theme, setThemeState] = React.useState<Theme>(() => {
    if (typeof window === "undefined") return "system";
    return (
      (localStorage.getItem(THEME_STORAGE_KEY) as Theme | null) || "system"
    );
  });
  const [resolvedTheme, setResolvedTheme] = React.useState<
    "light" | "dark"
  >(() => readInitialResolvedTheme());

  React.useEffect(() => {
    const root = document.documentElement;

    const applyTheme = (t: Theme) => {
      let resolved: "light" | "dark";
      if (t === "system") {
        resolved = window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light";
      } else {
        resolved = t;
      }
      root.classList.toggle("dark", resolved === "dark");
      setResolvedTheme(resolved);
    };

    applyTheme(theme);

    // Only subscribe to OS changes while the user's preference is "system".
    if (theme === "system") {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const handler = () => applyTheme("system");
      mq.addEventListener("change", handler);
      return () => mq.removeEventListener("change", handler);
    }
  }, [theme]);

  const setTheme = React.useCallback((t: Theme) => {
    setThemeState(t);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, t);
    } catch {
      // localStorage can throw in private mode / quota-exceeded; ignore.
    }
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, setTheme, resolvedTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

// ------------------------------------------------------------------
// React Query
// ------------------------------------------------------------------
function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30 * 1000,
        refetchOnWindowFocus: false,
      },
    },
  });
}

let browserQueryClient: QueryClient | undefined;

function getQueryClient() {
  if (typeof window === "undefined") return makeQueryClient();
  if (!browserQueryClient) browserQueryClient = makeQueryClient();
  return browserQueryClient;
}

// ------------------------------------------------------------------
// Combined Providers
// ------------------------------------------------------------------
function AuthBootstrap({ children }: { children: React.ReactNode }) {
  const loadUser = useAuthStore((s) => s.loadUser);

  React.useEffect(() => {
    loadUser();
  }, [loadUser]);

  return <>{children}</>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const queryClient = getQueryClient();

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ConfirmProvider>
          <AuthBootstrap>{children}</AuthBootstrap>
        </ConfirmProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}

/**
 * Sonner's <Toaster> needs the app's resolved theme so toast backgrounds
 * and text render correctly in dark mode. Rendering it here lets it read
 * from the same ThemeContext Providers set up above.
 */
export function ThemedToaster() {
  const { resolvedTheme } = useTheme();
  return <Toaster richColors position="bottom-right" theme={resolvedTheme} />;
}
