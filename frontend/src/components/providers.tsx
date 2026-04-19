"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

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

function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = React.useState<Theme>("system");
  const [resolvedTheme, setResolvedTheme] = React.useState<"light" | "dark">("light");

  React.useEffect(() => {
    const stored = localStorage.getItem("megooci_theme") as Theme | null;
    if (stored) setThemeState(stored);
  }, []);

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

    if (theme === "system") {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const handler = () => applyTheme("system");
      mq.addEventListener("change", handler);
      return () => mq.removeEventListener("change", handler);
    }
  }, [theme]);

  const setTheme = React.useCallback((t: Theme) => {
    setThemeState(t);
    localStorage.setItem("megooci_theme", t);
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
export function Providers({ children }: { children: React.ReactNode }) {
  const queryClient = getQueryClient();

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>{children}</ThemeProvider>
    </QueryClientProvider>
  );
}
