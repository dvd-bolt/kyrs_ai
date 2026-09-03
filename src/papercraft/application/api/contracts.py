"""Versioned DTOs for the desktop-to-application and worker boundaries.

The models in this module deliberately contain only safe, serialisable data.
They are the public vocabulary for the future UI: domain objects and SQLite
repositories stay behind :class:`DesktopApplication`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

APPLICATION_API_VERSION = 1
WORKER_PROTOCOL_VERSION = 1


class ApiDto(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=True)

    api_version: Literal[1] = 1


class WorkerAction(StrEnum):
    START_GENERATION = "start_generation"
    PAUSE_GENERATION = "pause_generation"
    RESUME_GENERATION = "resume_generation"
    CANCEL_GENERATION = "cancel_generation"
    RETRY_GENERATION = "retry_generation"
    REGENERATE_SECTION = "regenerate_section"


class WorkerRequest(BaseModel):
    """The complete JSONL command envelope defined by worker protocol v1."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    protocol_version: Literal[1] = 1
    request_id: str = Field(min_length=1)
    action: WorkerAction
    project_id: str = Field(min_length=1)
    run_id: str | None = None
    stage_id: str | None = None
    section_id: str | None = None

    @model_validator(mode="after")
    def validate_target(self) -> WorkerRequest:
        if self.action is WorkerAction.START_GENERATION:
            if self.run_id is not None:
                raise ValueError("run_id must be null for start_generation")
        elif self.run_id is None:
            raise ValueError("run_id is required for this action")
        if self.action is WorkerAction.REGENERATE_SECTION and self.section_id is None:
            raise ValueError("section_id is required for regenerate_section")
        if self.action is not WorkerAction.REGENERATE_SECTION and self.section_id is not None:
            raise ValueError("section_id is allowed only for regenerate_section")
        if self.action is not WorkerAction.RETRY_GENERATION and self.stage_id is not None:
            raise ValueError("stage_id is allowed only for retry_generation")
        return self

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        payload.pop("request_id")
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class Money(ApiDto):
    amount: str = Field(pattern=r"^\d+(?:\.\d+)?$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @classmethod
    def from_decimal(cls, amount: Decimal, currency: str) -> Money:
        return cls(amount=format(amount, "f"), currency=currency.upper())


class CredentialStatus(ApiDto):
    configured: bool
    verified: bool
    state: Literal["missing", "valid", "unverified", "invalid"]
    last_checked_at: str | None = None
    safe_message: str


class ProviderCheck(ApiDto):
    provider: Literal["gemini"] = "gemini"
    ok: bool
    state: Literal["valid", "invalid", "unavailable"]
    checked_at: str
    retryable: bool
    safe_message: str


class RunSnapshot(ApiDto):
    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    status: str
    stage: str | None = None
    progress: float = Field(ge=0, le=1)
    message: str = ""
    retry_at: str | None = None
    estimated_cost: Money | None = None
    actual_cost: Money
    started_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None
    can_pause: bool
    can_resume: bool
    can_cancel: bool
    can_retry: bool


class WorkerEvent(BaseModel):
    """A safe, persisted-event projection emitted as one UTF-8 JSONL line."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    protocol_version: Literal[1] = 1
    request_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    run_id: str | None = None
    sequence: int = Field(ge=1)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    event_type: Literal[
        "request_accepted", "progress", "run_state", "request_finished", "request_failed", "heartbeat"
    ]
    stage: str | None = None
    status: str | None = None
    progress: float = Field(default=0, ge=0, le=1)
    message: str = ""
    error_code: str | None = None
    retry_at: str | None = None
    estimated_cost: Money | None = None


__all__ = [
    "APPLICATION_API_VERSION",
    "WORKER_PROTOCOL_VERSION",
    "ApiDto",
    "CredentialStatus",
    "Money",
    "ProviderCheck",
    "RunSnapshot",
    "WorkerAction",
    "WorkerEvent",
    "WorkerRequest",
]
