"use client";

import * as React from "react";
import { usePathname } from "next/navigation";
import { Search, Bell, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";

function getBreadcrumbs(pathname: string) {
  const segments = pathname.split("/").filter(Boolean);
  return segments.map((segment, index) => ({
    label: segment.charAt(0).toUpperCase() + segment.slice(1).replace(/-/g, " "),
    href: "/" + segments.slice(0, index + 1).join("/"),
    isLast: index === segments.length - 1,
  }));
}

export function Header() {
  const pathname = usePathname();
  const breadcrumbs = getBreadcrumbs(pathname);

  return (
    <header className="flex h-14 items-center justify-between border-b bg-card px-6">
      {/* Breadcrumbs */}
      <nav className="flex items-center gap-1 text-sm">
        {breadcrumbs.map((crumb, i) => (
          <React.Fragment key={crumb.href}>
            {i > 0 && (
              <span className="mx-1 text-muted-foreground">/</span>
            )}
            {crumb.isLast ? (
              <span className="font-medium">{crumb.label}</span>
            ) : (
              <a
                href={crumb.href}
                className="text-muted-foreground hover:text-foreground transition-colors"
              >
                {crumb.label}
              </a>
            )}
          </React.Fragment>
        ))}
      </nav>

      {/* Right section */}
      <div className="flex items-center gap-2">
        {/* Search */}
        <div className="relative hidden md:block">
          <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search... ⌘K"
            className="w-64 pl-9"
            readOnly
          />
        </div>

        <Separator orientation="vertical" className="mx-1 h-6" />

        {/* Quick actions */}
        <Button variant="ghost" size="icon" className="relative">
          <Bell className="h-4 w-4" />
        </Button>

        <Button size="sm">
          <Plus className="mr-1 h-4 w-4" />
          New Build
        </Button>
      </div>
    </header>
  );
}
