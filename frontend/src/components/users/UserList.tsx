"use client";

import * as React from "react";
import { Search } from "lucide-react";
import type { Role, UserDetail } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { accessKind, leftoverGlobalRoles, projectRows, type AccessKind } from "./access";

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : name.slice(0, 2);
  return letters.toUpperCase();
}

const GROUPS: { kind: AccessKind; title: string }[] = [
  { kind: "half", title: "Needs a decision" },
  { kind: "administrator", title: "Administrators" },
  { kind: "member", title: "Members" },
];

function MetaLine({ user, roles }: { user: UserDetail; roles: Role[] }) {
  const kind = accessKind(user);
  const warnings: string[] = [];
  if (!user.two_step_enabled) warnings.push("two-step off");
  if (kind === "member" && leftoverGlobalRoles(user, roles).length > 0) {
    warnings.push("sees all projects");
  }

  let text: string;
  if (kind === "administrator") text = "Every project";
  else if (kind === "half") text = "";
  else {
    const rows = projectRows(user);
    const developer = rows.filter((r) => r.role_name === "developer").length;
    text = `${rows.length} project${rows.length === 1 ? "" : "s"}`;
    if (developer > 0) text += ` · ${developer} as developer`;
  }

  return (
    <span className="block truncate text-xs text-muted-foreground">
      {kind === "half" && (
        <span className="text-amber-700 dark:text-amber-400">Admin access half set up</span>
      )}
      {text}
      {warnings.map((w) => (
        <span key={w}>
          {" · "}
          <span className="text-amber-700 dark:text-amber-400">{w}</span>
        </span>
      ))}
    </span>
  );
}

export function Avatar({ user, size = "md" }: { user: UserDetail; size?: "md" | "lg" }) {
  const admin = accessKind(user) === "administrator";
  return (
    <span
      className={cn(
        "grid shrink-0 place-items-center rounded-full border bg-muted font-semibold",
        size === "lg" ? "h-11 w-11 text-sm" : "h-8 w-8 text-xs",
        admin && "border-primary text-primary",
      )}
    >
      {initials(user.name || user.email)}
    </span>
  );
}

/** People in the order the list shows them: by group, then by name. */
export function listOrder(users: UserDetail[]): UserDetail[] {
  return GROUPS.flatMap((group) =>
    users
      .filter((u) => accessKind(u) === group.kind)
      .sort((x, y) => x.name.localeCompare(y.name, undefined, { sensitivity: "base" })),
  );
}

export function UserList({
  users,
  roles,
  selectedId,
  onSelect,
}: {
  users: UserDetail[];
  roles: Role[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [query, setQuery] = React.useState("");
  const q = query.trim().toLowerCase();
  const matching = users.filter(
    (u) => !q || u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q),
  );

  return (
    <div className="flex flex-col gap-1 p-2">
      <div className="relative mb-1">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search name or email…"
          className="pl-8"
        />
      </div>
      {matching.length === 0 && (
        <p className="px-2 py-6 text-center text-sm text-muted-foreground">No one matches</p>
      )}
      {GROUPS.map((group) => {
        const people = listOrder(matching).filter((u) => accessKind(u) === group.kind);
        if (people.length === 0) return null;
        return (
          <div key={group.kind}>
            <p className="px-2 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              {group.title} · {people.length}
            </p>
            {people.map((u) => (
              <button
                key={u.id}
                type="button"
                onClick={() => onSelect(u.id)}
                aria-current={u.id === selectedId}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors",
                  u.id === selectedId
                    ? "bg-primary/10 shadow-[inset_3px_0_0_hsl(var(--primary))]"
                    : "hover:bg-muted/60",
                  !u.is_active && "opacity-50",
                )}
              >
                <Avatar user={u} />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{u.name}</span>
                  <MetaLine user={u} roles={roles} />
                </span>
              </button>
            ))}
          </div>
        );
      })}
    </div>
  );
}
