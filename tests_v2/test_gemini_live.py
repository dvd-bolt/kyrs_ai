from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from papercraft.config import AppSettings
from papercraft.infrastructure.gemini import GeminiGateway

pytestmark = pytest.mark.live


class _LiveStructuredReply(BaseModel):
    status: str = Field(pattern="^ok$")
    value: int


def _live_gateway(tmp_path: Path) -> GeminiGateway:
    if os.getenv("PAPERCRAFT_RUN_GEMINI_TESTS") != "1":
        pytest.skip("set PAPERCRAFT_RUN_GEMINI_TESTS=1 and GEMINI_API_KEY for live tests")
    settings = AppSettings.from_environment().model_copy(
        update={"projects_root": tmp_path}
    )
    if settings.gemini_api_key is None:
        pytest.skip("GEMINI_API_KEY is not configured")
    return GeminiGateway(settings)


def test_live_structured_output_contract(tmp_path: Path) -> None:
    gateway = _live_gateway(tmp_path)
    gateway.health_check()
    response = gateway.generate_structured(
        prompt="Return status='ok' and value=42.",
        schema=_LiveStructuredReply,
        role="extractor",
        system_instruction="Follow the output schema exactly.",
    )
    assert response.status == "ok"
    assert response.value == 42


def test_live_file_lifecycle(tmp_path: Path) -> None:
    gateway = _live_gateway(tmp_path)
    fixture = tmp_path / "anonymous-fixture.txt"
    fixture.write_text("PaperCraft anonymous integration fixture.", encoding="utf-8")
    uploaded = gateway.upload_file(fixture)
    try:
        assert uploaded.name
        assert uploaded.uri
    finally:
        gateway.delete_file(uploaded.name)
