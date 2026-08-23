from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, SecretStr

from papercraft.config import AppSettings, ModelPolicy, RetryPolicy, ThinkingPolicy
from papercraft.infrastructure.gemini import (
    FakeGeminiGateway,
    GeminiAuthenticationError,
    GeminiConfigurationError,
    GeminiGateway,
    GeminiGatewayError,
    GeminiSafetyError,
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


class FakeRawInteractionResponse:
    request_id = None

    def __init__(self, payload, headers):
        self.payload = payload
        self.headers = headers

    def parse(self):
        return self.payload


class FakeRawInteractions:
    def __init__(self, response, headers=None):
        self.response = response
        self.headers = headers or {}
        self.calls = []
        self.with_raw_response = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeRawInteractionResponse(self.response, self.headers)


def settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        projects_root=tmp_path,
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0, jitter_seconds=0),
    )


def test_owned_sdk_client_disables_nested_http_retries(tmp_path: Path) -> None:
    configured = settings(tmp_path).model_copy(
        update={"gemini_api_key": SecretStr("unit-test-only")}
    )
    gateway = GeminiGateway(configured)

    retry_config = gateway.client.interactions.sdk_configuration.retry_config
    assert retry_config.max_retries == 1
    assert retry_config.status_codes_override == ["599"]


def test_structured_generation_uses_schema(tmp_path: Path) -> None:
    response = SimpleNamespace(output_text='{"title":"План","count":3}', usage=None)
    client = SimpleNamespace(interactions=FakeInteractions([response]), files=FakeFiles())
    gateway = GeminiGateway(settings(tmp_path), client=client)
    result = gateway.generate_structured(prompt="build", schema=Payload, role="blueprint")
    assert result == Payload(title="План", count=3)
    sent = client.interactions.calls[0]
    assert sent["response_format"]["mime_type"] == "application/json"
    assert sent["response_format"]["schema"]["required"] == ["title", "count"]
    assert sent["generation_config"] == {"thinking_level": "medium"}


def test_usage_preserves_request_id_when_provider_omits_interaction_id(
    tmp_path: Path,
) -> None:
    usage = []
    response = SimpleNamespace(
        output_text="OK",
        usage=SimpleNamespace(total_input_tokens=1, total_output_tokens=1),
        status="completed",
        id="",
    )
    interactions = FakeRawInteractions(
        response,
        headers={"x-goog-request-id": "request-from-header"},
    )
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    gateway = GeminiGateway(settings(tmp_path), client=client, usage_sink=usage.append)

    gateway.health_check()

    assert usage[0].metadata["interaction_id"] == ""
    assert usage[0].metadata["request_id"] == "request-from-header"
    assert usage[0].metadata["request_id_source"] == "provider"
    assert interactions.response is response


def test_usage_keeps_local_request_id_separate_when_provider_returns_no_ids(
    tmp_path: Path,
) -> None:
    usage = []
    response = SimpleNamespace(
        output_text="OK",
        usage=SimpleNamespace(total_input_tokens=1, total_output_tokens=1),
        status="completed",
        id="",
    )
    interactions = FakeRawInteractions(response)
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    gateway = GeminiGateway(settings(tmp_path), client=client, usage_sink=usage.append)

    gateway.health_check()

    client_request_id = usage[0].metadata["client_request_id"]
    assert len(client_request_id) == 32
    assert usage[0].metadata["request_id"] == ""
    assert usage[0].metadata["request_id_source"] == "unavailable"
    assert usage[0].metadata["provider_request_id"] == ""
    assert "labels" not in interactions.calls[0]


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


def test_production_policy_is_pinned_to_stage_three_models() -> None:
    models = ModelPolicy()
    thinking = ThinkingPolicy()
    assert models.classification == models.extraction == "gemini-3.5-flash-lite"
    assert {
        models.requirements,
        models.blueprint,
        models.research,
        models.writer,
        models.critic,
        models.final_review,
        models.visual_qa,
    } == {"gemini-3.7-flash"}
    assert models.image == "gemini-3.1-flash-image"
    assert models.embedding == "gemini-embedding-2"
    assert thinking.classification == thinking.extraction == "minimal"
    assert thinking.critic == thinking.final_review == "high"


def test_model_not_found_is_a_configuration_error(tmp_path: Path) -> None:
    interactions = FakeInteractions([RuntimeError("404 model not found")])
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    gateway = GeminiGateway(settings(tmp_path), client=client, sleep=lambda _: None)
    with pytest.raises(GeminiConfigurationError):
        gateway.health_check()
    assert len(interactions.calls) == 1


