import * as React from "react";

import { cn } from "@/lib/utils";

export function Badge({
  className,
  children
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full bg-surface-100 px-2.5 py-1 text-xs font-semibold text-surface-800",
        className
      )}
    >
      {children}
    </span>
  );
}

