"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { AgentEvent } from "@/lib/types";

type ToastItem = {
  id: string;
  title: string;
  description: string;
  href?: string;
  createdAt: number;
};

type Props = {
  events: AgentEvent[];
};

export function ToastStack({ events }: Props) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  useEffect(() => {
    const last = events[events.length - 1];
    if (!last || last.event_type !== "toast.notification") return;
    const payload = last.payload as {
      title?: string;
      description?: string;
      action?: { href?: string };
    };
    setToasts((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        title: payload.title ?? "알림",
        description: payload.description ?? "",
        href: payload.action?.href,
        createdAt: Date.now()
      }
    ]);
  }, [events]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      const now = Date.now();
      setToasts((current) => current.filter((toast) => now - toast.createdAt < 6000));
    }, 800);
    return () => window.clearInterval(timer);
  }, []);

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex max-w-[360px] flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className="rounded-xl border border-surface-100 bg-white/95 p-3 shadow-panel backdrop-blur"
        >
          <p className="text-sm font-semibold text-surface-900">{toast.title}</p>
          <p className="mt-1 text-xs text-surface-800/85">{toast.description}</p>
          {toast.href && (
            <a href={toast.href} target="_blank" rel="noreferrer" className="mt-2 inline-flex">
              <Button size="sm" variant="secondary">
                이동
              </Button>
            </a>
          )}
        </div>
      ))}
    </div>
  );
}