def test_retry_after_is_honoured_for_429(tmp_path: Path) -> None:
    class Throttled(RuntimeError):
        status_code = 429
        response = SimpleNamespace(headers={"Retry-After": "7"})

    response = SimpleNamespace(output_text="OK", usage=None, status="completed", id="v1_ok")
    interactions = FakeInteractions([Throttled("rate limit"), response])
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    delays: list[float] = []
    configured = settings(tmp_path).model_copy(
        update={
            "retry_policy": RetryPolicy(
                max_attempts=2,
                base_delay_seconds=0,
                maximum_delay_seconds=10,
                jitter_seconds=0,
            )
        }
    )
    GeminiGateway(configured, client=client, sleep=delays.append).health_check()
    assert delays == [7]


def test_retry_after_from_gemini_error_message_is_honoured(tmp_path: Path) -> None:
    class Throttled(RuntimeError):
        status_code = 429

    response = SimpleNamespace(output_text="OK", usage=None, status="completed", id="v1_ok")
    interactions = FakeInteractions(
        [Throttled("Quota exceeded. Please retry in 40.717499257s."), response]
    )
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    delays: list[float] = []
    configured = settings(tmp_path).model_copy(
        update={
            "retry_policy": RetryPolicy(
                max_attempts=2,
                base_delay_seconds=0,
                maximum_delay_seconds=60,
                jitter_seconds=0,
            )
        }
    )
    GeminiGateway(configured, client=client, sleep=delays.append).health_check()
    assert delays == [40.717499257]


def test_authentication_failure_marks_run_as_waiting_input() -> None:
    assert GeminiAuthenticationError.waiting_input is True


@pytest.mark.parametrize("status_code", [408, 500, 502, 503, 504])
def test_transient_provider_failures_are_retried(tmp_path: Path, status_code: int) -> None:
    class Transient(RuntimeError):
        pass

    error = Transient("transient provider failure")
    error.status_code = status_code
    response = SimpleNamespace(output_text="OK", usage=None, status="completed", id="v1_ok")
    interactions = FakeInteractions([error, response])
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    GeminiGateway(settings(tmp_path), client=client, sleep=lambda _: None).health_check()
    assert len(interactions.calls) == 2


def test_transport_timeout_is_retried(tmp_path: Path) -> None:
    response = SimpleNamespace(output_text="OK", usage=None, status="completed", id="v1_ok")
    interactions = FakeInteractions([TimeoutError("socket timeout"), response])
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    GeminiGateway(settings(tmp_path), client=client, sleep=lambda _: None).health_check()
    assert len(interactions.calls) == 2


def test_malformed_structured_json_is_repaired_with_schema_feedback(tmp_path: Path) -> None:
    malformed = SimpleNamespace(
        output_text='{"title":"Incomplete"}', usage=None, status="completed", id="v1_bad"
    )
    repaired = SimpleNamespace(
        output_text='{"title":"Complete","count":2}',
        usage=None,
        status="completed",
        id="v1_good",
    )
    interactions = FakeInteractions([malformed, repaired])
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    result = GeminiGateway(settings(tmp_path), client=client).generate_structured(
        prompt="build", schema=Payload, role="blueprint"
    )
    assert result == Payload(title="Complete", count=2)
    assert len(interactions.calls) == 2
    repair_input = interactions.calls[1]["input"][-1]["text"]
    assert "did not validate" in repair_input


def test_safety_block_is_distinct_and_not_retried(tmp_path: Path) -> None:
    blocked = SimpleNamespace(
        output_text="", usage=None, status="blocked", errors=["safety policy"]
    )
    interactions = FakeInteractions([blocked])
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    gateway = GeminiGateway(settings(tmp_path), client=client)
    with pytest.raises(GeminiSafetyError):
        gateway.generate_text(prompt="unsafe", role="writer")
    assert len(interactions.calls) == 1


def test_empty_provider_output_fails_closed(tmp_path: Path) -> None:
    empty = SimpleNamespace(output_text="  ", usage=None, status="completed")
    interactions = FakeInteractions([empty])
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    gateway = GeminiGateway(settings(tmp_path), client=client)
    with pytest.raises(GeminiGatewayError, match="empty text"):
        gateway.generate_text(prompt="answer", role="writer")
