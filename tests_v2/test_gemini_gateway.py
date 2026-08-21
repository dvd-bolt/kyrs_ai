from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from papercraft.config import AppSettings, RetryPolicy
from papercraft.infrastructure.gemini import (
    FakeGeminiGateway,
    GeminiAuthenticationError,
    GeminiGateway,
)


class Payload(BaseModel):
    title: str
    count: int


class FakeInteractions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeFiles:
    def upload(self, *, file):
        return SimpleNamespace(name="files/example", uri="fake://example", mime_type="text/plain")

    def delete(self, *, name):
        return None


def settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        projects_root=tmp_path,
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0, jitter_seconds=0),
    )


def test_structured_generation_uses_schema(tmp_path: Path) -> None:
    response = SimpleNamespace(output_text='{"title":"План","count":3}', usage=None)
    client = SimpleNamespace(interactions=FakeInteractions([response]), files=FakeFiles())
    gateway = GeminiGateway(settings(tmp_path), client=client)
    result = gateway.generate_structured(prompt="build", schema=Payload, role="architect")
    assert result == Payload(title="План", count=3)
    sent = client.interactions.calls[0]
    assert sent["response_format"]["mime_type"] == "application/json"
    assert sent["response_format"]["schema"]["required"] == ["title", "count"]


def test_authentication_error_is_not_retried(tmp_path: Path) -> None:
    error = RuntimeError("401 invalid api key")
    interactions = FakeInteractions([error])
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    gateway = GeminiGateway(settings(tmp_path), client=client, sleep=lambda _: None)
    with pytest.raises(GeminiAuthenticationError):
        gateway.health_check()
    assert len(interactions.calls) == 1


def test_fake_gateway_is_explicit_and_schema_checked() -> None:
    fake = FakeGeminiGateway()
    fake.enqueue("generate_structured", {"title": "X", "count": 1})
    result = fake.generate_structured(prompt="x", schema=Payload, role="writer")
    assert result.count == 1
    assert fake.calls[0]["role"] == "writer"
