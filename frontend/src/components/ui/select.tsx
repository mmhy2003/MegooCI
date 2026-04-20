import * as React from "react";
import { cn } from "@/lib/utils";
import { ChevronDown } from "lucide-react";

export interface SelectProps
  extends React.SelectHTMLAttributes<HTMLSelectElement> {
  options: { value: string; label: string }[];
  placeholder?: string;
}

const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, options, placeholder, ...props }, ref) => {
    return (
      <div className="relative">
        <select
          className={cn(
            // Base field styling. `color-scheme` is set light/dark via the
            // document root in `globals.css` so the OS-rendered option
            // popup picks up the theme automatically (Chrome/Firefox/Safari).
            "flex h-9 w-full appearance-none rounded-md border border-input",
            "bg-background text-foreground px-3 py-1 pr-8 text-sm shadow-sm",
            "transition-colors focus-visible:outline-none focus-visible:ring-1",
            "focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
            // Explicitly color each <option>. Browsers honor this on <option>
            // (though not much else), which gives us readable dropdown rows
            // in dark mode independent of the OS's native theme.
            "[&>option]:bg-popover [&>option]:text-popover-foreground",
            className,
          )}
          ref={ref}
          {...props}
        >
          {placeholder && (
            <option value="" disabled>
              {placeholder}
            </option>
          )}
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
      </div>
    );
  },
);
Select.displayName = "Select";

export { Select };
