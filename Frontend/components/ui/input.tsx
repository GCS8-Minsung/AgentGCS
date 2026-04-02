import * as React from "react";

import { cn } from "@/lib/utils";

export const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => (
  <input
    className={cn(
      "h-10 w-full rounded-2xl border border-white/80 bg-white/55 px-3 text-sm text-gray-800 shadow-sm outline-none placeholder:text-orange-900/35 focus:border-orange-300 focus:ring-2 focus:ring-orange-200/50",
      className
    )}
    style={{
      backdropFilter: "blur(10px)"
    }}
    ref={ref}
    {...props}
  />
));
Input.displayName = "Input";
