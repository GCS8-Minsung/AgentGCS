import * as React from "react";

import { cn } from "@/lib/utils";

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    className={cn(
      "min-h-[84px] w-full rounded-2xl border border-white/80 bg-white/55 px-3 py-2 text-sm text-gray-800 shadow-sm outline-none placeholder:text-orange-900/35 focus:border-orange-300 focus:ring-2 focus:ring-orange-200/50 dark:border-white/20 dark:bg-slate-800/70 dark:text-slate-100 dark:placeholder:text-slate-300/40",
      className
    )}
    style={{
      backdropFilter: "blur(10px)"
    }}
    ref={ref}
    {...props}
  />
));
Textarea.displayName = "Textarea";
