from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, AfterValidator, field_validator


def uuid_string(value):
    return str(UUID(value))


Id = Annotated[str, AfterValidator(uuid_string)]
Text = Annotated[str, Field(min_length=1, max_length=160)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    model_config = ConfigDict(extra="forbid")


class Password(Input):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)
    model_config = ConfigDict(extra="forbid")


class UserCreate(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    @field_validator("email", "name", mode="before")
    @classmethod
    def trim_identity(cls, value):
        return value.strip() if isinstance(value, str) else value

    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    name: Text
    password: str = Field(min_length=12, max_length=256)
    is_admin: bool = False
    clearance: int = Field(default=1, ge=1, le=3)


class UserUpdate(Input):
    active: bool | None = None
    clearance: int | None = Field(default=None, ge=1, le=3)


class ScopeCreate(Input):
    create_default_workspace: bool = True
    name: Text
    kind: str = Field(min_length=1, max_length=80)
    parent_id: Id | None = None


class GrantCreate(Input):
    user_id: Id
    scope_id: Id


class ProjectCreate(Input):
    name: Text
    goal: str = Field(default="", max_length=4000)


class ProjectUpdate(Input):
    name: Text | None = None
    goal: str | None = Field(default=None, max_length=4000)
    archived: bool | None = None


class NoteCreate(Input):
    title: str = Field(default="", max_length=160)
    body: str = Field(min_length=1, max_length=100000)
    kind: Literal["observation", "thought", "hypothesis", "decision", "commitment", "minutes"] = (
        "observation"
    )
    project_id: Id | None = None


class NoteUpdate(Input):
    title: Text | None = None
    body: str | None = Field(default=None, min_length=1, max_length=100000)
    kind: (
        Literal["observation", "thought", "hypothesis", "decision", "commitment", "minutes"] | None
    ) = None
    project_id: Id | None = None


class MemoryCreate(Input):
    note_id: Id
    text: str = Field(min_length=1, max_length=4000)


class MemoryUpdate(Input):
    text: str = Field(min_length=1, max_length=4000)


class ReminderCreate(Input):
    note_id: Id
    title: str = Field(min_length=1, max_length=500)
    due_at: datetime

    @field_validator("due_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Include a timezone offset")
        return value


class ReminderUpdate(Input):
    status: Literal["open", "waiting", "snoozed", "done", "cancelled"]
    due_at: datetime | None = None

    @field_validator("due_at")
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Include a timezone offset")
        return value


class WorkspaceCreate(Input):
    name: Text
    description: str = Field(default="", max_length=4000)
    scope_id: Id
    classification: int = Field(default=1, ge=1, le=3)
    external_ai_enabled: bool = False


class WorkspaceUpdate(Input):
    name: Text | None = None
    description: str | None = Field(default=None, max_length=4000)
    external_ai_enabled: bool | None = None


class MemberCreate(Input):
    user_id: Id
    role: Literal["viewer", "member", "manager"] = "member"


class ConversationCreate(Input):
    title: Text = "New conversation"
    workspace_id: Id | None = None
    project_id: Id | None = None


class ChatSend(Input):
    content: str = Field(min_length=1, max_length=12000)
    model: str | None = Field(default=None, max_length=200)
    external_ai_consent: bool = False


class IncidentCreate(Input):
    workspace_id: Id
    title: Text
    summary: str = Field(min_length=1, max_length=6000)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    assignee_id: Id
    decision: str = Field(min_length=1, max_length=2000)
    published: bool = False
    request_id: str = Field(min_length=16, max_length=100)
    note_id: Id | None = None


class IncidentAction(Input):
    action: Literal["acknowledge", "resolve", "publish", "escalate"]
    reason: str = Field(default="", max_length=6000)


class SubscriptionUpdate(Input):
    scope_ids: list[Id] = Field(max_length=100)


class JobCreate(Input):
    name: Text
    instructions: str = Field(min_length=1, max_length=12000)
    model: str | None = Field(default=None, max_length=200)
    workspace_id: Id | None = None
    interval_minutes: int | None = Field(default=None, ge=15, le=20160)
    next_run_at: datetime
    external_ai_enabled: bool = False
    dependency_ids: list[Id] = Field(default_factory=list, max_length=20)
    task_type: Literal[
        "analysis", "validation", "handover", "production_comparison", "morning_brief"
    ] = "analysis"
    config: dict = Field(default_factory=dict)
    timezone: str = Field(default="UTC", max_length=80)
    dependency_policy: Literal["pass", "pass_warn", "any"] = "pass_warn"

    @field_validator("next_run_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Include a timezone offset")
        return value


class JobUpdate(Input):
    status: Literal["active", "paused"]
