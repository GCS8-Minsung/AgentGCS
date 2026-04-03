export type PersonaAxisKey =
  | "creativity"
  | "logic"
  | "critical_thinking"
  | "data_dependency"
  | "empathy"
  | "drive";

export type PersonaStats = Record<PersonaAxisKey, number>;

export const PERSONA_AXES: { key: PersonaAxisKey; label: string }[] = [
  { key: "creativity", label: "창의성" },
  { key: "logic", label: "논리력" },
  { key: "critical_thinking", label: "비판적 사고" },
  { key: "data_dependency", label: "데이터 의존도" },
  { key: "empathy", label: "공감 능력" },
  { key: "drive", label: "추진력" }
];

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
  claude_base_url?: string | null;
  preferred_model?: string | null;
  default_notify_email?: string | null;
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
};

export type WorkspaceConnectionStatus = {
  claude: ClaudeConnectionStatus;
  school_api: { token_saved: boolean };
  google_workspace: { token_saved: boolean };
};
