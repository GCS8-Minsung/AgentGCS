import {
  ApiKeyMeta,
  AutonomyMode,
  ConversationMessage,
  ConversationThread,
  PersonaStats,
  TaskItem,
  TaskStatus,
  WorkspaceConnectionStatus,
  UserSettings
} from "@/lib/types";

const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";
const REQUEST_TIMEOUT_MS = 180000;

async function request<T>(
  path: string,
  userId: string,
  options?: RequestInit,
  timeoutMs?: number
): Promise<T> {
  const controller = new AbortController();
  const effectiveTimeoutMs = timeoutMs ?? REQUEST_TIMEOUT_MS;
  const timeout = window.setTimeout(() => controller.abort(), effectiveTimeoutMs);
  let response: Response;
  try {
    response = await fetch(`${backendUrl}${path}`, {
      ...options,
      signal: options?.signal ?? controller.signal,
      headers: {
        "Content-Type": "application/json",
        "x-user-id": userId,
        ...(options?.headers ?? {})
      }
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") {
      throw new Error(
        `요청 시간 초과 (${Math.floor(effectiveTimeoutMs / 1000)}초): ${backendUrl}${path}. 백엔드 상태를 확인해주세요.`
      );
    }
    throw new Error(
      `백엔드 연결 실패 (${backendUrl}). 백엔드 서버(8000)가 실행 중인지 확인해주세요. ${(error as Error).message}`
    );
  } finally {
    window.clearTimeout(timeout);
  }

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${text}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export async function startDeepTask(params: {
  userId: string;
  task: string;
  personaStats: PersonaStats;
  notifyEmail?: string;
  useMock?: boolean;
}) {
  return request<{
    run_id: string;
    status: string;
    websocket_path: string;
  }>("/api/agents/deep-task/start", params.userId, {
    method: "POST",
    body: JSON.stringify({
      task: params.task,
      persona_stats: params.personaStats,
      notify_email: params.notifyEmail ?? null,
      use_mock: params.useMock ?? false
    })
  });
}

export async function orchestratorRun(params: {
  userId: string;
  task: string;
  persona_count?: number;
  useMock?: boolean;
}) {
  return request<{
    run_id: string;
    summary?: string;
    notebook?: any;
    pptx?: string;
    drive?: any;
  }>(
    "/api/orchestrator/run",
    params.userId,
    {
      method: "POST",
      body: JSON.stringify({
        task: params.task,
        persona_count: params.persona_count ?? 4,
        use_mock: params.useMock ?? false
      })
    },
    420000
  );
}

export async function triggerPersonalAgent(params: {
  userId: string;
  instruction: string;
}) {
  return request<{ status: string; plan: string }>(
    "/api/agents/personal/trigger",
    params.userId,
    {
      method: "POST",
      body: JSON.stringify({
        instruction: params.instruction
      })
    }
  );
}

export async function fetchTasks(userId: string) {
  return request<{ items: TaskItem[] }>("/api/tasks", userId, { method: "GET" });
}

export async function createTask(
  userId: string,
  task: { title: string; due_date?: string | null; status?: TaskStatus }
) {
  return request<{ item: TaskItem }>("/api/tasks", userId, {
    method: "POST",
    body: JSON.stringify({
      title: task.title,
      due_date: task.due_date ?? null,
      status: task.status ?? "todo"
    })
  });
}

export async function updateTask(
  userId: string,
  taskId: string,
  updates: Partial<Pick<TaskItem, "status" | "title" | "description" | "due_date">>
) {
  return request<{ item: TaskItem }>(`/api/tasks/${taskId}`, userId, {
    method: "PATCH",
    body: JSON.stringify(updates)
  });
}

export async function bootstrapUser(params: {
  userId: string;
  email?: string | null;
  fullName?: string | null;
  avatarUrl?: string | null;
}) {
  return request<{ status: string; user_id: string; source: string }>(
    "/api/users/bootstrap",
    params.userId,
    {
      method: "POST",
      body: JSON.stringify({
        user_id: params.userId,
        email: params.email ?? null,
        full_name: params.fullName ?? null,
        avatar_url: params.avatarUrl ?? null
      })
    }
  );
}

export async function fetchWorkspaceSettings(userId: string) {
  return request<{ settings: UserSettings; source: string }>(
    "/api/workspace/settings",
    userId,
    {
      method: "GET"
    }
  );
}

export async function saveWorkspaceSettings(userId: string, settings: UserSettings) {
  return request<{ settings: UserSettings; source: string }>(
    "/api/workspace/settings",
    userId,
    {
      method: "PUT",
      body: JSON.stringify(settings)
    }
  );
}

export async function fetchConversations(userId: string, limit = 20) {
  return request<{ items: ConversationThread[]; source: string }>(
    `/api/workspace/conversations?limit=${limit}`,
    userId,
    { method: "GET" }
  );
}

export async function createConversation(
  userId: string,
  payload: { title?: string | null; thread_id?: string | null }
) {
  return request<{ item: ConversationThread; source: string }>(
    "/api/workspace/conversations",
    userId,
    {
      method: "POST",
      body: JSON.stringify(payload)
    }
  );
}

export async function fetchConversationMessages(
  userId: string,
  threadId: string,
  limit = 100
) {
  return request<{ items: ConversationMessage[]; source: string }>(
    `/api/workspace/conversations/${threadId}/messages?limit=${limit}`,
    userId,
    { method: "GET" }
  );
}

export async function deleteConversation(userId: string, threadId: string) {
  return request<{ deleted: boolean; thread_id: string; source: string }>(
    `/api/workspace/conversations/${threadId}`,
    userId,
    { method: "DELETE" }
  );
}

export async function appendConversationMessage(
  userId: string,
  threadId: string,
  payload: {
    role: "user" | "assistant" | "system";
    content: string;
    metadata?: Record<string, unknown>;
  }
) {
  return request<{ item: ConversationMessage; source: string }>(
    `/api/workspace/conversations/${threadId}/messages`,
    userId,
    {
      method: "POST",
      body: JSON.stringify(payload)
    }
  );
}

export async function agentChat(params: {
  userId: string;
  message: string;
  threadId?: string | null;
  title?: string | null;
  mode: AutonomyMode;
  personaStats?: PersonaStats | null;
  knowledgePrompt?: string | null;
  useMock?: boolean;
  debugRaw?: boolean;
}) {
  return request<{
    thread_id: string;
    reply: string;
    assistant_message: ConversationMessage;
    mode: AutonomyMode;
  }>("/api/agents/chat", params.userId, {
    method: "POST",
    body: JSON.stringify({
      message: params.message,
      thread_id: params.threadId ?? null,
      title: params.title ?? null,
      mode: params.mode,
      persona_stats: params.personaStats ?? null,
      knowledge_prompt: params.knowledgePrompt ?? null,
      use_mock: params.useMock ?? false,
      debug_raw: params.debugRaw ?? false
    })
  }, 420000);
}

export async function fetchConnectionStatus(userId: string) {
  return request<WorkspaceConnectionStatus>("/api/agents/connection-status", userId, {
    method: "GET"
  });
}

export async function listUserKeys(userId: string) {
  return request<{ items: ApiKeyMeta[]; source: string }>("/api/keys", userId, {
    method: "GET"
  });
}

export async function storeUserKey(
  userId: string,
  payload: { key_name: string; plaintext_key: string }
) {
  return request<{ status: string; key_name: string; source: string }>(
    "/api/keys",
    userId,
    {
      method: "POST",
      body: JSON.stringify(payload)
    }
  );
}
