"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bolt,
  GalleryVerticalEnd,
  Layers3,
  LogOut,
  RadioTower,
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
  ApiKeyMeta,
  AutonomyMode,
  ConversationMessage,
  PersonaStats,
  WorkspaceConnectionStatus,
  UserSettings
} from "@/lib/types";

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
  preferred_model: "gpt-5.2",
  knowledge_base_prompt: null,
  chat_mode_personas: {
    cautious: {
      creativity: 42,
      logic: 92,
      critical_thinking: 95,
      data_dependency: 88,
      empathy: 52,
      drive: 58
    },
    balanced: {
      creativity: 74,
      logic: 78,
      critical_thinking: 79,
      data_dependency: 72,
      empathy: 64,
      drive: 72
    },
    creative: {
      creativity: 96,
      logic: 62,
      critical_thinking: 58,
      data_dependency: 46,
      empathy: 68,
      drive: 86
    },
    autonomous: {
      creativity: 72,
      logic: 82,
      critical_thinking: 78,
      data_dependency: 66,
      empathy: 54,
      drive: 93
    }
  },
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

type WorkspaceView = "multi_agent" | "kanban" | "automation";

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
  if (event.event_type === "chat.processing") {
    const stage = String(payload.stage ?? "processing");
    if (stage === "received") return "요청을 접수했습니다. 응답을 준비 중입니다.";
    if (stage === "tool_context_ready") {
      const tools = Array.isArray(payload.tools) ? payload.tools.join(", ") : "none";
      return `도구 컨텍스트 준비 완료 (${tools})`;
    }
    if (stage === "api_actions_done") {
      const actions = Array.isArray(payload.actions) ? payload.actions.join(", ") : "none";
      return `API 액션 실행 완료 (${actions})`;
    }
    if (stage === "multi_agent_reasoning") return "멀티 에이전트 토론을 진행하고 있습니다.";
    return `처리 단계: ${stage}`;
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

function mergeApiKeys(current: ApiKeyMeta[], incoming: ApiKeyMeta[]): ApiKeyMeta[] {
  const merged = new Map<string, ApiKeyMeta>();
  for (const item of current) {
    if (!item.key_name) continue;
    merged.set(item.key_name, item);
  }
  for (const item of incoming) {
    if (!item.key_name) continue;
    merged.set(item.key_name, item);
  }
  return Array.from(merged.values()).sort((a, b) => a.key_name.localeCompare(b.key_name));
}

export default function HomePage() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [settings, setSettings] = useState<UserSettings>(DEFAULT_SETTINGS);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [forceKeySetup, setForceKeySetup] = useState(false);
  const [apiKeys, setApiKeys] = useState<ApiKeyMeta[]>([]);
  const [connectionStatus, setConnectionStatus] = useState<WorkspaceConnectionStatus | null>(
    null
  );

  const [messages, setMessages] = useState<ConsoleMessage[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [chatLoading, setChatLoading] = useState(false);
  const [socketConnected, setSocketConnected] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<WorkspaceView>("multi_agent");

  const [conversations, setConversations] = useState<ConversationPreview[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);

  const [task, setTask] = useState("");
  const [personalInstruction, setPersonalInstruction] = useState("");
  const [loadingWorkspace, setLoadingWorkspace] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const apiKeysRef = useRef<ApiKeyMeta[]>([]);
  const devModeRef = useRef<boolean>(settings.dev_mode);

  const activeTaskPersona = useMemo(() => {
    const target = settings.personas.find((persona) => persona.id === settings.active_persona_id);
    return target ?? settings.personas[0] ?? null;
  }, [settings.active_persona_id, settings.personas]);

  const previousConversations = useMemo(
    () => conversations.filter((conversation) => conversation.id !== activeConversationId),
    [activeConversationId, conversations]
  );

  const streamBadge = useMemo(
    () => (socketConnected ? "실시간 연결됨" : "실시간 연결 끊김"),
    [socketConnected]
  );
  const hasSchoolApiToken = useMemo(
    () =>
      settings.dev_mode ||
      Boolean(connectionStatus?.school_api.token_saved) ||
      apiKeys.some((item) => item.key_name === "school_api_token"),
    [apiKeys, connectionStatus?.school_api.token_saved, settings.dev_mode]
  );

  const userId = session?.userId ?? "";
  const userEmail = session?.email ?? null;

  useEffect(() => {
    apiKeysRef.current = apiKeys;
  }, [apiKeys]);

  useEffect(() => {
    devModeRef.current = settings.dev_mode;
  }, [settings.dev_mode]);

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

  useEffect(() => {
    const container = messageListRef.current;
    if (!container) return;
    const rafId = window.requestAnimationFrame(() => {
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    });
    return () => window.cancelAnimationFrame(rafId);
  }, [messages]);

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
    const localHasSchoolToken = apiKeysRef.current.some(
      (item) => item.key_name === "school_api_token"
    );
    const devModeEnabled = devModeRef.current;
    try {
      const response = await fetchConnectionStatus(userId);
      setConnectionStatus(response);
      setForceKeySetup(
        !response.school_api.token_saved && !localHasSchoolToken && !devModeEnabled
      );
    } catch {
      setConnectionStatus(null);
      setForceKeySetup(!localHasSchoolToken && !devModeEnabled);
    }
  }, [userId]);

  const refreshKeys = useCallback(async () => {
    if (!userId) return [];
    try {
      const response = await listUserKeys(userId);
      const incoming = response.items ?? [];
      let merged: ApiKeyMeta[] = [];
      setApiKeys((current) => {
        merged = mergeApiKeys(current, incoming);
        return merged;
      });
      return merged;
    } catch {
      return apiKeysRef.current;
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
    let watchdog: number | null = null;

    async function initialize() {
      setLoadingWorkspace(true);
      setWorkspaceError(null);
      watchdog = window.setTimeout(() => {
        if (cancelled) return;
        setLoadingWorkspace(false);
        setWorkspaceError((current) =>
          current ?? "초기화가 지연되어 일부 기능만 먼저 표시합니다."
        );
      }, 20000);
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

        const nextSettings: UserSettings = {
          ...DEFAULT_SETTINGS,
          ...(settingsResponse.settings ?? DEFAULT_SETTINGS),
          chat_mode_personas: {
            ...DEFAULT_SETTINGS.chat_mode_personas,
            ...(settingsResponse.settings?.chat_mode_personas ?? {})
          }
        };
        if (
          !nextSettings.preferred_model ||
          nextSettings.preferred_model === "claude-3-5-sonnet-20241022"
        ) {
          nextSettings.preferred_model = "gpt-5.2";
          try {
            await saveWorkspaceSettings(sessionUserId, nextSettings);
          } catch {
            // ignore model migration errors
          }
        }
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

        const hasSchoolKey = (keysResponse.items ?? []).some(
          (item) => item.key_name === "school_api_token"
        );
        setForceKeySetup(!hasSchoolKey && !nextSettings.dev_mode);
      } catch (error) {
        if (!cancelled) {
          setWorkspaceError((error as Error).message);
        }
      } finally {
        if (watchdog !== null) {
          window.clearTimeout(watchdog);
        }
        if (!cancelled) {
          setLoadingWorkspace(false);
        }
      }
    }

    void initialize();
    void refreshConnectionStatus();

    return () => {
      cancelled = true;
      if (watchdog !== null) {
        window.clearTimeout(watchdog);
      }
    };
  }, [session, refreshConnectionStatus]);

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

  useEffect(() => {
    if (settings.dev_mode) {
      setForceKeySetup(false);
      return;
    }
    if (apiKeys.some((item) => item.key_name === "school_api_token")) {
      setForceKeySetup(false);
    }
  }, [apiKeys, settings.dev_mode]);

  const maybeRequireApproval = useCallback(
    async (mode: AutonomyMode, riskyAction: boolean): Promise<boolean> => {
      if (
        mode === "autonomous" &&
        settings.approval_policy.autonomous_needs_first_warning &&
        !settings.approval_policy.autonomous_warning_accepted
      ) {
        const approved = window.confirm(
          "완전자율 모드는 최초 1회 승인 이후 모든 작업을 자동으로 진행합니다. 계속할까요?"
        );
        if (!approved) return false;

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
        return true;
      }

      if (!riskyAction) return true;
      if (mode === "autonomous") return true;

      if (mode === "cautious" && settings.approval_policy.cautious_requires_approval) {
        return window.confirm("신중함 모드 위험 작업 진행시 의무 승인 요청: 계속할까요?");
      }
      if (mode === "balanced" && settings.approval_policy.balanced_requires_approval) {
        return window.confirm("균형형 모드 위험 작업 진행시 의무 승인 요청: 계속할까요?");
      }
      if (mode === "creative" && settings.approval_policy.creative_requires_approval) {
        return window.confirm("창의적 모드 위험 작업 진행시 의무 승인 요청: 계속할까요?");
      }
      return true;
    },
    [settings, userId]
  );

  const handleChatSend = useCallback(
    async (text: string, mode: AutonomyMode) => {
      if (!userId || !settings) return;
      if (!hasSchoolApiToken) {
        appendAssistantMessage("API 키를 입력하세요. `api.1000.school` 토큰 저장 후 대화를 시작할 수 있습니다.");
        setSettingsOpen(true);
        return;
      }
      const approved = await maybeRequireApproval(mode, false);
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
          personaStats: settings.chat_mode_personas[mode] ?? DEFAULT_STATS,
          knowledgePrompt: settings.knowledge_base_prompt ?? null,
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
      appendAssistantMessage,
      connectionStatus?.claude.reachable,
      hasSchoolApiToken,
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
      knowledge_base_prompt: nextSettings.knowledge_base_prompt?.trim() || null,
      chat_mode_personas: {
        ...DEFAULT_SETTINGS.chat_mode_personas,
        ...nextSettings.chat_mode_personas
      }
    };
    const response = await saveWorkspaceSettings(userId, normalized);
    setSettings(response.settings);
  }, [userId]);

  const handleSaveApiKey = useCallback(async (keyName: string, plaintextKey: string) => {
    if (!userId) return;
    const response = await storeUserKey(userId, { key_name: keyName, plaintext_key: plaintextKey });
    setApiKeys((current) =>
      mergeApiKeys(current, [
        {
          id: `local-${response.key_name}`,
          key_name: response.key_name,
          key_version: 1,
          updated_at: new Date().toISOString()
        }
      ])
    );
    await refreshKeys();
    await refreshConnectionStatus();
  }, [refreshConnectionStatus, refreshKeys, userId]);

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

    if (!hasSchoolApiToken) {
      appendAssistantMessage("API 키를 입력하세요. `api.1000.school` 토큰이 없으면 과제 자동화를 시작할 수 없습니다.");
      setSettingsOpen(true);
      return;
    }
    if (settings.personas.length < 3) {
      appendAssistantMessage("과제 자동화를 시작하려면 멀티 에이전트 페르소나를 최소 3개 이상 설정해야 합니다.");
      setSettingsOpen(true);
      return;
    }

    const approved = await maybeRequireApproval("balanced", true);
    if (!approved) return;

    setRunning(true);
    setActiveView("multi_agent");

    try {
      const notifyCandidate = userEmail ?? undefined;
      const notifyEmail = isValidEmail(notifyCandidate) ? notifyCandidate : undefined;
      const response = await startDeepTask({
        userId,
        task: trimmedTask,
        personaStats: activeTaskPersona?.stats ?? DEFAULT_STATS,
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
    if (!personalInstruction.trim()) {
      appendAssistantMessage("업무 자동화 요청을 먼저 입력해주세요.");
      return;
    }
    if (!hasSchoolApiToken && !settings.dev_mode) {
      appendAssistantMessage("API 키를 입력하세요. `api.1000.school` 토큰 저장 후 업무 자동화를 실행할 수 있습니다.");
      setSettingsOpen(true);
      return;
    }
    try {
      const response = await triggerPersonalAgent({
        userId,
        instruction: personalInstruction.trim()
      });
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
    { id: "automation", title: "업무 자동화 센터", subtitle: "개인 업무 에이전트 도구" }
  ];

  const handleCloseSettings = useCallback(() => {
    setSettingsOpen(false);
  }, []);

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
          <div className="pointer-events-none absolute left-[-10%] top-[-10%] h-[40%] w-[40%] rounded-full bg-orange-200/25 blur-[30px]" />
          <div className="pointer-events-none absolute bottom-[-10%] right-[-5%] h-[50%] w-[50%] rounded-full bg-amber-100/35 blur-[35px]" />
          <div className="pointer-events-none absolute right-[10%] top-[20%] h-[30%] w-[30%] rounded-full bg-white/50 blur-[25px]" />

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
              setTask("");
            }}
            conversations={previousConversations}
            activeConversationId={activeConversationId}
            onSelectConversation={(threadId) => {
              void openConversation(threadId);
              setActiveView("multi_agent");
            }}
            onOpenSettings={() => setSettingsOpen(true)}
            userName={session.fullName}
            userEmail={session.email}
            userAvatarUrl={session.avatarUrl}
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
              <Button
                variant="ghost"
                size="sm"
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
                  <div
                    ref={messageListRef}
                    className="min-h-0 flex-1 space-y-6 overflow-y-auto px-6 py-6 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-orange-300/35 [&::-webkit-scrollbar-track]:bg-transparent"
                  >
                    {renderedMessages}
                  </div>
                </PerfTrace>
              )}

              <div
                className="flex w-full flex-col items-center justify-center pb-6 pt-2"
                style={{ flex: messages.length > 0 ? "0 0 auto" : "1 1 auto" }}
              >
                {loadingWorkspace ? (
                  <div className={messages.length === 0 ? "mb-8" : "mb-4"}>
                    <p className="rounded-2xl border border-white/70 bg-white/70 px-4 py-2 text-sm text-orange-900/75 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200">
                      워크스페이스 동기화 중...
                    </p>
                  </div>
                ) : (
                  <>
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
                      disabled={chatLoading || loadingWorkspace || !hasSchoolApiToken}
                    />
                  </>
                )}
                {!hasSchoolApiToken && (
                  <p className="mt-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900/85">
                    API 키를 입력하세요. 설정에서 `api.1000.school` 토큰을 저장하면 AI 대화 입력창이 활성화됩니다.
                  </p>
                )}
              </div>
            </div>

            <div className="min-h-0 space-y-4 overflow-y-auto pb-2 pr-1">
              <Card className="space-y-3">
                <CardTitle>사용자 인증 / 연결 현황</CardTitle>
                <CardDescription>
                  로그인: {session.provider === "google" ? "Google OAuth" : "Dev 모드"}
                  <br />
                  user_id: <span className="font-mono text-xs">{session.userId}</span>
                </CardDescription>
                <div className="grid grid-cols-1 gap-2 text-xs md:grid-cols-2">
                  <p className="rounded-xl border border-white/70 bg-white/55 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/70">
                    Claude:{" "}
                    <span className="font-semibold">
                      {connectionStatus?.claude.reachable ? "정상" : `오류 (${connectionStatus?.claude.status ?? "unknown"})`}
                    </span>
                  </p>
                  <p className="rounded-xl border border-white/70 bg-white/55 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/70">
                    GCS Pulse(api.1000.school):{" "}
                    <span className="font-semibold">
                      {connectionStatus?.school_api.reachable
                        ? "정상"
                        : connectionStatus?.school_api.token_saved
                          ? `오류 (${connectionStatus?.school_api.status ?? "unknown"})`
                          : "미연결"}
                    </span>
                    {connectionStatus?.school_api.reason ? (
                      <span className="mt-1 block text-[10px] text-red-700/90 dark:text-red-300/90">
                        {connectionStatus.school_api.reason}
                      </span>
                    ) : null}
                    {connectionStatus?.school_api.source ? (
                      <span className="mt-1 block text-[10px] text-orange-900/70 dark:text-slate-300/80">
                        source: {connectionStatus.school_api.source}
                      </span>
                    ) : null}
                  </p>
                  <p className="rounded-xl border border-white/70 bg-white/55 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/70">
                    Google Workspace:{" "}
                    <span className="font-semibold">
                      {connectionStatus?.google_workspace.token_saved ? "연결됨" : "미연결"}
                    </span>
                  </p>
                  <p className="rounded-xl border border-white/70 bg-white/55 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/70">
                    DB:{" "}
                    <span className="font-semibold">
                      {connectionStatus?.database?.connected ? "연결됨" : "미연결"} (
                      {connectionStatus?.database?.source ?? "unknown"})
                    </span>
                  </p>
                </div>
                {forceKeySetup && !settings.dev_mode && (
                  <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900/80">
                    `api.1000.school` 토큰을 먼저 저장해주세요.
                  </p>
                )}
              </Card>

              <Card className="space-y-3">
                <CardTitle>개발 도구</CardTitle>
                <CardDescription>설정/개발 모드 전환 등 개발용 제어 버튼입니다.</CardDescription>
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
                </div>
              </Card>

              <Card className="space-y-3">
                <CardTitle>멀티 에이전트 콘솔 (과제 자동화)</CardTitle>
                <CardDescription>
                  과제 해결 전용 멀티 에이전트 실행 패널입니다. 설정된 과제 페르소나(최소 3개)로 심층 토론을 시작합니다.
                </CardDescription>
                <Textarea
                  value={task}
                  onChange={(event) => setTask(event.target.value)}
                  placeholder="해결할 과제를 입력하세요..."
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    onClick={() => void handleStartDeepTask()}
                    disabled={running || !hasSchoolApiToken}
                  >
                    {running ? "실행 중..." : "과제 토론 시작"}
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setTask("");
                    }}
                  >
                    입력 비우기
                  </Button>
                </div>
                {activeRunId && (
                  <p className="rounded-xl bg-white/70 px-3 py-2 font-mono text-xs text-orange-900/80">
                    run_id: {activeRunId}
                  </p>
                )}
                <p className="rounded-xl border border-white/70 bg-white/45 px-3 py-2 text-xs text-orange-900/80 dark:border-slate-700 dark:bg-slate-800/70 dark:text-slate-300">
                  현재 설정된 과제 에이전트 수: {settings.personas.length}개
                </p>
                {!hasSchoolApiToken && (
                  <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900/85">
                    API 키를 입력하세요. `api.1000.school` 토큰이 없으면 과제 자동화 API가 비활성화됩니다.
                  </p>
                )}
              </Card>

              <Card className="space-y-3">
                <CardTitle>업무 자동화 센터</CardTitle>
                <CardDescription>교내 API/Gmail/Calendar 실행 계획을 수동 트리거합니다.</CardDescription>
                <Textarea
                  value={personalInstruction}
                  onChange={(event) => setPersonalInstruction(event.target.value)}
                  placeholder="자동화할 업무 요청을 입력하세요..."
                />
                <Button
                  onClick={() => void handlePersonalTrigger()}
                  className="gap-2"
                  disabled={!personalInstruction.trim()}
                >
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
              <CardTitle>업무 자동화 안내</CardTitle>
              <CardDescription>
                업무 자동화 센터에서 요청을 입력하면 학교 API 및 Google 연동 작업을 수동 실행합니다.
              </CardDescription>
            </Card>
          </div>
        )}
          </section>
        </>
      )}
    </main>
  );
}
