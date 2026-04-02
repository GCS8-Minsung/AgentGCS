import * as React from "react";

import { cn } from "@/lib/utils";

export function Badge({
  className,
  children
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border border-white/80 bg-white/60 px-2.5 py-1 text-xs font-semibold text-orange-900/85",
        className
      )}
    >
      {children}
    </span>
  );
}
