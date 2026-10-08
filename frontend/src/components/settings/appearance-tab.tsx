"use client";

import { Palette } from "lucide-react";
import { useTheme } from "@/components/providers";
import { ThemeToggle } from "@/components/theme-toggle";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export function AppearanceTab() {
  const { theme, resolvedTheme } = useTheme();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Palette className="h-5 w-5" />
          Appearance
        </CardTitle>
        <CardDescription>
          Choose how MegooCI looks. Selecting{" "}
          <span className="font-medium text-foreground">System</span>{" "}
          follows your operating system&apos;s light / dark preference
          automatically.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <label className="text-sm font-medium">Theme</label>
          <ThemeToggle />
        </div>
        <p className="text-xs text-muted-foreground">
          Current mode:{" "}
          <span className="font-medium text-foreground">
            {resolvedTheme === "dark" ? "Dark" : "Light"}
          </span>
          {theme === "system" && " (auto-detected from system)"}
        </p>
      </CardContent>
    </Card>
  );
}
