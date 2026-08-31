from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock

import pytest
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from papercraft.application.schemas import ResearchPlan
from papercraft.config import AppSettings
from papercraft.infrastructure.gemini import (
    CredentialSecretStore,
    GeminiGateway,
    UsageRecord,
)

pytestmark = pytest.mark.live


class _LiveStructuredReply(BaseModel):
    status: str = Field(pattern="^ok$")
    value: int


class _LiveVisionReply(BaseModel):
    text: str
    number: int


def _required_positive_usd_limit(variable: str) -> Decimal:
    """Fail closed before an opt-in live suite can spend provider quota."""

    raw = os.getenv(variable, "").strip()
    if not raw:
        pytest.fail(f"{variable} must be set to a positive USD limit for live Gemini tests")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        pytest.fail(f"{variable} must be a decimal USD limit")
    if not value.is_finite() or value <= 0:
        pytest.fail(f"{variable} must be a finite positive USD limit")
    return value


@dataclass(slots=True)
class _LiveUsageBudget:
    """A process-wide ceiling for direct gateway contract tests.

    Autopilot runs already have a persisted per-run cap. The isolated gateway
    tests do not, so this sink exposes the same pre-admission ``limit_reached``
    protocol and records the aggregate suite spend without logging prompts or
    credentials.
    """

    limit: Decimal
    spent: Decimal = Decimal("0")
    _lock: RLock = field(default_factory=RLock, repr=False)

    def __call__(self, record: UsageRecord) -> None:
        with self._lock:
            self.spent += record.estimated_cost
            if self.spent > self.limit:
                pytest.fail(
                    "Live Gemini test cost exceeded PAPERCRAFT_LIVE_TEST_MAX_COST_USD "
                    f"({self.spent} > {self.limit} USD)"
                )

    def limit_reached(self) -> bool:
        with self._lock:
            return self.spent >= self.limit


@dataclass(slots=True)
class _LiveUsageSink:
    budget: _LiveUsageBudget
    records: list[UsageRecord] | None = None

    def __call__(self, record: UsageRecord) -> None:
        self.budget(record)
        if self.records is not None:
            self.records.append(record)

    def limit_reached(self) -> bool:
        return self.budget.limit_reached()


_LIVE_BUDGET: _LiveUsageBudget | None = None
_BACKGROUND_POLL_ATTEMPTS = 20
_BACKGROUND_POLL_SECONDS = 0.5


def _live_budget() -> _LiveUsageBudget:
    global _LIVE_BUDGET
    if _LIVE_BUDGET is None:
        _LIVE_BUDGET = _LiveUsageBudget(
            _required_positive_usd_limit("PAPERCRAFT_LIVE_TEST_MAX_COST_USD")
        )
    return _LIVE_BUDGET


def _live_gateway(
    tmp_path: Path,
    *,
    usage_sink: list[UsageRecord] | None = None,
) -> GeminiGateway:
    if os.getenv("PAPERCRAFT_RUN_GEMINI_TESTS") != "1":
        pytest.skip("set PAPERCRAFT_RUN_GEMINI_TESTS=1 for live tests")
    budget = _live_budget()
    if not CredentialSecretStore().get_api_key():
        pytest.fail(
            "PAPERCRAFT_RUN_GEMINI_TESTS=1 but Gemini is not configured in "
            "Credential Manager or GEMINI_API_KEY"
        )
    settings = AppSettings.from_environment().model_copy(update={"projects_root": tmp_path})
    return GeminiGateway(settings, usage_sink=_LiveUsageSink(budget, usage_sink))


def _wait_for_background_cancellation(gateway: GeminiGateway, interaction_id: str) -> None:
    """Wait a bounded amount of time for the provider to finish cancellation."""

    for _ in range(_BACKGROUND_POLL_ATTEMPTS):
        status = gateway.get_interaction_status(interaction_id)
        if status in {"cancelled", "canceled"}:
            return
        if status is None:
            break
        time.sleep(_BACKGROUND_POLL_SECONDS)
    pytest.fail("Background interaction did not reach cancelled status before cleanup")


def _wait_for_deleted_interaction(gateway: GeminiGateway, interaction_id: str) -> None:
    """Prove the stored provider object is gone after the delete request."""

    for _ in range(_BACKGROUND_POLL_ATTEMPTS):
        if gateway.get_interaction_status(interaction_id) is None:
            return
        time.sleep(_BACKGROUND_POLL_SECONDS)
    pytest.fail("Background interaction remained stored after deletion")


def test_live_budget_requires_explicit_positive_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    global _LIVE_BUDGET
    _LIVE_BUDGET = None
    monkeypatch.delenv("PAPERCRAFT_LIVE_TEST_MAX_COST_USD", raising=False)
    with pytest.raises(pytest.fail.Exception, match="PAPERCRAFT_LIVE_TEST_MAX_COST_USD"):
        _live_budget()

    monkeypatch.setenv("PAPERCRAFT_LIVE_TEST_MAX_COST_USD", "0.25")
    assert _live_budget().limit == Decimal("0.25")
    _LIVE_BUDGET = None


