from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

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


def _live_gateway(
    tmp_path: Path,
    *,
    usage_sink: list[UsageRecord] | None = None,
) -> GeminiGateway:
    if os.getenv("PAPERCRAFT_RUN_GEMINI_TESTS") != "1":
        pytest.skip("set PAPERCRAFT_RUN_GEMINI_TESTS=1 for live tests")
    if not CredentialSecretStore().get_api_key():
        pytest.skip("Gemini is not configured in Credential Manager or GEMINI_API_KEY")
    settings = AppSettings.from_environment().model_copy(update={"projects_root": tmp_path})
    return GeminiGateway(settings, usage_sink=usage_sink.append if usage_sink is not None else None)


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
        "gemini-3.7-flash",
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
    gateway = _live_gateway(tmp_path)
    interaction_id = gateway.start_background_text(
        prompt=(
            "Produce a highly detailed 50,000-word technical history of distributed systems, "
            "with a long chronological appendix."
        ),
        role="research",
    )
    status = gateway.cancel_interaction(interaction_id)
    assert "cancel" in status
