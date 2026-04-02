"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Bolt, GalleryVerticalEnd, Layers3, RadioTower, Sparkles } from "lucide-react";

import { AIIndicator } from "@/components/agent-ui/ai-indicator";
import { ChatInput } from "@/components/agent-ui/chat-input";
import { ChatMessage } from "@/components/agent-ui/chat-message";
import { ChatSidebar, SidebarItem } from "@/components/agent-ui/chat-sidebar";
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

type ConsoleMessage = {
  id: string;
  content: string;
  role: "user" | "assistant";
  timestamp: Date;
};

type WorkspaceView = "deep_task" | "kanban" | "automation";

function resolveWsBase() {
  if (process.env.NEXT_PUBLIC_BACKEND_WS_URL) return process.env.NEXT_PUBLIC_BACKEND_WS_URL;
  const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";
  return backendUrl.replace("https://", "wss://").replace("http://", "ws://") + "/ws/agents";
}

function summarizeEvent(event: AgentEvent): string | null {
  const payload = event.payload as Record<string, unknown>;
  if (event.event_type === "deep_task.debate_turn") {
    return `[${String(payload.persona_label ?? "Agent")}] ${String(payload.message ?? "")}`;
  }
  if (event.event_type === "deep_task.completed") {
    return `최종 결론\n${String(payload.final_summary ?? "")}`;
  }
  if (typeof payload.summary === "string") return payload.summary;
  if (typeof payload.message === "string") return payload.message;
  if (typeof payload.description === "string") return payload.description;
  return null;
}

function statsByAutonomy(level: number): PersonaStats {
  if (level === 0) {
    return {
      creativity: 40,
      logic: 88,
      critical_thinking: 90,
      data_dependency: 92,
      empathy: 45,
      drive: 55
    };
  }
  if (level === 2) {
    return {
      creativity: 93,
      logic: 68,
      critical_thinking: 70,
      data_dependency: 60,
      empathy: 64,
      drive: 82
    };
  }
  if (level === 3) {
    return {
      creativity: 86,
      logic: 78,
      critical_thinking: 74,
      data_dependency: 69,
      empathy: 58,
      drive: 96
    };
  }
  return DEFAULT_STATS;
}