def test_live_interactions_thinking_and_structured_output(tmp_path: Path) -> None:
    usage: list[UsageRecord] = []
    gateway = _live_gateway(tmp_path, usage_sink=usage)
    gateway.health_check()
    response = gateway.generate_structured(
        prompt="Return status='ok' and value=42.",
        schema=_LiveStructuredReply,
        role="extraction",
        system_instruction="Follow the output schema exactly.",
    )
    assert response == _LiveStructuredReply(status="ok", value=42)
    assert {record.model for record in usage} == {
        "gemini-3.5-flash-lite",
    }
    assert all(record.total_tokens > 0 for record in usage)
    assert all(record.metadata.get("client_request_id") for record in usage)
    assert all(
        record.metadata.get("request_id_source") in {"provider", "unavailable"}
        for record in usage
    )
    assert all(int(record.metadata.get("thought_tokens", 0)) >= 0 for record in usage)
    assert all(record.estimated_cost >= 0 for record in usage)


def test_live_research_plan_structured_contract(tmp_path: Path) -> None:
    usage: list[UsageRecord] = []
    gateway = _live_gateway(tmp_path, usage_sink=usage)
    prompt = (
        "For an academic paper about reproducible software testing, return exactly one short, "
        "checkable claim and a precise web search query for verifying it."
    )

    plan = gateway.generate_structured(
        prompt=prompt,
        schema=ResearchPlan,
        role="research",
        system_instruction="Return only a research plan that conforms to the JSON schema.",
    )

    assert isinstance(plan, ResearchPlan)
    assert plan.claims
    assert all(claim.text.strip() and claim.search_query.strip() for claim in plan.claims)
    record = usage[-1]
    assert record.operation == "generate_structured"
    assert record.model == gateway.settings.model_policy.research
    assert record.total_tokens > 0
    assert record.estimated_cost > 0
    assert record.metadata.get("client_request_id")
    assert prompt not in str(record.metadata)


def test_live_file_vision_lifecycle(tmp_path: Path) -> None:
    gateway = _live_gateway(tmp_path)
    fixture = tmp_path / "vision-contract.png"
    image = Image.new("RGB", (640, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 60), "PAPERCRAFT LIVE VISION", fill="black")
    draw.text((40, 130), "NUMBER 42", fill="black")
    image.save(fixture)
    uploaded = gateway.upload_file(fixture)
    try:
        response = gateway.generate_structured(
            prompt="Read the image. Return text='PAPERCRAFT LIVE VISION' and number=42.",
            schema=_LiveVisionReply,
            role="extraction",
            files=[uploaded],
        )
        assert response.number == 42
        assert "PAPERCRAFT" in response.text.upper()
    finally:
        gateway.delete_file(uploaded.name)


def test_live_search_grounding_and_cost_metadata(tmp_path: Path) -> None:
    usage: list[UsageRecord] = []
    gateway = _live_gateway(tmp_path, usage_sink=usage)
    result = gateway.search_grounded(
        prompt=(
            "Use Google Search to identify the official Google AI documentation page for "
            "Gemini 3.7 Flash. State its stable model code and cite the official page."
        )
    )
    assert result.text.strip()
    assert result.raw_steps
    assert any(
        str(item.get("url") or item.get("source") or "").startswith("https://")
        for item in result.annotations
    )
    record = usage[-1]
    assert record.operation == "search_grounded"
    assert int(record.metadata["search_queries"]) >= 1
    assert record.estimated_cost > 0


def test_live_image_generation_and_cost(tmp_path: Path) -> None:
    usage: list[UsageRecord] = []
    gateway = _live_gateway(tmp_path, usage_sink=usage)
    destination = tmp_path / "gemini-contract.png"
    gateway.generate_image(
        prompt=(
            "A clean academic icon: a single blue paper sheet with a small green check mark, "
            "plain white background, no words, no logo, no watermark."
        ),
        destination=destination,
    )
    with Image.open(destination) as image:
        image.verify()
    assert destination.stat().st_size > 1000
    assert usage[-1].operation == "generate_image"
    assert usage[-1].estimated_cost >= gateway.settings.pricing_policy.image_2k_estimate


def test_live_embedding_2_contract(tmp_path: Path) -> None:
    gateway = _live_gateway(tmp_path)
    vectors = gateway.embed_texts(
        ["academic evidence", "проверяемое утверждение"],
        output_dimensionality=768,
    )
    assert len(vectors) == 2
    assert all(len(vector) == 768 for vector in vectors)
    assert all(any(value != 0 for value in vector) for vector in vectors)


def test_live_background_cancellation(tmp_path: Path) -> None:
    if os.getenv("PAPERCRAFT_RUN_BACKGROUND_LIFECYCLE_TESTS") != "1":
        pytest.skip(
            "set PAPERCRAFT_RUN_BACKGROUND_LIFECYCLE_TESTS=1 for the stored background lifecycle"
        )
    gateway = _live_gateway(tmp_path)
    interaction_id: str | None = None
    try:
        interaction_id = gateway.start_background_text(
            prompt=(
                "Write one 200-word technical note about safe cancellation of an "
                "asynchronous task."
            ),
            role="research",
        )
        gateway.cancel_interaction(interaction_id)
        _wait_for_background_cancellation(gateway, interaction_id)
    finally:
        if interaction_id is not None:
            gateway.delete_interaction(interaction_id)
            _wait_for_deleted_interaction(gateway, interaction_id)
