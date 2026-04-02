"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bolt,
  GalleryVerticalEnd,
  Layers3,
  LogOut,
  RadioTower,
  Sparkles,
  Wrench
} from "lucide-react";

import { AIIndicator } from "@/components/agent-ui/ai-indicator";
import { ChatInput } from "@/components/agent-ui/chat-input";
import { ChatMessage } from "@/components/agent-ui/chat-message";
import {
  ChatSidebar,
  ConversationPreview,
  SidebarItem
} from "@/components/agent-ui/chat-sidebar";
import { clearAuthSession, LoginGate } from "@/components/auth/login-gate";
import { GenerativeFeed } from "@/components/generative-feed";
import { KanbanBoard } from "@/components/kanban-board";
import { PerfTrace } from "@/components/perf-trace";
import { SettingsModal } from "@/components/settings-modal";
import { ToastStack } from "@/components/toast-stack";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  agentChat,
  bootstrapUser,
  fetchConnectionStatus,
  fetchConversationMessages,
  fetchConversations,
  fetchWorkspaceSettings,
  listUserKeys,
  saveWorkspaceSettings,
  startDeepTask,
  storeUserKey,
  triggerPersonalAgent
} from "@/lib/api";
import {
  AgentEvent,
  AutonomyMode,
  ConversationMessage,
  PersonaStats,
  UserSettings
} from "@/lib/types";

const DEFAULT_TASK =
  "볼류메트릭 디스플레이 3D 식물/크리처 렌더링 테스트에 대한 비즈니스 모델 구축";
const MAX_MESSAGES = 160;
const MAX_EVENTS = 180;

const DEFAULT_STATS: PersonaStats = {
  creativity: 82,
  logic: 76,
  critical_thinking: 79,
  data_dependency: 71,
  empathy: 48,
  drive: 84
};

const DEFAULT_SETTINGS: UserSettings = {
  theme: "system",
  dev_mode: false,
  claude_base_url: "https://claude.1000.school",
  preferred_model: "claude-3-5-sonnet-20241022",
  default_notify_email: null,
  active_persona_id: "default-balanced",
  personas: [
    {
      id: "default-balanced",
      name: "기본 균형형",
      stats: DEFAULT_STATS
    }
  ],
  approval_policy: {
    cautious_requires_approval: true,
    balanced_requires_approval: true,
    creative_requires_approval: true,
    autonomous_needs_first_warning: true,
    autonomous_warning_accepted: false
  }
};

type AuthSession = {
  userId: string;
  email: string | null;
  fullName: string | null;
  avatarUrl: string | null;
  provider: "google" | "dev";
};

type ConsoleMessage = {
  id: string;
  content: string;
  role: "user" | "assistant";
  timestamp: Date;
};

type WorkspaceView = "multi_agent" | "kanban" | "automation" | "task_automation";

function resolveWsBase() {
  if (process.env.NEXT_PUBLIC_BACKEND_WS_URL) return process.env.NEXT_PUBLIC_BACKEND_WS_URL;
  const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";
  return backendUrl.replace("https://", "wss://").replace("http://", "ws://") + "/ws/agents";
}

function summarizeEvent(event: AgentEvent): string | null {
  const payload =
    event.payload && typeof event.payload === "object"
      ? (event.payload as Record<string, unknown>)
      : {};
  if (event.event_type === "deep_task.debate_turn") {
    return `[${String(payload.persona_label ?? "Agent")}] ${String(payload.message ?? "")}`;
  }
  if (event.event_type === "deep_task.completed") {
    return `최종 결론\n${String(payload.final_summary ?? "")}`;
  }
  if (event.event_type === "deep_task.failed") {
    return `실행 실패\n${String(payload.message ?? payload.description ?? "원인 미상")}`;
  }
  if (event.event_type === "toast.notification" && typeof payload.description === "string") {
    return payload.description;
  }
  return null;
}

