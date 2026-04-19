import * as React from "react";
import { cn } from "@/lib/utils";

interface ScrollAreaProps extends React.HTMLAttributes<HTMLDivElement> {
  maxHeight?: string;
}

const ScrollArea = React.forwardRef<HTMLDivElement, ScrollAreaProps>(
  ({ className, maxHeight, children, style, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("relative overflow-auto", className)}
      style={{ maxHeight, ...style }}
      {...props}
    >
      {children}
    </div>
  ),
);
ScrollArea.displayName = "ScrollArea";

export { ScrollArea };