export default function HomePage() {
  const [userId, setUserId] = useState("00000000-0000-0000-0000-000000000001");
  const [userEmail, setUserEmail] = useState<string | null>("demo@local");
  const [task, setTask] = useState(DEFAULT_TASK);
  const [personaStats, setPersonaStats] = useState<PersonaStats>(DEFAULT_STATS);
  const [messages, setMessages] = useState<ConsoleMessage[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [socketConnected, setSocketConnected] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<WorkspaceView>("deep_task");
  const [personalInstruction, setPersonalInstruction] = useState(
    "이번 주 마감 과제와 발표 준비 일정 정리해서 캘린더 액션 초안 생성해줘"
  );

  const wsRef = useRef<WebSocket | null>(null);

  const appendEvent = useCallback((event: AgentEvent) => {
    setEvents((current) => [...current, event]);
  }, []);

  const appendAssistantMessage = useCallback((content: string) => {
    setMessages((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        content,
        role: "assistant",
        timestamp: new Date()
      }
    ]);
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
          const summary = summarizeEvent(parsed);
          if (summary && parsed.event_type !== "socket.connected") {
            appendAssistantMessage(summary);
          }
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
  }, [appendAssistantMessage, appendEvent, userId]);

  const streamBadge = useMemo(() => (socketConnected ? "실시간 연결됨" : "실시간 연결 끊김"), [socketConnected]);

  async function handleStartDeepTask(taskText: string, autonomyLevel: number) {
    const tunedStats = statsByAutonomy(autonomyLevel);
    setPersonaStats(tunedStats);
    setTask(taskText);
    setRunning(true);
    setActiveView("deep_task");
    setMessages((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        content: taskText,
        role: "user",
        timestamp: new Date()
      }
    ]);
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
        task: taskText,
        personaStats: tunedStats,
        notifyEmail: userEmail ?? undefined,
        useMock: true
      });
      setActiveRunId(response.run_id);
    } catch (error) {
      setRunning(false);
      appendAssistantMessage(`요청 실패: ${(error as Error).message}`);
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
      setActiveView("automation");
      appendEvent({
        event_type: "personal_agent.local_echo",
        timestamp: new Date().toISOString(),
        payload: { message: response.plan }
      });
      appendAssistantMessage(`개인 업무 에이전트 계획\n${response.plan}`);
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

  const sidebarItems: SidebarItem[] = [
    { id: "deep_task", title: "멀티 에이전트 콘솔", subtitle: "토론 실행 및 실시간 스트림" },
    { id: "kanban", title: "칸반 워크스페이스", subtitle: "마일스톤/Task 관리" },
    { id: "automation", title: "업무 자동화 센터", subtitle: "개인 업무 에이전트 트리거" }
  ];

  return (
    <main className="relative flex h-screen w-full overflow-hidden">
      <ToastStack events={events} />

      <div className="pointer-events-none absolute left-[-10%] top-[-10%] h-[40%] w-[40%] rounded-full bg-orange-200/30 blur-[100px]" />
      <div className="pointer-events-none absolute bottom-[-10%] right-[-5%] h-[50%] w-[50%] rounded-full bg-amber-100/40 blur-[120px]" />
      <div className="pointer-events-none absolute right-[10%] top-[20%] h-[30%] w-[30%] rounded-full bg-white/60 blur-[80px]" />

      <ChatSidebar
        items={sidebarItems}
        activeItem={activeView}
        onSelectItem={(id) => setActiveView(id as WorkspaceView)}
        onReset={() => {
          setMessages([]);
          setEvents([]);
          setActiveRunId(null);
          setRunning(false);
          setTask(DEFAULT_TASK);
          setPersonaStats(DEFAULT_STATS);
        }}
      />

      <section className="relative z-10 flex min-h-0 flex-1 flex-col">
        <header className="glass-panel m-4 mb-3 rounded-3xl p-4 md:p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="text-2xl font-bold text-gray-800 md:text-3xl">
                AgentGCS 통합 워크스페이스
              </h1>
              <p className="mt-1 text-sm text-orange-900/65">
                AgentGCS_UI 디자인 기반으로 멀티 에이전트, 자동화, 칸반 기능을 통합했습니다.
              </p>
            </div>
            <div className="rounded-full bg-white/80 px-3 py-1 text-xs font-semibold text-orange-900/80">
              {streamBadge}
            </div>
          </div>
          <div className="mt-3 flex flex-wrap gap-2 lg:hidden">
            {sidebarItems.map((item) => (
              <Button
                key={item.id}
                variant={activeView === item.id ? "accent" : "secondary"}
                size="sm"
                onClick={() => setActiveView(item.id as WorkspaceView)}
              >
                {item.title}
              </Button>
            ))}
          </div>
        </header>

        {activeView === "kanban" ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
            <KanbanBoard userId={userId} />
          </div>
        ) : (
          <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 px-4 pb-4 xl:grid-cols-[1fr_410px]">
            <div className="glass-panel flex min-h-0 flex-col rounded-3xl">
              <AnimatePresence>
                {messages.length > 0 && (
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="min-h-0 flex-1 space-y-6 overflow-y-auto px-6 py-6 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-orange-300/35 [&::-webkit-scrollbar-track]:bg-transparent"
                  >
                    {messages.map((message) => (
                      <ChatMessage
                        key={message.id}
                        content={message.content}
                        role={message.role}
                        timestamp={message.timestamp}
                      />
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>

              <motion.div
                layout
                className="flex w-full flex-col items-center justify-center pb-6 pt-2"
                style={{ flex: messages.length > 0 ? "0 0 auto" : "1 1 auto" }}
                transition={{ type: "spring", damping: 25, stiffness: 200 }}
              >
                {messages.length === 0 && (
                  <motion.div
                    initial={{ scale: 0, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    transition={{ delay: 0.2 }}
                    className="mb-8"
                  >
                    <AIIndicator
                      isActive={false}
                      isChatStarted={false}
                      onOptionSelect={(text) => handleStartDeepTask(text, 1)}
                    />
                  </motion.div>
                )}
                {messages.length > 0 && (
                  <motion.div
                    initial={{ scale: 0, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    className="mb-4"
                  >
                    <AIIndicator
                      isActive={running}
                      isChatStarted
                      onOptionSelect={(text) => handleStartDeepTask(text, 1)}
                    />
                  </motion.div>
                )}

                <ChatInput
                  onSend={(text, autonomyLevel) => handleStartDeepTask(text, autonomyLevel)}
                  isCenter={messages.length === 0}
                  disabled={running}
                />
              </motion.div>
            </div>

            <div className="min-h-0 space-y-4 overflow-y-auto pb-2 pr-1">
              <GoogleSignInCard
                onUserChange={(nextUserId, nextEmail) => {
                  setUserId(nextUserId);
                  setUserEmail(nextEmail ?? null);
                }}
              />
              <Card className="space-y-2">
                <CardTitle>시나리오 빠른 실행</CardTitle>
                <CardDescription>
                  기본 과제를 즉시 실행하거나 커스텀 과제로 덮어쓸 수 있습니다.
                </CardDescription>
                <Textarea value={task} onChange={(event) => setTask(event.target.value)} />
                <div className="flex flex-wrap gap-2">
                  <Button onClick={() => handleStartDeepTask(task, 1)}>기본 스탯으로 실행</Button>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setTask(DEFAULT_TASK);
                      setPersonaStats(DEFAULT_STATS);
                    }}
                  >
                    기본값 복원
                  </Button>
                </div>
                {activeRunId && (
                  <p className="rounded-xl bg-white/70 px-3 py-2 font-mono text-xs text-orange-900/80">
                    run_id: {activeRunId}
                  </p>
                )}
              </Card>

              <PersonaRadar value={personaStats} onChange={setPersonaStats} />

              <Card className="space-y-3">
                <CardTitle>개인 업무 자동화</CardTitle>
                <CardDescription>
                  Gmail/Calendar/교내 API 실행 계획을 수동 트리거합니다.
                </CardDescription>
                <Textarea
                  value={personalInstruction}
                  onChange={(event) => setPersonalInstruction(event.target.value)}
                />
                <Button onClick={handlePersonalTrigger} className="gap-2">
                  <Bolt className="h-4 w-4" />
                  개인 업무 에이전트 실행
                </Button>
              </Card>

              <GenerativeFeed events={events} running={running} />

              <Card className="space-y-3">
                <CardTitle>워크플로우 상태</CardTitle>
                <CardDescription>
                  정보 탐색 → 5인 토론 → 결론 → NotebookLM 후처리 → Drive/Gmail 통보
                </CardDescription>
                <div className="grid grid-cols-3 gap-2 text-xs text-orange-900/80">
                  <div className="rounded-xl border border-white/80 bg-white/55 p-3">
                    <RadioTower className="mb-2 h-4 w-4" />
                    실시간 WebSocket
                  </div>
                  <div className="rounded-xl border border-white/80 bg-white/55 p-3">
                    <Layers3 className="mb-2 h-4 w-4" />
                    Persona 동적 주입
                  </div>
                  <div className="rounded-xl border border-white/80 bg-white/55 p-3">
                    <GalleryVerticalEnd className="mb-2 h-4 w-4" />
                    UI Action Stream
                  </div>
                </div>
              </Card>
            </div>
          </div>
        )}

        {activeView === "automation" && (
          <div className="px-4 pb-4">
            <Card className="space-y-3">
              <CardTitle>자동화 센터 보조 패널</CardTitle>
              <CardDescription>
                현재 이벤트 기준 추천: 이메일 통보 정책/웹훅 룰/개인 API 키 저장을 우선 구성하세요.
              </CardDescription>
              <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
                <div className="rounded-xl border border-white/80 bg-white/50 p-3 text-sm text-orange-900/80">
                  <Sparkles className="mb-1 h-4 w-4" />
                  마감 임박 토스트 규칙 점검
                </div>
                <div className="rounded-xl border border-white/80 bg-white/50 p-3 text-sm text-orange-900/80">
                  <Bolt className="mb-1 h-4 w-4" />
                  개인 업무 프롬프트 템플릿 관리
                </div>
                <div className="rounded-xl border border-white/80 bg-white/50 p-3 text-sm text-orange-900/80">
                  <RadioTower className="mb-1 h-4 w-4" />
                  실시간 알림 채널 테스트
                </div>
              </div>
            </Card>
          </div>
        )}
      </section>
    </main>
  );
}
