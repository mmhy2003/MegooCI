"use client";

import * as React from "react";
import { toast } from "sonner";
import { User, Save } from "lucide-react";
import { useAuthStore } from "@/lib/auth";
import { authApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

export function ProfileTab() {
  const { user } = useAuthStore();
  const [profileName, setProfileName] = React.useState("");
  const [profileEmail, setProfileEmail] = React.useState("");
  const [savingProfile, setSavingProfile] = React.useState(false);
  const [profileDirty, setProfileDirty] = React.useState(false);

  // Sync local profile state when user loads.
  React.useEffect(() => {
    if (user) {
      setProfileName(user.name || "");
      setProfileEmail(user.email || "");
    }
  }, [user]);

  // Track dirty state.
  React.useEffect(() => {
    if (!user) return;
    setProfileDirty(
      profileName.trim() !== (user.name || "") ||
      profileEmail.trim().toLowerCase() !== (user.email || ""),
    );
  }, [profileName, profileEmail, user]);

  async function handleSaveProfile(e: React.FormEvent) {
    e.preventDefault();
    if (!user) return;
    const updates: { name?: string; email?: string } = {};
    const trimmedName = profileName.trim();
    const trimmedEmail = profileEmail.trim().toLowerCase();
    if (trimmedName !== user.name) updates.name = trimmedName;
    if (trimmedEmail !== user.email) updates.email = trimmedEmail;
    if (!Object.keys(updates).length) return;

    if (!trimmedName) {
      toast.error("Name cannot be empty");
      return;
    }

    setSavingProfile(true);
    try {
      const updatedUser = await authApi.updateProfile(updates);
      // Refresh the auth store with updated user data.
      useAuthStore.setState({ user: updatedUser });
      toast.success("Profile updated");
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ??
        (err instanceof Error ? err.message : "Failed to update profile");
      toast.error(detail);
    } finally {
      setSavingProfile(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <User className="h-5 w-5" />
          Profile
        </CardTitle>
        <CardDescription>
          Your personal account information.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSaveProfile} className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Full name</label>
            <Input
              value={profileName}
              onChange={(e) => setProfileName(e.target.value)}
              placeholder="Your name"
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Email</label>
            <Input
              type="email"
              value={profileEmail}
              onChange={(e) => setProfileEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Role</label>
            <div>
              <Badge variant="secondary">
                {user?.role || (user?.is_admin ? "admin" : "user")}
              </Badge>
            </div>
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Account status</label>
            <div>
              <Badge variant={user?.is_active ? "success" : "failed"}>
                {user?.is_active ? "Active" : "Inactive"}
              </Badge>
            </div>
          </div>
          <Button type="submit" disabled={!profileDirty || savingProfile}>
            {savingProfile ? "Saving..." : "Save changes"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
