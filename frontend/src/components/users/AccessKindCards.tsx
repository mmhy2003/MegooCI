"use client";

import { cn } from "@/lib/utils";

export type AccessChoice = "administrator" | "member";

const CHOICES: { value: AccessChoice; title: string; text: string }[] = [
  {
    value: "administrator",
    title: "Administrator",
    text: "Everything, on every project, including users, integrations, backups and settings.",
  },
  {
    value: "member",
    title: "Member",
    text: "Only the projects below, with the role given on each.",
  },
];

/** The one choice between Administrator and Member. `null` selects neither,
 *  for a user whose administrator access is half set up. */
export function AccessKindCards({
  value,
  onChange,
  disabled = false,
  memberText,
}: {
  value: AccessChoice | null;
  onChange: (value: AccessChoice) => void;
  disabled?: boolean;
  /** Replaces the Member description where there is no project list below. */
  memberText?: string;
}) {
  return (
    <div role="radiogroup" className="grid gap-2 sm:grid-cols-2">
      {CHOICES.map((choice) => {
        const selected = value === choice.value;
        return (
          <button
            key={choice.value}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            onClick={() => !selected && onChange(choice.value)}
            className={cn(
              "flex gap-3 rounded-lg border p-3 text-left text-sm transition-colors",
              "disabled:cursor-not-allowed disabled:opacity-60",
              selected
                ? "border-primary bg-primary/5"
                : "border-input hover:border-primary/50",
            )}
          >
            <span
              className={cn(
                "mt-0.5 h-4 w-4 shrink-0 rounded-full border-2",
                selected ? "border-primary bg-primary ring-2 ring-inset ring-background" : "border-input",
              )}
            />
            <span>
              <span className="block font-medium">{choice.title}</span>
              <span className="text-xs text-muted-foreground">
                {choice.value === "member" && memberText ? memberText : choice.text}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
