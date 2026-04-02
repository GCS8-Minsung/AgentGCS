import * as React from "react";

import { cn } from "@/lib/utils";

export const Card = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      "rounded-3xl border border-white/80 bg-white/60 p-4 backdrop-blur-xl dark:border-white/20 dark:bg-slate-900/60",
      className
    )}
    style={{
      boxShadow:
        "0 10px 35px rgba(255, 160, 122, 0.12), inset 0 0 20px rgba(255, 255, 255, 0.65)"
    }}
    {...props}
  />
));
Card.displayName = "Card";

export const CardTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h3
    ref={ref}
    className={cn("text-base font-semibold tracking-tight text-gray-800 dark:text-slate-100", className)}
    {...props}
  />
));
CardTitle.displayName = "CardTitle";

export const CardDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p
    ref={ref}
    className={cn("text-sm text-gray-600 dark:text-slate-300/85", className)}
    {...props}
  />
));
CardDescription.displayName = "CardDescription";
