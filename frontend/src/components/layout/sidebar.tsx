"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutDashboard,
  GitBranch,
  FolderKanban,
  Hammer,
  Server,
  KeyRound,
  Settings,
  ChevronLeft,
  ChevronRight,
  LogOut,
  Monitor,
  Moon,
  Sun,
  User,
  Users,
  X,
  Plug,
  Container,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Avatar } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuLabel,
} from "@/components/ui/dropdown-menu";
import { useAuthStore } from "@/lib/auth";
import { useTheme } from "@/components/providers";
import { useConfirm } from "@/components/ui/confirm-dialog";

interface NavItem {
  href: string;
  label: string;
  icon: React.ElementType;
  adminOnly?: boolean;
  permission?: string;
}

const navItems: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/pipelines", label: "Pipelines", icon: GitBranch, permission: "pipelines.read" },
  { href: "/projects", label: "Projects", icon: FolderKanban, permission: "projects.read" },
  { href: "/builds", label: "Builds", icon: Hammer, permission: "builds.read" },
  { href: "/agents", label: "Agents", icon: Server, permission: "agents.read" },
  { href: "/secrets", label: "Secrets", icon: KeyRound, permission: "secrets.read" },
  { href: "/registry", label: "Registry", icon: Container, permission: "registry.read" },
  { href: "/integrations", label: "Integrations", icon: Plug, adminOnly: true },
  { href: "/admin/users", label: "Users", icon: Users, adminOnly: true },
  { href: "/settings", label: "Settings", icon: Settings },
];

interface SidebarProps {
  mobileOpen: boolean;
  onCloseMobile: () => void;
}

const COLLAPSED_STORAGE_KEY = "megooci_sidebar_collapsed";

