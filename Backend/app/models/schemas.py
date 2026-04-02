from datetime import datetime, date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


TaskStatus = Literal["todo", "in_progress", "review", "done"]


class PersonaStats(BaseModel):
    creativity: int = Field(default=55, ge=0, le=100)
    logic: int = Field(default=70, ge=0, le=100)
    critical_thinking: int = Field(default=75, ge=0, le=100)
    data_dependency: int = Field(default=80, ge=0, le=100)
    empathy: int = Field(default=45, ge=0, le=100)
    drive: int = Field(default=65, ge=0, le=100)

    def as_dict(self) -> dict[str, int]:
        return self.model_dump()


class DeepTaskRequest(BaseModel):
    task: str = Field(min_length=3, max_length=500)
    persona_stats: PersonaStats
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
