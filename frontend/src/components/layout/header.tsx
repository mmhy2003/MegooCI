"use client";

import * as React from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  Search,
  Bell,
  Menu,
  CheckCircle2,
  XCircle,
  Ban,
  ServerOff,
  BellOff,
  CheckCheck,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ThemeToggle } from "@/components/theme-toggle";
import { useNotifications } from "@/contexts/notification-context";
import type { UserNotification } from "@/lib/api";

const HIDDEN_SEGMENTS = new Set(["admin"]);

function getBreadcrumbs(pathname: string) {
  const segments = pathname.split("/").filter(Boolean);
  const visible = segments.filter((s) => !HIDDEN_SEGMENTS.has(s));
  return visible.map((segment, index) => ({
    label: segment.charAt(0).toUpperCase() + segment.slice(1).replace(/-/g, " "),
    href: "/" + segments.slice(0, segments.indexOf(segment) + 1).join("/"),
    isLast: index === visible.length - 1,
  }));
}

function notifIcon(type: string) {
  switch (type) {
    case "build_success":
      return <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />;
    case "build_failed":
      return <XCircle className="h-4 w-4 shrink-0 text-red-500" />;
    case "build_cancelled":
      return <Ban className="h-4 w-4 shrink-0 text-yellow-500" />;
    case "agent_offline":
      return <ServerOff className="h-4 w-4 shrink-0 text-red-500" />;
    default:
      return <Bell className="h-4 w-4 shrink-0 text-muted-foreground" />;
  }
}

function entityHref(n: UserNotification): string | null {
  if (!n.entity_type || !n.entity_id) return null;
  switch (n.entity_type) {
    case "build":
      return `/builds/${n.entity_id}`;
    case "agent":
      return `/agents`;
    case "pipeline":
      return `/pipelines`;
    default:
      return null;
  }
}

function timeAgo(iso: string): string {
  const seconds = Math.floor(
    (Date.now() - new Date(iso).getTime()) / 1000,
  );
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

interface HeaderProps {
  onOpenMobile: () => void;
  onOpenSearch: () => void;
}

export function Header({ onOpenMobile, onOpenSearch }: HeaderProps) {
  const pathname = usePathname();
  const router = useRouter();
  const breadcrumbs = getBreadcrumbs(pathname);
  const mobileCrumb = breadcrumbs[breadcrumbs.length - 1];

  const { notifications, unreadCount, markRead, markAllRead } =
    useNotifications();

  const [dropdownOpen, setDropdownOpen] = React.useState(false);
  const dropdownRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleNotifClick = (n: UserNotification) => {
    if (!n.read_at) markRead(n.id);
    const href = entityHref(n);
    if (href) {
      router.push(href);
      setDropdownOpen(false);
    }
  };

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

        {mobileCrumb && (
          <span className="truncate text-sm font-medium md:hidden">
            {mobileCrumb.label}
          </span>
        )}
      </div>

      {/* Right section */}
      <div className="flex shrink-0 items-center gap-1 sm:gap-2">
        <button
          type="button"
          onClick={onOpenSearch}
          className="relative hidden lg:block"
        >
          <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search... ⌘K"
            className="w-64 cursor-pointer pl-9"
            readOnly
            tabIndex={-1}
          />
        </button>

        <Button
          variant="ghost"
          size="icon"
          className="lg:hidden"
          aria-label="Search"
          onClick={onOpenSearch}
        >
          <Search className="h-4 w-4" />
        </Button>

        <Separator
          orientation="vertical"
          className="mx-1 hidden h-6 sm:block"
        />

        {/* Notifications dropdown */}
        <div ref={dropdownRef} className="relative">
          <Button
            variant="ghost"
            size="icon"
            className="relative"
            aria-label="Notifications"
            onClick={() => setDropdownOpen((prev) => !prev)}
          >
            <Bell className="h-4 w-4" />
            {unreadCount > 0 && (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-bold text-destructive-foreground">
                {unreadCount > 99 ? "99+" : unreadCount}
              </span>
            )}
          </Button>

          {dropdownOpen && (
            <div className="absolute right-0 z-50 mt-1 w-80 overflow-hidden rounded-lg border bg-popover shadow-lg sm:w-96">
              <div className="flex items-center justify-between border-b px-4 py-2.5">
                <h3 className="text-sm font-semibold">Notifications</h3>
                {unreadCount > 0 && (
                  <button
                    type="button"
                    onClick={() => markAllRead()}
                    className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <CheckCheck className="h-3.5 w-3.5" />
                    Mark all read
                  </button>
                )}
              </div>

              {notifications.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-10 text-muted-foreground">
                  <BellOff className="h-8 w-8" />
                  <p className="text-sm">No notifications yet</p>
                </div>
              ) : (
                <ScrollArea maxHeight="384px">
                  <div className="divide-y">
                    {notifications.map((n) => (
                      <button
                        key={n.id}
                        type="button"
                        onClick={() => handleNotifClick(n)}
                        className={`flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-accent/50 ${
                          !n.read_at ? "bg-accent/20" : ""
                        }`}
                      >
                        <div className="mt-0.5">{notifIcon(n.type)}</div>
                        <div className="min-w-0 flex-1">
                          <p className={`text-sm leading-snug ${!n.read_at ? "font-medium" : "text-muted-foreground"}`}>
                            {n.title}
                          </p>
                          <p className="mt-0.5 truncate text-xs text-muted-foreground">
                            {n.body}
                          </p>
                          <p className="mt-1 text-[11px] text-muted-foreground/70">
                            {timeAgo(n.created_at)}
                          </p>
                        </div>
                        {!n.read_at && (
                          <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-primary" />
                        )}
                      </button>
                    ))}
                  </div>
                </ScrollArea>
              )}
            </div>
          )}
        </div>

        <ThemeToggle variant="icon" className="h-8 w-8 border-0 shadow-none" />
      </div>
    </header>
  );
}
