"use client";

import * as React from "react";
import { toast } from "sonner";
import { KeyRound, Lock, Monitor, Palette, Sparkles, User, Wrench } from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { useAuthStore } from "@/lib/auth";
import { systemApi, type SystemInfo } from "@/lib/api";
import {
  SettingsTabs,
  settingsPanelId,
  settingsTabId,
  type SettingsTabGroup,
} from "@/components/settings/settings-tabs";
import { ProfileTab } from "@/components/settings/profile-tab";
import { PasswordTab } from "@/components/settings/password-tab";
import { AppearanceTab } from "@/components/settings/appearance-tab";
import { ApiTokensTab } from "@/components/settings/api-tokens-tab";
import { MaintenanceCard } from "@/components/settings/maintenance-tab";
import { AiConfigCard } from "@/components/settings/ai-assistant-tab";
import { SystemTab } from "@/components/settings/system-tab";

type TabId =
  | "profile"
  | "password"
  | "appearance"
  | "api-tokens"
  | "maintenance"
  | "ai-assistant"
  | "system";

const DEFAULT_TAB: TabId = "profile";

/** The tab named in the address bar, if any: /settings?tab=api-tokens */
function tabInAddress(): string | null {
  return new URLSearchParams(window.location.search).get("tab");
}

export default function SettingsPage() {
  const { user } = useAuthStore();
  const isAdmin = !!user?.is_admin;

  // Shared by the API tokens tab and the three server tabs, so loaded once.
  const [info, setInfo] = React.useState<SystemInfo | null>(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    systemApi
      .info()
      .then(setInfo)
      .catch(() => toast.error("Failed to load system configuration"))
      .finally(() => setLoading(false));
  }, []);

  const groups = React.useMemo<SettingsTabGroup<TabId>[]>(
    () => [
      {
        label: "Account",
        tabs: [
          { id: "profile", label: "Profile", icon: User },
          // Accounts that sign in through a provider have no password here.
          ...(user?.auth_provider === "local"
            ? [{ id: "password" as const, label: "Password", icon: Lock }]
            : []),
          { id: "appearance", label: "Appearance", icon: Palette },
          { id: "api-tokens", label: "API tokens", icon: KeyRound },
        ],
      },
      {
        label: "Server",
        tabs: [
          ...(isAdmin
            ? [{ id: "maintenance" as const, label: "Maintenance", icon: Wrench }]
            : []),
          { id: "ai-assistant", label: "AI assistant", icon: Sparkles },
          { id: "system", label: "System", icon: Monitor },
        ],
      },
    ],
    [user?.auth_provider, isAdmin],
  );
  const available = groups.flatMap((group) => group.tabs.map((tab) => tab.id));

  // The address decides the tab, so a reload, a link and the back button all
  // work. A tab that does not exist, or that this user may not see, falls
  // back to the first one. The address is only known in the browser, so the
  // tabs appear once it has been read: a link to a tab then opens that tab,
  // without first loading the default one.
  const [requested, setRequested] = React.useState<string | null>(null);
  const [addressRead, setAddressRead] = React.useState(false);
  React.useEffect(() => {
    const read = () => setRequested(tabInAddress());
    read();
    setAddressRead(true);
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, []);
  const active = available.find((id) => id === requested) ?? DEFAULT_TAB;

  function selectTab(id: TabId) {
    if (id === active) return;
    setRequested(id);
    window.history.pushState(
      null,
      "",
      id === DEFAULT_TAB ? window.location.pathname : `?tab=${id}`,
    );
  }

  // A tab is loaded when first opened and then kept, so what was typed into
  // it is still there after a look at another tab.
  const [opened, setOpened] = React.useState<TabId[]>([]);
  React.useEffect(() => {
    if (!addressRead) return;
    setOpened((current) => (current.includes(active) ? current : [...current, active]));
  }, [active, addressRead]);

  const panels: Record<TabId, () => React.ReactNode> = {
    profile: () => <ProfileTab />,
    password: () => <PasswordTab />,
    appearance: () => <AppearanceTab />,
    "api-tokens": () => <ApiTokensTab info={info} />,
    maintenance: () => (
      <MaintenanceCard
        info={info}
        loading={loading}
        onUpdated={(m) => {
          if (info) setInfo({ ...info, maintenance: m });
        }}
      />
    ),
    "ai-assistant": () => (
      <AiConfigCard
        info={info}
        loading={loading}
        isAdmin={isAdmin}
        onUpdated={(ai) => {
          if (info) setInfo({ ...info, ai });
        }}
      />
    ),
    system: () => <SystemTab info={info} loading={loading} />,
  };

  return (
    <AppLayout>
      <div className="mx-auto max-w-5xl space-y-6">
        <div>
          <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
            Settings
          </h1>
          <p className="text-sm text-muted-foreground sm:text-base">
            Your account, and how this server is configured.
          </p>
        </div>

        {addressRead && (
          <div className="md:grid md:grid-cols-[12rem_minmax(0,1fr)] md:items-start md:gap-8">
            <SettingsTabs groups={groups} value={active} onChange={selectTab} />

            <div className="mt-4 min-w-0 max-w-3xl md:mt-0">
              {available
                .filter((id) => id === active || opened.includes(id))
                .map((id) => (
                  <div
                    key={id}
                    role="tabpanel"
                    id={settingsPanelId(id)}
                    aria-labelledby={settingsTabId(id)}
                    hidden={id !== active}
                  >
                    {panels[id]()}
                  </div>
                ))}
            </div>
          </div>
        )}
      </div>
    </AppLayout>
  );
}