export function Sidebar({ mobileOpen, onCloseMobile }: SidebarProps) {
  const pathname = usePathname();
  // `AppLayout` (and therefore `Sidebar`) mounts per route rather than being
  // hoisted into a Next.js layout, so component-local state would reset on
  // every navigation. Persist the desktop collapsed state to localStorage so
  // it survives page transitions and full reloads.
  const [collapsed, setCollapsedState] = React.useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return localStorage.getItem(COLLAPSED_STORAGE_KEY) === "1";
  });

  const setCollapsed = React.useCallback((value: boolean) => {
    setCollapsedState(value);
    try {
      localStorage.setItem(COLLAPSED_STORAGE_KEY, value ? "1" : "0");
    } catch {
      // Private mode / quota — non-fatal, the state is still live in memory
      // for this session.
    }
  }, []);

  const router = useRouter();
  const { user, logout } = useAuthStore();
  const { theme, setTheme } = useTheme();
  const confirm = useConfirm();

  const handleLogout = React.useCallback(async () => {
    const ok = await confirm({
      title: "Log out of MegooCI?",
      description: (
        <>
          You&apos;ll need to sign in again to view builds, trigger pipelines,
          or access your projects.
        </>
      ),
      confirmText: "Log out",
      cancelText: "Stay signed in",
      tone: "warning",
    });
    if (ok) logout();
  }, [confirm, logout]);

  // Collapse toggle only makes sense on desktop; on mobile the drawer is always
  // the "expanded" width so users can tap labels.
  const isCollapsed = collapsed;

  return (
    <>
      {/* Mobile backdrop */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={onCloseMobile}
          aria-hidden="true"
        />
      )}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex h-screen flex-col border-r bg-card transition-transform duration-200",
          "md:static md:translate-x-0 md:transition-[width]",
          mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
          // On mobile the drawer is always 16rem wide for tappable targets.
          "w-64",
          // On desktop respect collapsed state.
          isCollapsed ? "md:w-16" : "md:w-64",
        )}
      >
        {/* Brand */}
        <div className="flex h-14 items-center justify-between border-b px-4">
          <Link
            href="/dashboard"
            className="flex items-center gap-2"
            onClick={onCloseMobile}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/icons/icon.svg"
              alt="MegooCI"
              className="h-8 w-8 rounded-lg"
            />
            {(!isCollapsed || mobileOpen) && (
              <span className="text-lg font-bold tracking-tight md:block">
                <span className={cn(isCollapsed && "md:hidden")}>MegooCI</span>
              </span>
            )}
          </Link>
          {/* Close drawer on mobile */}
          <button
            onClick={onCloseMobile}
            className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground md:hidden"
            aria-label="Close navigation"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 space-y-1 overflow-y-auto px-2 py-4">
          {navItems.map((item) => {
            if (item.adminOnly && !user?.is_admin) return null;
            if (item.permission && !user?.is_admin && !user?.permissions?.includes(item.permission)) return null;
            const isActive =
              pathname === item.href || pathname.startsWith(item.href + "/");
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={onCloseMobile}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                  isCollapsed && "md:justify-center md:px-2",
                )}
                title={isCollapsed ? item.label : undefined}
              >
                <item.icon className="h-5 w-5 shrink-0" />
                <span className={cn(isCollapsed && "md:hidden")}>
                  {item.label}
                </span>
              </Link>
            );
          })}
        </nav>

        {/* Collapse toggle (desktop only) */}
        <div className="hidden border-t px-2 py-2 md:block">
          <button
            onClick={() => setCollapsed(!isCollapsed)}
            className="flex w-full items-center justify-center rounded-lg px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            {isCollapsed ? (
              <ChevronRight className="h-4 w-4" />
            ) : (
              <>
                <ChevronLeft className="h-4 w-4 mr-2" />
                <span>Collapse</span>
              </>
            )}
          </button>
        </div>

        {/* User section — shows user info + a one-click Log out.
            Expanded: user info on the left (opens the account dropdown),
            Log out icon button on the right.
            Collapsed (desktop only): avatar on top, log out button stacked
            below it so both remain reachable. */}
        <div className="border-t p-2 overflow-hidden">
          <div
            className={cn(
              "flex min-w-0 items-center gap-1",
              isCollapsed && "md:flex-col md:gap-1",
            )}
          >
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  className={cn(
                    "flex min-w-0 flex-1 items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors hover:bg-accent overflow-hidden",
                    isCollapsed && "md:flex-none md:justify-center md:px-2",
                  )}
                >
                  <Avatar
                    fallback={user?.name || user?.email || "U"}
                    size="sm"
                  />
                  <div
                    className={cn(
                      "min-w-0 flex-1 text-left",
                      isCollapsed && "md:hidden",
                    )}
                  >
                    <p className="truncate font-medium text-sm">
                      {user?.name || "User"}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {user?.email}
                    </p>
                  </div>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent side="top" align="start">
                <DropdownMenuLabel>My Account</DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => router.push("/settings")}>
                  <User className="mr-2 h-4 w-4" />
                  Profile
                </DropdownMenuItem>
                {/* Cycle light -> dark -> system -> light so users can get
                    back to OS-follow without visiting Settings. Label names
                    the *next* state to match the icon convention. */}
                <DropdownMenuItem
                  onClick={() =>
                    setTheme(
                      theme === "light"
                        ? "dark"
                        : theme === "dark"
                          ? "system"
                          : "light",
                    )
                  }
                >
                  {theme === "light" ? (
                    <Moon className="mr-2 h-4 w-4" />
                  ) : theme === "dark" ? (
                    <Monitor className="mr-2 h-4 w-4" />
                  ) : (
                    <Sun className="mr-2 h-4 w-4" />
                  )}
                  {theme === "light"
                    ? "Dark mode"
                    : theme === "dark"
                      ? "Match system"
                      : "Light mode"}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>

            <button
              type="button"
              onClick={handleLogout}
              className={cn(
                "inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
              )}
              title="Log out"
              aria-label="Log out"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}
