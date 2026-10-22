"use client";

import type { Role, UserDetail } from "@/lib/api";
import { projectRows } from "./access";

export function AccessTab({
  user,
}: {
  user: UserDetail;
  roles: Role[];
  users: UserDetail[];
  onChanged: () => void;
}) {
  const count = projectRows(user).length;
  return (
    <p className="text-sm text-muted-foreground">
      {count} project{count === 1 ? "" : "s"}
    </p>
  );
}
