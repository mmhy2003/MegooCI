"use client";

import * as React from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export interface SettingsTab<Id extends string = string> {
  id: Id;
  label: string;
  icon: LucideIcon;
}

export interface SettingsTabGroup<Id extends string = string> {
  label: string;
  tabs: SettingsTab<Id>[];
}

/** The id of the panel a tab controls, and of the tab that labels a panel. */
export function settingsPanelId(id: string): string {
  return `settings-panel-${id}`;
}

export function settingsTabId(id: string): string {
  return `settings-tab-${id}`;
}

/**
 * The list of settings tabs, in groups. A column beside the content on wide
 * screens; on narrow ones a single row above it that scrolls sideways.
 *
 * Arrow keys, Home and End move between tabs, as in any tab list.
 */
export function SettingsTabs<Id extends string>({
  groups,
  value,
  onChange,
}: {
  groups: SettingsTabGroup<Id>[];
  value: Id;
  onChange: (id: Id) => void;
}) {
  const buttons = React.useRef(new Map<Id, HTMLButtonElement>());
  const order = groups.flatMap((group) => group.tabs.map((tab) => tab.id));

  // Keep the current tab in sight when the row has scrolled sideways.
  React.useEffect(() => {
    buttons.current.get(value)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [value]);

  function handleKeyDown(e: React.KeyboardEvent) {
    const step =
      e.key === "ArrowDown" || e.key === "ArrowRight"
        ? 1
        : e.key === "ArrowUp" || e.key === "ArrowLeft"
          ? -1
          : 0;
    let next: Id | undefined;
    if (step) {
      next = order[(order.indexOf(value) + step + order.length) % order.length];
    } else if (e.key === "Home") {
      next = order[0];
    } else if (e.key === "End") {
      next = order[order.length - 1];
    }
    if (next === undefined) return;
    e.preventDefault();
    onChange(next);
    buttons.current.get(next)?.focus();
  }

  return (
    <div
      role="tablist"
      aria-label="Settings"
      aria-orientation="vertical"
      onKeyDown={handleKeyDown}
      className="flex gap-1 overflow-x-auto pb-2 md:sticky md:top-0 md:flex-col md:gap-0 md:overflow-visible md:pb-0"
    >
      {groups.map((group, index) => (
        <React.Fragment key={group.label}>
          <div
            role="presentation"
            className={cn(
              "hidden px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground md:block",
              index > 0 && "md:mt-5",
            )}
          >
            {group.label}
          </div>
          {group.tabs.map((tab) => {
            const selected = tab.id === value;
            return (
              <button
                key={tab.id}
                ref={(element) => {
                  if (element) buttons.current.set(tab.id, element);
                  else buttons.current.delete(tab.id);
                }}
                type="button"
                role="tab"
                id={settingsTabId(tab.id)}
                aria-selected={selected}
                aria-controls={settingsPanelId(tab.id)}
                tabIndex={selected ? 0 : -1}
                onClick={() => onChange(tab.id)}
                className={cn(
                  "flex shrink-0 items-center gap-2.5 whitespace-nowrap rounded-md px-3 py-2 text-left text-sm transition-colors",
                  "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                  selected
                    ? "bg-primary/10 font-medium text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <tab.icon className="h-4 w-4 shrink-0" />
                {tab.label}
              </button>
            );
          })}
        </React.Fragment>
      ))}
    </div>
  );
}
