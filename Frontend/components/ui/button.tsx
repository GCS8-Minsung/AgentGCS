"use client";

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap rounded-2xl text-sm font-semibold transition-transform focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-300 disabled:pointer-events-none disabled:opacity-50 active:translate-y-[1px]",
  {
    variants: {
      variant: {
        default:
          "border border-white/60 bg-gradient-to-r from-orange-400 to-amber-400 text-white shadow-[0_10px_22px_-12px_rgba(255,160,122,0.7)] hover:brightness-95",
        secondary:
          "border border-white/80 bg-white/55 text-orange-900/85 hover:bg-white/70 dark:border-white/25 dark:bg-slate-800/70 dark:text-slate-100 dark:hover:bg-slate-700/70",
        ghost:
          "bg-transparent text-orange-900/85 hover:bg-white/40 dark:text-slate-100 dark:hover:bg-slate-700/40",
        accent:
          "border border-white/60 bg-gradient-to-r from-amber-300 to-orange-300 text-white shadow-[0_8px_18px_-8px_rgba(245,158,11,0.8)] hover:brightness-95"
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-8 rounded-md px-3",
        lg: "h-11 rounded-lg px-8"
      }
    },
    defaultVariants: {
      variant: "accent",
      size: "default"
    }
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => {
    return (
      <button
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
