import * as React from "react";

import { cn } from "@/lib/utils";

export const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => (
  <input
    className={cn(
      "h-10 w-full rounded-lg border border-surface-100 bg-white px-3 text-sm text-surface-900 shadow-sm outline-none placeholder:text-surface-800/45 focus:border-accent-blue focus:ring-2 focus:ring-accent-blue/20",
      className
    )}
    ref={ref}
    {...props}
  />
));
Input.displayName = "Input";

