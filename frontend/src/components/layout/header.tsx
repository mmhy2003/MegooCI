"use client";

import * as React from "react";
import { usePathname } from "next/navigation";
import { Search, Bell, Menu } from "lucide-react";
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

interface HeaderProps {
  onOpenMobile: () => void;
}

export function Header({ onOpenMobile }: HeaderProps) {
  const pathname = usePathname();
  const breadcrumbs = getBreadcrumbs(pathname);
  // On mobile we hide intermediate crumbs and just show the last one for
  // compactness.
  const mobileCrumb = breadcrumbs[breadcrumbs.length - 1];

  return (
    <header className="flex h-14 items-center justify-between gap-2 border-b bg-card px-3 sm:px-6">
      {/* Left: hamburger + breadcrumbs */}
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <Button
          variant="ghost"
          size="icon"
          className="md:hidden"
          onClick={onOpenMobile}
          aria-label="Open navigation"
        >
          <Menu className="h-5 w-5" />
        </Button>

        {/* Breadcrumbs (full on desktop) */}
        <nav className="hidden min-w-0 items-center gap-1 text-sm md:flex">
          {breadcrumbs.map((crumb, i) => (
            <React.Fragment key={crumb.href}>
              {i > 0 && (
                <span className="mx-1 text-muted-foreground">/</span>
              )}
              {crumb.isLast ? (
                <span className="truncate font-medium">{crumb.label}</span>
              ) : (
                <a
                  href={crumb.href}
                  className="truncate text-muted-foreground hover:text-foreground transition-colors"
                >
                  {crumb.label}
                </a>
              )}
            </React.Fragment>
          ))}
        </nav>

        {/* Mobile: just current page title */}
        {mobileCrumb && (
          <span className="truncate text-sm font-medium md:hidden">
            {mobileCrumb.label}
          </span>
        )}
      </div>

      {/* Right section */}
      <div className="flex shrink-0 items-center gap-1 sm:gap-2">
        {/* Search: lg+ only */}
        <div className="relative hidden lg:block">
          <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search... ⌘K"
            className="w-64 pl-9"
            readOnly
          />
        </div>

        {/* Mobile/tablet: icon-only search */}
        <Button
          variant="ghost"
          size="icon"
          className="lg:hidden"
          aria-label="Search"
        >
          <Search className="h-4 w-4" />
        </Button>

        <Separator
          orientation="vertical"
          className="mx-1 hidden h-6 sm:block"
        />

        <Button
          variant="ghost"
          size="icon"
          className="relative"
          aria-label="Notifications"
        >
          <Bell className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );
}