function isValidEmail(email: string | null | undefined): email is string {
  if (!email) return false;
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function toConsoleMessages(items: ConversationMessage[]): ConsoleMessage[] {
  return items.map((item) => ({
    id: item.id,
    content: item.content,
    role: item.role === "user" ? "user" : "assistant",
    timestamp: item.created_at ? new Date(item.created_at) : new Date()
  }));
}

function modeLabel(mode: AutonomyMode): string {
  if (mode === "cautious") return "신중함";
  if (mode === "creative") return "창의적";
  if (mode === "autonomous") return "완전자율";
  return "균형형";
}

export default function HomePage() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [settings, setSettings] = useState<UserSettings>(DEFAULT_SETTINGS);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [forceKeySetup, setForceKeySetup] = useState(false);
  const [apiKeys, setApiKeys] = useState<Array<{ id: string; key_name: string; key_version: number }>>([]);
  const [connectionStatus, setConnectionStatus] = useState<{
    claude: {
      configured: boolean;
      base_url?: string | null;
      has_auth_token: boolean;
      has_api_key: boolean;
      reachable: boolean;
      status: string;
      attempts: Array<Record<string, unknown>>;
    };
    school_api: { token_saved: boolean };
  } | null>(null);

  const [messages, setMessages] = useState<ConsoleMessage[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [chatLoading, setChatLoading] = useState(false);
  const [socketConnected, setSocketConnected] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<WorkspaceView>("multi_agent");

  const [conversations, setConversations] = useState<ConversationPreview[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);

  const [task, setTask] = useState(DEFAULT_TASK);
  const [taskMode, setTaskMode] = useState<AutonomyMode>("balanced");
  const [personalInstruction, setPersonalInstruction] = useState(
    "이번 주 마감 과제와 발표 준비 일정 정리해서 캘린더 액션 초안 생성해줘"
  );
  const [loadingWorkspace, setLoadingWorkspace] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);

  const activePersona = useMemo(() => {
    const target = settings.personas.find((persona) => persona.id === settings.active_persona_id);
    return target ?? settings.personas[0] ?? null;
  }, [settings.active_persona_id, settings.personas]);

  const streamBadge = useMemo(
    () => (socketConnected ? "실시간 연결됨" : "실시간 연결 끊김"),
    [socketConnected]
  );

  const userId = session?.userId ?? "";
  const userEmail = session?.email ?? null;

  const renderedMessages = useMemo(
    () =>
      messages.map((message) => (
        <ChatMessage
          key={message.id}
          content={message.content}
          role={message.role}
          timestamp={message.timestamp}
        />
      )),
    [messages]
  );

  const appendAssistantMessage = useCallback((content: string) => {
    setMessages((current) => {
      const next = [
        ...current,
        {
          id: crypto.randomUUID(),
          content,
          role: "assistant" as const,
          timestamp: new Date()
        }
      ];
      if (next.length <= MAX_MESSAGES) return next;
      return next.slice(next.length - MAX_MESSAGES);
    });
  }, []);

  const appendEvent = useCallback((event: AgentEvent) => {
    if (event.event_type === "socket.pong") return;
    setEvents((current) => {
      const next = [...current, event];
      if (next.length <= MAX_EVENTS) return next;
      return next.slice(next.length - MAX_EVENTS);
    });
  }, []);

  const refreshConversations = useCallback(
    async (targetThreadId?: string | null) => {
      if (!userId) return;
      const response = await fetchConversations(userId, 20);
      const mapped: ConversationPreview[] = (response.items ?? []).map((item) => ({
        id: item.id,
        title: item.title,
        updated_at: item.updated_at
      }));
      setConversations(mapped);
      if (!activeConversationId && mapped[0]) {
        setActiveConversationId(mapped[0].id);
      }
      if (targetThreadId) {
        setActiveConversationId(targetThreadId);
      }
    },
    [activeConversationId, userId]
  );

  const refreshConnectionStatus = useCallback(async () => {
    if (!userId) return;
    try {
      const response = await fetchConnectionStatus(userId);
      setConnectionStatus(response);
    } catch {
      setConnectionStatus(null);
    }
  }, [userId]);

  const refreshKeys = useCallback(async () => {
    if (!userId) return [];
    try {
      const response = await listUserKeys(userId);
      setApiKeys(response.items ?? []);
      return response.items ?? [];
    } catch {
      setApiKeys([]);
      return [];
    }
  }, [userId]);

  const openConversation = useCallback(
    async (threadId: string) => {
      if (!userId) return;
      setActiveConversationId(threadId);
      try {
        const response = await fetchConversationMessages(userId, threadId, 120);
        setMessages(toConsoleMessages(response.items ?? []));
      } catch (error) {
        appendAssistantMessage(`대화 로딩 실패: ${(error as Error).message}`);
      }
    },
    [appendAssistantMessage, userId]
  );

  useEffect(() => {
    const currentSession = session;
    if (!currentSession?.userId) return;
    const sessionUserId = currentSession.userId;
    const sessionEmail = currentSession.email;
    const sessionFullName = currentSession.fullName;
    const sessionAvatarUrl = currentSession.avatarUrl;
    let cancelled = false;

    async function initialize() {
      setLoadingWorkspace(true);
      setWorkspaceError(null);
      try {
        await bootstrapUser({
          userId: sessionUserId,
          email: sessionEmail,
          fullName: sessionFullName,
          avatarUrl: sessionAvatarUrl
        });

        const [settingsResponse, keysResponse, convResponse] = await Promise.all([
          fetchWorkspaceSettings(sessionUserId),
          listUserKeys(sessionUserId),
          fetchConversations(sessionUserId, 20)
        ]);
        if (cancelled) return;

        const nextSettings = settingsResponse.settings ?? DEFAULT_SETTINGS;
        setSettings(nextSettings);
        setApiKeys(keysResponse.items ?? []);

        const mappedConversations: ConversationPreview[] = (convResponse.items ?? []).map((item) => ({
          id: item.id,
          title: item.title,
          updated_at: item.updated_at
        }));
        setConversations(mappedConversations);

        if (mappedConversations[0]) {
          setActiveConversationId(mappedConversations[0].id);
          const messagesResponse = await fetchConversationMessages(
            sessionUserId,
            mappedConversations[0].id,
            120
          );
          if (!cancelled) {
            setMessages(toConsoleMessages(messagesResponse.items ?? []));
          }
        } else {
          setMessages([]);
        }

        if ((keysResponse.items ?? []).length === 0 && !nextSettings.dev_mode) {
          setForceKeySetup(true);
          setSettingsOpen(true);
        } else {
          setForceKeySetup(false);
        }
      } catch (error) {
        if (!cancelled) {
          setWorkspaceError((error as Error).message);
        }
      } finally {
        if (!cancelled) {
          setLoadingWorkspace(false);
        }
      }
    }

    void initialize();
    void refreshConnectionStatus();

    return () => {
      cancelled = true;
    };
  }, [refreshConnectionStatus, session]);

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
        // ignore heartbeat payload
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

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");

    function applyTheme() {
      const requested = settings.theme;
      const resolved = requested === "system" ? (media.matches ? "dark" : "light") : requested;
      document.documentElement.classList.toggle("dark", resolved === "dark");
      localStorage.setItem("agentgcs_theme", requested);
    }

    applyTheme();
    media.addEventListener("change", applyTheme);
    return () => media.removeEventListener("change", applyTheme);
  }, [settings.theme]);

  const maybeRequireApproval = useCallback(
    async (mode: AutonomyMode): Promise<boolean> => {
      if (mode === "cautious" && settings.approval_policy.cautious_requires_approval) {
        return window.confirm("신중함 모드 실행 전 사용자 승인이 필요합니다. 계속할까요?");
      }
      if (mode === "balanced" && settings.approval_policy.balanced_requires_approval) {
        return window.confirm("균형형 모드 실행 전 사용자 승인이 필요합니다. 계속할까요?");
      }
      if (mode === "creative" && settings.approval_policy.creative_requires_approval) {
        return window.confirm("창의적 모드 실행 전 사용자 승인이 필요합니다. 계속할까요?");
      }
      if (
        mode === "autonomous" &&
        settings.approval_policy.autonomous_needs_first_warning &&
        !settings.approval_policy.autonomous_warning_accepted
      ) {
        const approved = window.confirm(
          "완전자율 모드는 최초 1회 경고 후 자동 진행됩니다. 승인하면 이후 동일 세션에서 추가 승인 없이 진행합니다. 계속할까요?"
        );
        if (approved) {
          const next: UserSettings = {
            ...settings,
            approval_policy: {
              ...settings.approval_policy,
              autonomous_warning_accepted: true
            }
          };
          setSettings(next);
          if (userId) {
            try {
              await saveWorkspaceSettings(userId, next);
            } catch {
              // ignore temporary persistence failure
            }
          }
        }
        return approved;
      }
      return true;
    },
    [settings, userId]
  );

  const handleChatSend = useCallback(
    async (text: string, mode: AutonomyMode) => {
      if (!userId || !settings) return;
      const approved = await maybeRequireApproval(mode);
      if (!approved) return;

      setActiveView("multi_agent");
      setChatLoading(true);
      setMessages((current) => {
        const next = [
          ...current,
          {
            id: crypto.randomUUID(),
            content: text,
            role: "user" as const,
            timestamp: new Date()
          }
        ];
        if (next.length <= MAX_MESSAGES) return next;
        return next.slice(next.length - MAX_MESSAGES);
      });

      try {
        const response = await agentChat({
          userId,
          message: text,
          threadId: activeConversationId,
          title: text.slice(0, 30),
          mode,
          personaStats: activePersona?.stats,
          useMock: settings.dev_mode || !connectionStatus?.claude.reachable
        });

        setMessages((current) => {
          const next = [
            ...current,
            {
              id: response.assistant_message.id ?? crypto.randomUUID(),
              content: response.reply,
              role: "assistant" as const,
              timestamp: response.assistant_message.created_at
                ? new Date(response.assistant_message.created_at)
                : new Date()
            }
          ];
          if (next.length <= MAX_MESSAGES) return next;
          return next.slice(next.length - MAX_MESSAGES);
        });

        setActiveConversationId(response.thread_id);
        await refreshConversations(response.thread_id);
      } catch (error) {
        appendAssistantMessage(`요청 실패: ${(error as Error).message}`);
      } finally {
        setChatLoading(false);
      }
    },
    [
      activeConversationId,
      activePersona?.stats,
      appendAssistantMessage,
      connectionStatus?.claude.reachable,
      maybeRequireApproval,
      refreshConversations,
      settings,
      userId
    ]
  );

  const handleQuickOptionSelect = useCallback(
    (text: string) => {
      void handleChatSend(text, "balanced");
    },
    [handleChatSend]
  );

  const handleChatInputSend = useCallback(
    (text: string, mode: AutonomyMode) => {
      void handleChatSend(text, mode);
    },
    [handleChatSend]
  );

  const handleSaveSettings = useCallback(async (nextSettings: UserSettings) => {
    if (!userId) return;
    const normalized: UserSettings = {
      ...nextSettings,
      claude_base_url: nextSettings.claude_base_url?.trim() || null,
      preferred_model: nextSettings.preferred_model?.trim() || null,
      default_notify_email: nextSettings.default_notify_email?.trim() || null
    };
    const response = await saveWorkspaceSettings(userId, normalized);
    setSettings(response.settings);
  }, [userId]);

  const handleSaveApiKey = useCallback(async (keyName: string, plaintextKey: string) => {
    if (!userId) return;
    await storeUserKey(userId, { key_name: keyName, plaintext_key: plaintextKey });
    const keys = await refreshKeys();
    setForceKeySetup(keys.length === 0 && !settings.dev_mode);
  }, [refreshKeys, settings.dev_mode, userId]);

  async function handleStartDeepTask() {
    if (!userId) return;
    const trimmedTask = task.trim();
    if (trimmedTask.length < 3) {
      appendAssistantMessage("과제 입력은 최소 3자 이상이어야 합니다.");
      appendEvent({
        event_type: "toast.notification",
        timestamp: new Date().toISOString(),
        payload: {
          title: "입력 검증",
          description: "과제는 최소 3자 이상 입력해주세요."
        }
      });
      return;
    }

    const approved = await maybeRequireApproval(taskMode);
    if (!approved) return;

    setRunning(true);
    setActiveView("task_automation");

    try {
      const notifyCandidate = settings.default_notify_email ?? userEmail ?? undefined;
      const notifyEmail = isValidEmail(notifyCandidate) ? notifyCandidate : undefined;
      const response = await startDeepTask({
        userId,
        task: trimmedTask,
        personaStats: activePersona?.stats ?? DEFAULT_STATS,
        notifyEmail,
        useMock: settings.dev_mode || !connectionStatus?.claude.reachable
      });
      setActiveRunId(response.run_id);
    } catch (error) {
      setRunning(false);
      appendAssistantMessage(`과제 자동화 요청 실패: ${(error as Error).message}`);
      appendEvent({
        event_type: "deep_task.failed",
        timestamp: new Date().toISOString(),
        payload: { message: (error as Error).message }
      });
    }
  }

  async function handlePersonalTrigger() {
    if (!userId) return;
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
      appendAssistantMessage(`개인 업무 에이전트 오류: ${(error as Error).message}`);
    }
  }

  const sidebarItems: SidebarItem[] = [
    { id: "multi_agent", title: "멀티 에이전트 콘솔", subtitle: "AI 대화 및 상태 스트림" },
    { id: "kanban", title: "칸반 워크스페이스", subtitle: "마일스톤/Task 관리" },
    { id: "automation", title: "업무 자동화 센터", subtitle: "개인 업무 에이전트 도구" },
    { id: "task_automation", title: "과제 자동화 센터", subtitle: "심층 과제 실행/토론" }
  ];

  const handleCloseSettings = useCallback(() => {
    if (forceKeySetup && !settings.dev_mode && apiKeys.length === 0) return;
    setSettingsOpen(false);
  }, [apiKeys.length, forceKeySetup, settings.dev_mode]);

  if (!session) {
    return <LoginGate onAuthenticated={(next) => setSession(next)} />;
  }

  return (
    <main className="relative flex h-screen w-full overflow-hidden">
      <ToastStack events={events} />

      <SettingsModal
        open={settingsOpen}
        userId={session.userId}
        userEmail={session.email}
        settings={settings}
        apiKeys={apiKeys}
        connectionStatus={connectionStatus}
        onClose={handleCloseSettings}
        onSaveSettings={handleSaveSettings}
        onSaveApiKey={handleSaveApiKey}
        onRefreshConnectionStatus={refreshConnectionStatus}
      />

      {!settingsOpen && (
        <>
          <div className="pointer-events-none absolute left-[-10%] top-[-10%] h-[40%] w-[40%] rounded-full bg-orange-200/25 blur-[60px]" />
          <div className="pointer-events-none absolute bottom-[-10%] right-[-5%] h-[50%] w-[50%] rounded-full bg-amber-100/35 blur-[70px]" />
          <div className="pointer-events-none absolute right-[10%] top-[20%] h-[30%] w-[30%] rounded-full bg-white/50 blur-[50px]" />

          <ChatSidebar
            items={sidebarItems}
            activeItem={activeView}
            onSelectItem={(id) => setActiveView(id as WorkspaceView)}
            onReset={() => {
              setMessages([]);
              setEvents([]);
              setActiveRunId(null);
              setRunning(false);
              setActiveConversationId(null);
              setTask(DEFAULT_TASK);
            }}
            conversations={conversations}
            activeConversationId={activeConversationId}
            onSelectConversation={(threadId) => {
              void openConversation(threadId);
              setActiveView("multi_agent");
            }}
            onOpenSettings={() => setSettingsOpen(true)}
          />

          <section className="relative z-10 flex min-h-0 flex-1 flex-col">
        <header className="glass-panel m-4 mb-3 rounded-3xl p-4 md:p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="text-2xl font-bold text-gray-800 dark:text-slate-100 md:text-3xl">
                AgentGCS 통합 워크스페이스
              </h1>
              <p className="mt-1 text-sm text-orange-900/65 dark:text-slate-300/80">
                작은 기록이 모여 특별한 성장을 만듭니다.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Badge>{streamBadge}</Badge>
              <Badge>{settings.dev_mode ? "Dev 모드" : "운영 모드"}</Badge>
            </div>
          </div>
          {workspaceError && (
            <p className="mt-3 rounded-xl border border-red-200 bg-red-50/80 px-3 py-2 text-xs text-red-700">
              {workspaceError}
            </p>
          )}
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
            <KanbanBoard userId={session.userId} />
          </div>
        ) : (
          <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 px-4 pb-4 xl:grid-cols-[1fr_410px]">
            <div className="glass-panel flex min-h-0 flex-col rounded-3xl">
              {messages.length > 0 && (
                <PerfTrace id="chat-message-list" thresholdMs={8}>
                  <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-6 py-6 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-orange-300/35 [&::-webkit-scrollbar-track]:bg-transparent">
                    {renderedMessages}
                  </div>
                </PerfTrace>
              )}

              <div
                className="flex w-full flex-col items-center justify-center pb-6 pt-2"
                style={{ flex: messages.length > 0 ? "0 0 auto" : "1 1 auto" }}
              >
                <div className={messages.length === 0 ? "mb-8" : "mb-4"}>
                  <PerfTrace id="ai-indicator" thresholdMs={6}>
                    <AIIndicator
                      isActive={chatLoading || running}
                      isChatStarted={messages.length > 0}
                      onOptionSelect={handleQuickOptionSelect}
                    />
                  </PerfTrace>
                </div>

                <ChatInput
                  onSend={handleChatInputSend}
                  isCenter={messages.length === 0}
                  disabled={chatLoading || loadingWorkspace}
                />
              </div>
            </div>

            <div className="min-h-0 space-y-4 overflow-y-auto pb-2 pr-1">
              <Card className="space-y-3">
                <CardTitle>사용자 인증 / API 연결 현황</CardTitle>
                <CardDescription>
                  로그인: {session.provider === "google" ? "Google OAuth" : "Dev 모드"}
                  <br />
                  user_id: <span className="font-mono text-xs">{session.userId}</span>
                </CardDescription>
                <div className="flex flex-wrap gap-2 text-xs">
                  <Badge>{connectionStatus?.claude.status ?? "Claude 상태 확인 전"}</Badge>
                  <Badge>
                    {connectionStatus?.school_api.token_saved
                      ? "School 토큰 저장됨"
                      : "School 토큰 없음"}
                  </Badge>
                  <Badge>{settings.dev_mode ? "DB 우회 테스트 가능" : "DB 연결 사용"}</Badge>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setSettingsOpen(true);
                    }}
                    className="gap-1.5"
                  >
                    <Wrench className="h-4 w-4" />
                    설정 열기
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={async () => {
                      const next = { ...settings, dev_mode: !settings.dev_mode };
                      setSettings(next);
                      if (userId) {
                        try {
                          await saveWorkspaceSettings(userId, next);
                          await refreshConnectionStatus();
                        } catch {
                          // ignore
                        }
                      }
                    }}
                  >
                    {settings.dev_mode ? "Dev 모드 해제" : "Dev 모드 활성화"}
                  </Button>
                  <Button
                    variant="ghost"
                    className="gap-1.5"
                    onClick={() => {
                      clearAuthSession();
                      window.location.reload();
                    }}
                  >
                    <LogOut className="h-4 w-4" />
                    로그아웃
                  </Button>
                </div>
                {forceKeySetup && !settings.dev_mode && (
                  <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900/80">
                    `api.1000.school` 토큰을 먼저 저장해주세요.
                  </p>
                )}
              </Card>

              <Card className="space-y-3">
                <CardTitle>과제 자동화 센터</CardTitle>
                <CardDescription>
                  시나리오 빠른 실행이 통합된 패널입니다. 선택한 페르소나로 심층 토론 파이프라인을 실행합니다.
                </CardDescription>
                <Textarea value={task} onChange={(event) => setTask(event.target.value)} />
                <div className="flex flex-wrap gap-2">
                  {(["cautious", "balanced", "creative", "autonomous"] as AutonomyMode[]).map(
                    (mode) => (
                      <Button
                        key={mode}
                        size="sm"
                        variant={taskMode === mode ? "accent" : "secondary"}
                        onClick={() => setTaskMode(mode)}
                      >
                        {modeLabel(mode)}
                      </Button>
                    )
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button onClick={() => void handleStartDeepTask()} disabled={running}>
                    {running ? "실행 중..." : "과제 토론 시작"}
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setTask(DEFAULT_TASK);
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

              <Card className="space-y-3">
                <CardTitle>업무 자동화 센터</CardTitle>
                <CardDescription>교내 API/Gmail/Calendar 실행 계획을 수동 트리거합니다.</CardDescription>
                <Textarea
                  value={personalInstruction}
                  onChange={(event) => setPersonalInstruction(event.target.value)}
                />
                <Button onClick={() => void handlePersonalTrigger()} className="gap-2">
                  <Bolt className="h-4 w-4" />
                  개인 업무 에이전트 실행
                </Button>
              </Card>

              <PerfTrace id="generative-feed" thresholdMs={8}>
                <GenerativeFeed events={events} running={running} />
              </PerfTrace>

              <Card className="space-y-3">
                <CardTitle>워크플로우 상태</CardTitle>
                <CardDescription>
                  정보 탐색 → 5인 토론 → 결론 → NotebookLM 후처리 → Drive/Gmail 통보
                </CardDescription>
                <div className="grid grid-cols-3 gap-2 text-xs text-orange-900/80">
                  <div className="rounded-xl border border-white/80 bg-white/55 p-3 dark:bg-slate-800/70">
                    <RadioTower className="mb-2 h-4 w-4" />
                    실시간 WebSocket
                  </div>
                  <div className="rounded-xl border border-white/80 bg-white/55 p-3 dark:bg-slate-800/70">
                    <Layers3 className="mb-2 h-4 w-4" />
                    Persona 동적 주입
                  </div>
                  <div className="rounded-xl border border-white/80 bg-white/55 p-3 dark:bg-slate-800/70">
                    <GalleryVerticalEnd className="mb-2 h-4 w-4" />
                    UI Action Stream
                  </div>
                </div>
              </Card>

              {connectionStatus?.claude.attempts && connectionStatus.claude.attempts.length > 0 && (
                <Card className="space-y-2">
                  <CardTitle>Claude 연결 진단</CardTitle>
                  <CardDescription>
                    `claude.1000.school` 우회 연결 결과입니다. 502가 반복되면 게이트웨이 측 점검이 필요합니다.
                  </CardDescription>
                  <div className="max-h-40 space-y-1 overflow-y-auto rounded-xl bg-white/60 p-2 text-xs text-orange-900/85 dark:bg-slate-900/60">
                    {connectionStatus.claude.attempts.map((attempt, index) => (
                      <pre key={index} className="whitespace-pre-wrap break-all">
                        {JSON.stringify(attempt, null, 2)}
                      </pre>
                    ))}
                  </div>
                </Card>
              )}
            </div>
          </div>
        )}

        {loadingWorkspace && (
          <div className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center bg-white/25 backdrop-blur-[1px] dark:bg-black/30">
            <p className="rounded-2xl bg-white/80 px-4 py-2 text-sm text-orange-900/80 shadow dark:bg-slate-900/80 dark:text-slate-100">
              워크스페이스 초기화 중...
            </p>
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
                <div className="rounded-xl border border-white/80 bg-white/50 p-3 text-sm text-orange-900/80 dark:bg-slate-800/70">
                  <Sparkles className="mb-1 h-4 w-4" />
                  마감 임박 토스트 규칙 점검
                </div>
                <div className="rounded-xl border border-white/80 bg-white/50 p-3 text-sm text-orange-900/80 dark:bg-slate-800/70">
                  <Bolt className="mb-1 h-4 w-4" />
                  개인 업무 프롬프트 템플릿 관리
                </div>
                <div className="rounded-xl border border-white/80 bg-white/50 p-3 text-sm text-orange-900/80 dark:bg-slate-800/70">
                  <RadioTower className="mb-1 h-4 w-4" />
                  실시간 알림 채널 테스트
                </div>
              </div>
            </Card>
          </div>
        )}
          </section>
        </>
      )}
    </main>
  );
}
