"use client";

import { ComponentType } from "react";
import { MonitorCog, MoonStar, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ThemeMode } from "@/lib/types";

type Props = {
  value: ThemeMode;
  onChange: (next: ThemeMode) => void;
};

const OPTIONS: Array<{ value: ThemeMode; label: string; icon: ComponentType<{ className?: string }> }> = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: MoonStar },
  { value: "system", label: "System", icon: MonitorCog }
];

export function ThemeToggle({ value, onChange }: Props) {
  return (
    <div className="flex flex-wrap gap-2">
      {OPTIONS.map((option) => {
        const Icon = option.icon;
        return (
          <Button
            key={option.value}
            type="button"
            size="sm"
            variant={value === option.value ? "accent" : "secondary"}
            className="gap-1.5"
            onClick={() => onChange(option.value)}
          >
            <Icon className="h-3.5 w-3.5" />
            {option.label}
          </Button>
        );
      })}
    </div>
  );
}
