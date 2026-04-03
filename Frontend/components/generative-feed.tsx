"use client";

import { useMemo } from "react";
import { Loader2, MessageCircleDashed, Search, Sparkles, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { AgentEvent } from "@/lib/types";

type Props = {
  events: AgentEvent[];
  running: boolean;
};

function formatPayload(payload: Record<string, unknown> | null | undefined) {
  if (!payload || typeof payload !== "object") return "(payload 없음)";
  if (typeof payload.message === "string") return payload.message;
  if (typeof payload.summary === "string") return payload.summary;
  return JSON.stringify(payload);
}

function eventIcon(type: string) {
  if (type.includes("started")) return <Loader2 className="h-4 w-4 animate-spin text-orange-500" />;
  if (type.includes("discovery")) return <Search className="h-4 w-4 text-amber-500" />;
  if (type.includes("debate")) return <MessageCircleDashed className="h-4 w-4 text-orange-500" />;
  if (type.includes("failed")) return <TriangleAlert className="h-4 w-4 text-red-600" />;
  return <Sparkles className="h-4 w-4 text-orange-900/70" />;
}

export function GenerativeFeed({ events, running }: Props) {
  const sorted = useMemo(() => {
    const recent = events
      .filter((event) => event.event_type !== "chat.processing")
      .slice(-80);
    return [...recent].reverse();
  }, [events]);
  return (
    <Card className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <CardTitle>Generative UI Stream</CardTitle>
          <CardDescription>
            스피너, 토스트, 토론 상태를 WebSocket으로 실시간 렌더링합니다.
          </CardDescription>
        </div>
        {running ? <Badge className="bg-orange-200/60">Running</Badge> : <Badge>Idle</Badge>}
      </div>
      <div className="max-h-[460px] space-y-2 overflow-y-auto rounded-2xl border border-white/60 bg-white/35 p-3">
        {sorted.length === 0 && (
          <p className="text-sm text-gray-600">
            아직 이벤트가 없습니다. 과제를 시작하면 에이전트 대화가 실시간으로 표시됩니다.
          </p>
        )}
        {sorted.map((event, index) => (
          <article
            key={`${event.timestamp ?? "na"}-${index}`}
            className="rounded-2xl border border-white/70 bg-white/70 p-3"
          >
            <header className="mb-2 flex items-center gap-2 text-xs text-orange-900/70">
              {eventIcon(event.event_type)}
              <span className="font-semibold">{event.event_type}</span>
              {event.timestamp && <span>{new Date(event.timestamp).toLocaleTimeString()}</span>}
            </header>
            <p className="whitespace-pre-wrap text-sm text-gray-800">
              {formatPayload(event.payload)}
            </p>
            {event.event_type === "deep_task.debate_turn" && (
              <p className="mt-2 text-xs text-orange-900/70">
                Persona: {String(event.payload?.persona_label ?? "-")} / Score:{" "}
                {String(event.payload?.weight_score ?? "-")}
              </p>
            )}
          </article>
        ))}
      </div>
    </Card>
  );
}
