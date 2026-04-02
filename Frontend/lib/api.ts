import { PersonaStats, TaskItem, TaskStatus } from "@/lib/types";

const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

async function request<T>(
  path: string,
  userId: string,
  options?: RequestInit
): Promise<T> {
  const response = await fetch(`${backendUrl}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "x-user-id": userId,
      ...(options?.headers ?? {})
    }
  });

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
      use_mock: params.useMock ?? true
    })
  });
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

