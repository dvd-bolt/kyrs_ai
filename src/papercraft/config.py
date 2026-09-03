from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field, field_validator


class ModelPolicy(BaseModel):
    """Pinned Gemini roles used for the lifetime of one generation run."""

    classification: str = "gemini-3.5-flash-lite"
    extraction: str = "gemini-3.5-flash-lite"
    requirements: str = "gemini-3.5-flash-lite"
    blueprint: str = "gemini-3.5-flash-lite"
    research: str = "gemini-3.5-flash-lite"
    writer: str = "gemini-3.5-flash-lite"
    critic: str = "gemini-3.5-flash-lite"
    final_review: str = "gemini-3.5-flash-lite"
    visual_qa: str = "gemini-3.5-flash-lite"
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


class ProviderPolicy(BaseModel):
    """Fallback models grouped by the Gemini capability they can replace.

    The primary model stays pinned in :class:`ModelPolicy` for every run.  A
    fallback is deliberately capability-specific: an image model can never be
    selected for text, structured output, or vision work.
    """

    text_fallback: str = "gemini-3.5-flash-lite"
    structured_fallback: str = "gemini-3.5-flash-lite"
    vision_fallback: str = "gemini-3.5-flash-lite"
    image_fallback: str = "gemini-3.1-flash-image"

    @field_validator("text_fallback", "structured_fallback", "vision_fallback", "image_fallback")
    @classmethod
    def validate_fallback_model_id(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ch.isspace() for ch in value):
            raise ValueError("Gemini fallback model identifiers must be non-empty and contain no spaces")
        return value


class ModelCapabilityRegistry:
    """Resolve a role to compatible primary/fallback model candidates."""

    _structured_roles = frozenset({"classification", "extraction", "requirements", "blueprint"})
    _vision_roles = frozenset({"visual_qa"})

    def __init__(self, models: ModelPolicy, provider: ProviderPolicy) -> None:
        self._models = models
        self._provider = provider

    def candidates(self, role: str) -> tuple[str, ...]:
        primary = getattr(self._models, role)
        if role == "image":
            fallback = self._provider.image_fallback
        elif role in self._vision_roles:
            fallback = self._provider.vision_fallback
        elif role in self._structured_roles:
            fallback = self._provider.structured_fallback
        else:
            fallback = self._provider.text_fallback
        return (primary,) if fallback == primary else (primary, fallback)


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


class PerformancePolicy(BaseModel):
    """Bounded concurrency and cache settings for a generation run.

    The limits are deliberately conservative.  They prevent a faster stage
    scheduler from turning one project into an uncontrolled burst of provider
    requests, while still allowing independent work to proceed in parallel.
    """

    max_concurrent_requests: int = Field(default=3, ge=1, le=16)
    max_research_requests: int = Field(default=2, ge=1, le=16)
    max_section_requests: int = Field(default=3, ge=1, le=16)
    max_image_requests: int = Field(default=2, ge=1, le=16)
    web_cache_ttl_hours: int = Field(default=168, ge=1, le=24 * 365)
    adaptive_throttling: bool = True
    recovery_successes: int = Field(default=8, ge=1, le=100)
    # Keep the safe one-worker rollback path as the production default until
    # live quota recovery and the golden-run gate have been completed.
    parallel_generation_enabled: bool = False


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
    model_policy: ModelPolicy = Field(default_factory=ModelPolicy)
    provider_policy: ProviderPolicy = Field(default_factory=ProviderPolicy)
    thinking_policy: ThinkingPolicy = Field(default_factory=ThinkingPolicy)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    performance_policy: PerformancePolicy = Field(default_factory=PerformancePolicy)
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

        log_level = os.getenv("PAPERCRAFT_LOG_LEVEL", "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            log_level = "INFO"
        return cls(
            projects_root=root,
            log_level=cast(Literal["DEBUG", "INFO", "WARNING", "ERROR"], log_level),
        )

    def ensure_directories(self) -> None:
        self.projects_root.mkdir(parents=True, exist_ok=True)
