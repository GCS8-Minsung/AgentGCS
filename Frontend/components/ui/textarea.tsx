import * as React from "react";

import { cn } from "@/lib/utils";

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    className={cn(
      "min-h-[84px] w-full rounded-xl border border-surface-100 bg-white px-3 py-2 text-sm text-surface-900 shadow-sm outline-none placeholder:text-surface-800/45 focus:border-accent-blue focus:ring-2 focus:ring-accent-blue/20",
      className
    )}
    ref={ref}
    {...props}
  />
));
Textarea.displayName = "Textarea";

