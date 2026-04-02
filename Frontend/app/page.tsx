"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bolt, Play, RadioTower, WandSparkles } from "lucide-react";

import { GoogleSignInCard } from "@/components/auth/google-sign-in";
import { GenerativeFeed } from "@/components/generative-feed";
import { KanbanBoard } from "@/components/kanban-board";
import { PersonaRadar } from "@/components/persona-radar";
import { ToastStack } from "@/components/toast-stack";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { startDeepTask, triggerPersonalAgent } from "@/lib/api";
import { AgentEvent, PersonaStats } from "@/lib/types";

const DEFAULT_TASK =
  "볼류메트릭 디스플레이 3D 식물/크리처 렌더링 테스트에 대한 비즈니스 모델 구축";

const DEFAULT_STATS: PersonaStats = {
  creativity: 82,
  logic: 76,
  critical_thinking: 79,
  data_dependency: 71,
  empathy: 48,
  drive: 84
};

function resolveWsBase() {
  if (process.env.NEXT_PUBLIC_BACKEND_WS_URL) return process.env.NEXT_PUBLIC_BACKEND_WS_URL;
  const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";
  return backendUrl.replace("https://", "wss://").replace("http://", "ws://") + "/ws/agents";
}

export default function HomePage() {
  const [userId, setUserId] = useState("00000000-0000-0000-0000-000000000001");
  const [userEmail, setUserEmail] = useState<string | null>("demo@local");
  const [task, setTask] = useState(DEFAULT_TASK);
  const [personaStats, setPersonaStats] = useState<PersonaStats>(DEFAULT_STATS);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [socketConnected, setSocketConnected] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [personalInstruction, setPersonalInstruction] = useState(
    "이번 주 마감 과제와 발표 준비 일정 정리해서 캘린더 액션 초안 생성해줘"
  );

  const wsRef = useRef<WebSocket | null>(null);

  const appendEvent = useCallback((event: AgentEvent) => {
    setEvents((current) => [...current, event]);
  }, []);

  useEffect(() => {
    if (!userId) return;
    const base = resolveWsBase();
    const ws = new WebSocket(`${base}?user_id=${encodeURIComponent(userId)}`);
    wsRef.current = ws;

    const heartbeat = window.setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send("ping");
      }
    }, 15000);

    ws.onopen = () => {
      setSocketConnected(true);
    };

    ws.onmessage = (message) => {
      try {
        const parsed = JSON.parse(message.data) as AgentEvent;
        if (parsed.event_type) {
          appendEvent(parsed);
          if (parsed.event_type === "deep_task.completed" || parsed.event_type === "deep_task.failed") {
            setRunning(false);
          }
        }
      } catch {
        // ignore non-json heartbeat packets
      }
    };

    ws.onclose = () => {
      setSocketConnected(false);
      window.clearInterval(heartbeat);
    };

    return () => {
      window.clearInterval(heartbeat);
      ws.close();
      wsRef.current = null;
    };
  }, [appendEvent, userId]);

  const streamBadge = useMemo(
    () => (socketConnected ? "WebSocket Connected" : "WebSocket Disconnected"),
    [socketConnected]
  );

  async function handleStartDeepTask() {
    setRunning(true);
    appendEvent({
      event_type: "ui.request_submitted",
      timestamp: new Date().toISOString(),
      payload: {
        message: "프론트엔드에서 멀티 에이전트 토론 시작 요청을 전송했습니다."
      }
    });
    try {
      const response = await startDeepTask({
        userId,
        task,
        personaStats,
        notifyEmail: userEmail ?? undefined,
        useMock: true
      });
      setActiveRunId(response.run_id);
    } catch (error) {
      setRunning(false);
      appendEvent({
        event_type: "deep_task.failed",
        timestamp: new Date().toISOString(),
        payload: { message: (error as Error).message }
      });
    }
  }

  async function handlePersonalTrigger() {
    try {
      const response = await triggerPersonalAgent({ userId, instruction: personalInstruction });
      appendEvent({
        event_type: "personal_agent.local_echo",
        timestamp: new Date().toISOString(),
        payload: { message: response.plan }
      });
    } catch (error) {
      appendEvent({
        event_type: "toast.notification",
        timestamp: new Date().toISOString(),
        payload: {
          title: "개인 업무 에이전트 오류",
          description: (error as Error).message
        }
      });
    }
  }

  return (
    <main className="mx-auto max-w-[1420px] p-4 md:p-8">
      <ToastStack events={events} />

      <header className="mb-4 rounded-2xl border border-white/60 bg-white/75 p-5 shadow-panel backdrop-blur-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-surface-900 md:text-3xl">
              AgentGCS: 자율형 멀티 에이전트 업무 자동화
            </h1>
            <p className="mt-1 text-sm text-surface-800/85">
              Next.js + FastAPI + Supabase + LangGraph 구조로 분리된 실시간 작업 공간
            </p>
          </div>
          <div className="rounded-full bg-surface-900 px-3 py-1 text-xs font-semibold text-white">
            {streamBadge}
          </div>
        </div>
      </header>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[1.05fr_0.95fr]">
        <div className="space-y-4">
          <GoogleSignInCard
            onUserChange={(nextUserId, nextEmail) => {
              setUserId(nextUserId);
              setUserEmail(nextEmail ?? null);
            }}
          />

          <Card className="space-y-3">
            <CardTitle>심층 과제 입력</CardTitle>
            <CardDescription>
              테스트 시나리오 기본값이 입력되어 있습니다. 수정 후 토론을 시작하세요.
            </CardDescription>
            <Textarea value={task} onChange={(event) => setTask(event.target.value)} />
            <div className="flex flex-wrap gap-2">
              <Button onClick={handleStartDeepTask} className="gap-2" disabled={running}>
                <Play className="h-4 w-4" />
                멀티 에이전트 토론 시작
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  setTask(DEFAULT_TASK);
                  setPersonaStats(DEFAULT_STATS);
                }}
              >
                기본 시나리오 복원
              </Button>
              {activeRunId && (
                <span className="rounded-full bg-surface-100 px-3 py-2 text-xs font-mono text-surface-900">
                  run_id: {activeRunId}
                </span>
              )}
            </div>
          </Card>

          <PersonaRadar value={personaStats} onChange={setPersonaStats} />

          <Card className="space-y-3">
            <CardTitle>개인 업무 에이전트 수동 트리거</CardTitle>
            <CardDescription>
              Gmail/Calendar/교내 API 호출 계획을 경량 모드로 생성합니다.
            </CardDescription>
            <Textarea
              value={personalInstruction}
              onChange={(event) => setPersonalInstruction(event.target.value)}
            />
            <Button variant="accent" onClick={handlePersonalTrigger} className="gap-2">
              <Bolt className="h-4 w-4" />
              개인 업무 에이전트 실행
            </Button>
          </Card>
        </div>

        <div className="space-y-4">
          <GenerativeFeed events={events} running={running} />
          <Card className="space-y-3">
            <CardTitle>워크플로우 요약</CardTitle>
            <CardDescription>
              정보 탐색 <span className="mx-1">→</span> 5인 토론 <span className="mx-1">→</span>{" "}
              결론 도출 <span className="mx-1">→</span> NotebookLM 전달
            </CardDescription>
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div className="rounded-lg bg-surface-50 p-3">
                <RadioTower className="mb-2 h-4 w-4 text-accent-blue" />
                WebSocket 중계
              </div>
              <div className="rounded-lg bg-surface-50 p-3">
                <WandSparkles className="mb-2 h-4 w-4 text-accent-teal" />
                Persona JSON 주입
              </div>
              <div className="rounded-lg bg-surface-50 p-3">
                <Bolt className="mb-2 h-4 w-4 text-orange-500" />
                Action UI 스트림
              </div>
            </div>
          </Card>
        </div>
      </section>

      <section className="mt-4">
        <KanbanBoard userId={userId} />
      </section>
    </main>
  );
}

