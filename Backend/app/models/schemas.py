from datetime import datetime, date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


TaskStatus = Literal["todo", "in_progress", "review", "done"]


class PersonaStats(BaseModel):
    creativity: int = Field(default=55, ge=0, le=100)
    logic: int = Field(default=70, ge=0, le=100)
    critical_thinking: int = Field(default=75, ge=0, le=100)
    data_dependency: int = Field(default=80, ge=0, le=100)
    cautiousness: int = Field(default=55, ge=0, le=100)
    drive: int = Field(default=65, ge=0, le=100)
    # Backward-compat input key from legacy UI/data.
    empathy: int | None = Field(default=None, ge=0, le=100, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if "critical" in normalized and "critical_thinking" not in normalized:
            normalized["critical_thinking"] = normalized["critical"]
        if "data_dependence" in normalized and "data_dependency" not in normalized:
            normalized["data_dependency"] = normalized["data_dependence"]
        if "empathy" in normalized and "cautiousness" not in normalized:
            normalized["cautiousness"] = normalized["empathy"]
        return normalized

    def as_dict(self) -> dict[str, int]:
        return {
            "creativity": self.creativity,
            "logic": self.logic,
            "critical_thinking": self.critical_thinking,
            "data_dependency": self.data_dependency,
            "cautiousness": self.cautiousness,
            "drive": self.drive,
        }


class DeepTaskRequest(BaseModel):
    task: str = Field(min_length=3, max_length=500)
    persona_stats: PersonaStats
    worker_count: int = Field(default=5, ge=3, le=6)
    trigger_source: Literal["console", "chat"] = "console"
    notify_email: EmailStr | None = None
    use_mock: bool = True


class DeepTaskStartResponse(BaseModel):
    run_id: str
    status: str
    websocket_path: str


class KeyStoreRequest(BaseModel):
    key_name: str = Field(min_length=2, max_length=50)
    plaintext_key: str = Field(min_length=8, max_length=4096)

    @field_validator("key_name")
    @classmethod
    def normalize_key_name(cls, v: str) -> str:
        return v.strip().lower().replace(" ", "_")


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    status: TaskStatus = "todo"
    due_date: date | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    status: TaskStatus | None = None
    due_date: date | None = None


class PersonalAgentTriggerRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=1000)


class PersonalSchoolActionRequest(BaseModel):
    action: str = Field(min_length=3, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class SnippetWriteRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


class MeetingReservationCreateRequest(BaseModel):
    room_id: int
    start_at: str = Field(
        description="ISO datetime, example: 2026-04-03T10:00:00+09:00"
    )
    end_at: str = Field(
        description="ISO datetime, example: 2026-04-03T11:00:00+09:00"
    )
    purpose: str | None = Field(default=None, max_length=500)


class AgentEvent(BaseModel):
    event_type: str
    run_id: str | None = None
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentLogCreate(BaseModel):
    run_id: str
    task: str
    final_summary: str
    arguments: dict[str, str]
    sources: list[dict[str, Any]]
    feedback_score: int | None = Field(default=None, ge=1, le=5)


class StoredTask(BaseModel):
    id: UUID
    user_id: UUID | str
    title: str
    description: str | None
    status: TaskStatus
    due_date: date | None
    created_at: datetime
    updated_at: datetime


class PersonaProfile(BaseModel):
    id: str = Field(min_length=2, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    stats: PersonaStats


class ApprovalPolicy(BaseModel):
    cautious_requires_approval: bool = True
    balanced_requires_approval: bool = True
    creative_requires_approval: bool = True
    autonomous_needs_first_warning: bool = True
    autonomous_warning_accepted: bool = False


class UserSettingsPayload(BaseModel):
    theme: Literal["light", "dark", "system"] = "system"
    dev_mode: bool = False
    debug_raw_mode: bool = False
    ai_provider: Literal["claude", "openai"] = "claude"
    claude_base_url: str | None = Field(default=None, max_length=500)
    preferred_model: str | None = Field(default=None, max_length=120)
    openai_preferred_model: str | None = Field(default=None, max_length=120)
    knowledge_base_prompt: str | None = Field(default=None, max_length=12000)
    chat_mode_personas: dict[str, PersonaStats] = Field(default_factory=dict)
    active_persona_id: str | None = None
    personas: list[PersonaProfile] = Field(default_factory=list)
    discussion_rounds: int = Field(default=3, ge=2, le=5)
    notebooklm_profile: str | None = Field(default=None, max_length=120)
    notebooklm_allow_oauth_mismatch: bool = True
    notebooklm_auto_switch_on_slide_failure: bool = True
    approval_policy: ApprovalPolicy = Field(default_factory=ApprovalPolicy)


class ConversationThreadCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    thread_id: str | None = Field(default=None, max_length=120)


class ConversationMessageCreateRequest(BaseModel):
    role: Literal["user", "assistant", "system"] = "user"
    content: str = Field(min_length=1, max_length=6000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    thread_id: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=120)
    mode: Literal["cautious", "balanced", "creative", "autonomous"] = "balanced"
    ai_provider: Literal["claude", "openai"] | None = None
    claude_model: str | None = Field(default=None, max_length=120)
    openai_model: str | None = Field(default=None, max_length=120)
    persona_stats: PersonaStats | None = None
    knowledge_prompt: str | None = Field(default=None, max_length=12000)
    use_mock: bool = False
    debug_raw: bool = False


class ActionStep(BaseModel):
    step_id: str = Field(min_length=1, max_length=64)
    tool: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=500)
    method: str | None = Field(default=None, max_length=12)
    path: str | None = Field(default=None, max_length=300)
    query: str | None = Field(default=None, max_length=1200)
    params: dict[str, Any] = Field(default_factory=dict)


class ActionPlan(BaseModel):
    objective: str = Field(min_length=1, max_length=800)
    requires_search_first: bool = True
    steps: list[ActionStep] = Field(default_factory=list, min_length=1, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)


class PlannerOutput(BaseModel):
    plan: ActionPlan
    search_context: list[dict[str, Any]] = Field(default_factory=list)
    planner_notes: str | None = Field(default=None, max_length=4000)


class ExecutionStepResult(BaseModel):
    step_id: str
    tool: str
    status: Literal["ok", "error", "empty"]
    result: Any = None
    error: str | None = None


class PipelineTrace(BaseModel):
    run_id: str
    mode: Literal["cautious", "balanced", "creative", "autonomous"]
    planner_output: PlannerOutput
    execution_results: list[ExecutionStepResult]
    final_markdown: str
    retry_count: int = 0


class UserBootstrapRequest(BaseModel):
    user_id: str = Field(min_length=3, max_length=120)
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=120)
    avatar_url: str | None = Field(default=None, max_length=500)
