"use client";

import * as React from "react";
import { ArrowLeft } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { Role, UserDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { UserDetailPane } from "./UserDetailPane";
import { UserList } from "./UserList";
import { selectedUser } from "./access";

export function UsersTab({
  users,
  roles,
  loading,
  onChanged,
}: {
  users: UserDetail[];
  roles: Role[];
  loading: boolean;
  onChanged: () => void;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [pinned, setPinned] = React.useState<string | null>(null);

  const setUserParam = (id: string | null) => {
    const params = new URLSearchParams(searchParams.toString());
    if (id) params.set("user", id);
    else params.delete("user");
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  };

  if (loading) return <Skeleton className="h-[480px] w-full" />;
  if (users.length === 0) {
    return <p className="py-10 text-center text-muted-foreground">No users found.</p>;
  }

  // ?user wins; a stale or missing one keeps whoever is on screen, so a
  // change that moves them to another group does not switch the pane.
  const chosen = users.find((u) => u.id === searchParams.get("user")) ?? null;
  const selected = selectedUser(users, searchParams.get("user"), pinned);
  if (selected.id !== pinned) setPinned(selected.id);

  return (
    <div className="grid overflow-hidden rounded-lg border md:min-h-[560px] md:grid-cols-[280px_1fr]">
      <div className={cn("border-b bg-card md:border-b-0 md:border-r", chosen && "hidden md:block")}>
        <UserList users={users} roles={roles} selectedId={selected.id} onSelect={setUserParam} />
      </div>
      <div className={cn(!chosen && "hidden md:block")}>
        <div className="px-4 pt-3 md:hidden">
          <Button variant="ghost" size="sm" className="gap-1.5" onClick={() => setUserParam(null)}>
            <ArrowLeft className="h-4 w-4" /> All users
          </Button>
        </div>
        <UserDetailPane
          key={selected.id}
          user={selected}
          roles={roles}
          users={users}
          onChanged={onChanged}
        />
      </div>
    </div>
  );
}
