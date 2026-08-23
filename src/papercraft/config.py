from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field, SecretStr, field_validator


class ModelPolicy(BaseModel):
    """Pinned Gemini roles used for the lifetime of one generation run."""

    classification: str = "gemini-3.5-flash-lite"
    extraction: str = "gemini-3.5-flash-lite"
    requirements: str = "gemini-3.7-flash"
    blueprint: str = "gemini-3.7-flash"
    research: str = "gemini-3.7-flash"
    writer: str = "gemini-3.7-flash"
    critic: str = "gemini-3.7-flash"
    final_review: str = "gemini-3.7-flash"
    visual_qa: str = "gemini-3.7-flash"
    image: str = "gemini-3.1-flash-image"
    embedding: str = "gemini-embedding-2"
    version: str = "2026-08-21"

    @field_validator(
        "classification",
        "extraction",
        "requirements",
        "blueprint",
        "research",
        "writer",
        "critic",
        "final_review",
        "visual_qa",
        "image",
        "embedding",
    )
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ch.isspace() for ch in value):
            raise ValueError("Gemini model identifiers must be non-empty and contain no spaces")
        return value


ThinkingLevel = Literal["minimal", "low", "medium", "high"]


class ThinkingPolicy(BaseModel):
    """Explicit Interactions API thinking levels for every textual role."""

    classification: ThinkingLevel = "minimal"
    extraction: ThinkingLevel = "minimal"
    requirements: ThinkingLevel = "medium"
    blueprint: ThinkingLevel = "medium"
    research: ThinkingLevel = "medium"
    writer: ThinkingLevel = "medium"
    critic: ThinkingLevel = "high"
    final_review: ThinkingLevel = "high"
    visual_qa: ThinkingLevel = "medium"


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=5, ge=1, le=10)
    base_delay_seconds: float = Field(default=1.0, ge=0, le=30)
    maximum_delay_seconds: float = Field(default=60.0, ge=0, le=300)
    jitter_seconds: float = Field(default=0.5, ge=0, le=10)


class TokenPrice(BaseModel):
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)


class PricingPolicy(BaseModel):
    """Conservative paid-tier estimates; actual billing remains authoritative."""

    models: dict[str, TokenPrice] = Field(
        default_factory=lambda: {
            "gemini-3.7-flash": TokenPrice(
                input_per_million=Decimal("0.75"),
                output_per_million=Decimal("3.75"),
            ),
            "gemini-3.5-flash-lite": TokenPrice(
                input_per_million=Decimal("0.30"),
                output_per_million=Decimal("2.50"),
            ),
            "gemini-embedding-2": TokenPrice(
                input_per_million=Decimal("0.20"),
                output_per_million=Decimal("0"),
            ),
        }
    )
    image_2k_estimate: Decimal = Field(default=Decimal("0.101"), ge=0)
    search_query_estimate: Decimal = Field(default=Decimal("0.014"), ge=0)
    version: str = "2026-08-21"


class AppSettings(BaseModel):
    """Runtime settings without project data or persisted secrets."""

    projects_root: Path
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    gemini_api_key: SecretStr | None = None
    model_policy: ModelPolicy = Field(default_factory=ModelPolicy)
    thinking_policy: ThinkingPolicy = Field(default_factory=ThinkingPolicy)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    pricing_policy: PricingPolicy = Field(default_factory=PricingPolicy)
    request_timeout_seconds: float = Field(default=180.0, ge=5, le=1800)
    minimum_free_space_mb: int = Field(default=1024, ge=128)
    remote_file_consent_required: bool = True

    @classmethod
    def from_environment(cls) -> AppSettings:
        configured_root = os.getenv("PAPERCRAFT_PROJECTS_DIR")
        if configured_root:
            root = Path(configured_root).expanduser().resolve()
        else:
            local_app_data = os.getenv("LOCALAPPDATA")
            base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
            root = base / "PaperCraftAI" / "projects"

        raw_key = os.getenv("GEMINI_API_KEY", "").strip()
        log_level = os.getenv("PAPERCRAFT_LOG_LEVEL", "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            log_level = "INFO"
        return cls(
            projects_root=root,
            log_level=cast(Literal["DEBUG", "INFO", "WARNING", "ERROR"], log_level),
            gemini_api_key=SecretStr(raw_key) if raw_key else None,
        )

    def ensure_directories(self) -> None:
        self.projects_root.mkdir(parents=True, exist_ok=True)
