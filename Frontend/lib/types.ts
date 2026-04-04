export type PersonaAxisKey =
  | "creativity"
  | "logic"
  | "critical_thinking"
  | "data_dependency"
  | "cautiousness"
  | "drive";

export type PersonaStats = Record<PersonaAxisKey, number>;

export const PERSONA_AXES: { key: PersonaAxisKey; label: string }[] = [
  { key: "creativity", label: "창의성" },
  { key: "logic", label: "논리력" },
  { key: "critical_thinking", label: "비판적 사고" },
  { key: "data_dependency", label: "데이터 의존도" },
  { key: "cautiousness", label: "신중함" },
  { key: "drive", label: "추진력" }
];

function clampStat(value: unknown, fallback = 50): number {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(0, Math.min(100, Math.round(numeric)));
}

export function normalizePersonaStats(
  value: Partial<Record<string, unknown>> | null | undefined
): PersonaStats {
  const source = value ?? {};
  return {
    creativity: clampStat(source.creativity),
    logic: clampStat(source.logic),
    critical_thinking: clampStat(
      source.critical_thinking ?? source.critical
    ),
    data_dependency: clampStat(
      source.data_dependency ?? source.data_dependence
    ),
    cautiousness: clampStat(source.cautiousness ?? source.empathy),
    drive: clampStat(source.drive)
  };
}

export type TaskStatus = "todo" | "in_progress" | "review" | "done";

export type TaskItem = {
  id: string;
  user_id: string;
  title: string;
  description?: string | null;
  status: TaskStatus;
  due_date?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type AgentEvent = {
  event_type: string;
  run_id?: string | null;
  timestamp?: string;
  payload: Record<string, unknown>;
};

export type ThemeMode = "light" | "dark" | "system";
export type AutonomyMode = "cautious" | "balanced" | "creative" | "autonomous";

export type PersonaProfile = {
  id: string;
  name: string;
  stats: PersonaStats;
};

export type ApprovalPolicy = {
  cautious_requires_approval: boolean;
  balanced_requires_approval: boolean;
  creative_requires_approval: boolean;
  autonomous_needs_first_warning: boolean;
  autonomous_warning_accepted: boolean;
};

export type UserSettings = {
  theme: ThemeMode;
  dev_mode: boolean;
  debug_raw_mode: boolean;
  ai_provider: "claude" | "openai";
  claude_base_url?: string | null;
  preferred_model?: string | null;
  openai_preferred_model?: string | null;
  knowledge_base_prompt?: string | null;
  chat_mode_personas: Record<AutonomyMode, PersonaStats>;
  active_persona_id: string | null;
  personas: PersonaProfile[];
  approval_policy: ApprovalPolicy;
};

export type ConversationThread = {
  id: string;
  user_id: string;
  title: string;
  created_at?: string;
  updated_at?: string;
};

export type ConversationMessage = {
  id: string;
  thread_id: string;
  user_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  metadata?: Record<string, unknown>;
  created_at?: string;
};

export type ApiKeyMeta = {
  id: string;
  key_name: string;
  key_version: number;
  created_at?: string;
  updated_at?: string;
};

export type ClaudeConnectionStatus = {
  configured: boolean;
  base_url?: string | null;
  has_auth_token: boolean;
  has_api_key: boolean;
  reachable: boolean;
  status: string;
  attempts: Array<Record<string, unknown>>;
  available_models?: string[];
};

export type WorkspaceConnectionStatus = {
  claude: ClaudeConnectionStatus;
  school_api: {
    token_saved: boolean;
    reachable?: boolean;
    status?: string;
    reason?: string | null;
    source?: "user_key" | "env_fallback" | "none";
  };
  google_workspace: {
    token_saved: boolean;
    reachable?: boolean;
    status?: string;
    reason?: string | null;
    oauth_configured?: boolean;
    token_expired?: boolean;
    refresh_available?: boolean;
    services?: {
      drive?: { status?: string; http_status?: number; reason?: string };
      gmail?: { status?: string; http_status?: number; reason?: string };
      calendar?: { status?: string; http_status?: number; reason?: string };
    };
  };
  openai_fallback?: {
    token_saved: boolean;
    reachable?: boolean;
    status?: string;
    model?: string;
  };
  active_provider?: "claude" | "openai";
  database: { connected: boolean; source: "supabase" | "dev_store"; reason?: string | null };
  web_search?: { reachable?: boolean; status?: string; result_count?: number; reason?: string };
  tools_catalog?: { builtin_count?: number; openapi_count?: number };
};
