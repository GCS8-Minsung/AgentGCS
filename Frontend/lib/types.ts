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

