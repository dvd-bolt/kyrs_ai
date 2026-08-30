import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, SecretStr, ValidationError

from papercraft.application.schemas import ResearchPlan
from papercraft.config import AppSettings, ModelPolicy, RetryPolicy, ThinkingPolicy
from papercraft.infrastructure.gemini import (
    FakeGeminiGateway,
    GeminiAuthenticationError,
    GeminiConfigurationError,
    GeminiGateway,
    GeminiGatewayError,
    GeminiRequestCancelled,
    GeminiSafetyError,
    GeminiUnavailableError,
    RemoteFile,
    UsageRecord,
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


class FakeStoredInteractions:
    """Small SDK double for the stored background-interaction lifecycle."""

    def __init__(
        self,
        *,
        create_response: object,
        cancel_response: object,
        get_responses: list[object],
        delete_response: object = None,
    ) -> None:
        self.create_response = create_response
        self.cancel_response = cancel_response
        self.get_responses = list(get_responses)
        self.delete_response = delete_response
        self.calls: list[dict[str, object]] = []

    @staticmethod
    def _response(value: object) -> object:
        if isinstance(value, Exception):
            raise value
        return value

    def create(self, **kwargs: object) -> object:
        self.calls.append({"operation": "create", **kwargs})
        return self._response(self.create_response)

    def cancel(self, *, id: str, **kwargs: object) -> object:
        self.calls.append({"operation": "cancel", "id": id, **kwargs})
        return self._response(self.cancel_response)

    def get(self, *, id: str, **kwargs: object) -> object:
        self.calls.append({"operation": "get", "id": id, **kwargs})
        return self._response(self.get_responses.pop(0))

    def delete(self, *, id: str, **kwargs: object) -> object:
        self.calls.append({"operation": "delete", "id": id, **kwargs})
        return self._response(self.delete_response)


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
    assert sent["input"] == "build"
    assert sent["response_format"] == {
        "type": "text",
        "mime_type": "application/json",
        "schema": Payload.model_json_schema(),
    }
    assert "response_mime_type" not in sent
    assert sent["generation_config"] == {"thinking_level": "medium"}


def test_structured_generation_uses_multimodal_input_only_when_files_are_present(
    tmp_path: Path,
) -> None:
    response = SimpleNamespace(output_text='{"title":"Vision","count":1}', usage=None)
    client = SimpleNamespace(interactions=FakeInteractions([response]), files=FakeFiles())
    gateway = GeminiGateway(settings(tmp_path), client=client)

    result = gateway.generate_structured(
        prompt="Read the attached material.",
        schema=Payload,
        role="extraction",
        files=[
            RemoteFile(name="files/image", uri="fake://image", mime_type="image/png"),
            RemoteFile(name="files/document", uri="fake://document", mime_type="application/pdf"),
        ],
    )

    assert result == Payload(title="Vision", count=1)
    sent = client.interactions.calls[0]
    assert sent["input"] == [
        {"type": "text", "text": "Read the attached material."},
        {"type": "image", "uri": "fake://image", "mime_type": "image/png"},
        {
            "type": "document",
            "uri": "fake://document",
            "mime_type": "application/pdf",
        },
    ]
    assert sent["response_format"]["type"] == "text"
    assert sent["response_format"]["mime_type"] == "application/json"
    assert "response_mime_type" not in sent


def test_research_plan_schema_is_deterministic_and_retains_claim_limit() -> None:
    first = ResearchPlan.model_json_schema()
    second = ResearchPlan.model_json_schema()
    canonical_first = json.dumps(first, sort_keys=True, separators=(",", ":"))
    canonical_second = json.dumps(second, sort_keys=True, separators=(",", ":"))

    assert canonical_first == canonical_second
    assert hashlib.sha256(canonical_first.encode()).hexdigest() == hashlib.sha256(
        canonical_second.encode()
    ).hexdigest()
    assert first["properties"]["claims"]["maxItems"] == 80
    with pytest.raises(ValidationError):
        ResearchPlan.model_validate(
            {
                "claims": [
                    {"text": f"Claim {index}", "search_query": f"query {index}"}
                    for index in range(81)
                ]
            }
        )


def test_structured_generation_omits_only_max_items_from_provider_schema(tmp_path: Path) -> None:
    response = SimpleNamespace(output_text='{"claims":[]}', usage=None, status="completed")
    client = SimpleNamespace(interactions=FakeInteractions([response]), files=FakeFiles())

    result = GeminiGateway(settings(tmp_path), client=client).generate_structured(
        prompt="Build a research plan.", schema=ResearchPlan, role="research"
    )

    assert result == ResearchPlan()
    sent_schema = client.interactions.calls[0]["response_format"]["schema"]
    assert "maxItems" not in json.dumps(sent_schema, sort_keys=True)
    assert ResearchPlan.model_json_schema()["properties"]["claims"]["maxItems"] == 80


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


def test_fake_gateway_tracks_stored_background_interaction_lifecycle() -> None:
    fake = FakeGeminiGateway()
    fake.enqueue("start_background_text", "int_fake_123")

    interaction_id = fake.start_background_text(prompt="safe fixture", role="research")

    assert interaction_id == "int_fake_123"
    assert fake.get_interaction_status(interaction_id) == "in_progress"
    assert fake.cancel_interaction(interaction_id) == "cancelled"
    assert fake.get_interaction_status(interaction_id) == "cancelled"
    fake.delete_interaction(interaction_id)
    assert fake.get_interaction_status(interaction_id) is None
    assert fake.deleted_interactions == [interaction_id]


def test_stored_background_interaction_lifecycle_is_safe_and_idempotent(tmp_path: Path) -> None:
    class NotFound(RuntimeError):
        status_code = 404

    interaction_id = "int_background_123"
    interactions = FakeStoredInteractions(
        create_response=SimpleNamespace(id=interaction_id, status="in_progress", usage=None),
        cancel_response=SimpleNamespace(status="in_progress"),
        get_responses=[
            SimpleNamespace(status="in_progress"),
            SimpleNamespace(status="cancelled"),
            NotFound("already deleted"),
        ],
        delete_response=NotFound("already deleted"),
    )
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    gateway = GeminiGateway(settings(tmp_path), client=client, sleep=lambda _: None)

    assert gateway.start_background_text(prompt="safe fixture", role="research") == interaction_id
    assert gateway.cancel_interaction(interaction_id) == "in_progress"
    assert gateway.get_interaction_status(interaction_id) == "in_progress"
    assert gateway.get_interaction_status(interaction_id) == "cancelled"
    gateway.delete_interaction(interaction_id)
    assert gateway.get_interaction_status(interaction_id) is None

    assert [call["operation"] for call in interactions.calls] == [
        "create",
        "cancel",
        "get",
        "get",
        "delete",
        "get",
    ]
    assert interactions.calls[0]["store"] is True
    assert interactions.calls[0]["background"] is True
    for call in interactions.calls[1:]:
        assert call["id"] == interaction_id
        assert call["extra_headers"] == {"Api-Revision": "2026-05-20"}


def test_remote_cleanup_and_background_lifecycle_bypass_a_spent_cost_limit(
    tmp_path: Path,
) -> None:
    class SpentBudget:
        def __call__(self, _record: UsageRecord) -> None:
            raise AssertionError("Cleanup must not emit paid usage")

        @staticmethod
        def limit_reached() -> bool:
            return True

    class TrackingFiles(FakeFiles):
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete(self, *, name: str) -> None:
            self.deleted.append(name)

    interaction_id = "int_cleanup_123"
    interactions = FakeStoredInteractions(
        create_response=SimpleNamespace(id=interaction_id, status="in_progress", usage=None),
        cancel_response=SimpleNamespace(status="cancelled"),
        get_responses=[SimpleNamespace(status="cancelled")],
    )
    files = TrackingFiles()
    gateway = GeminiGateway(
        settings(tmp_path),
        client=SimpleNamespace(interactions=interactions, files=files),
        usage_sink=SpentBudget(),
        sleep=lambda _: None,
    )

    with gateway.cancellation_scope(lambda: True):
        gateway.delete_file("files/example")
        assert gateway.cancel_interaction(interaction_id) == "cancelled"
        assert gateway.get_interaction_status(interaction_id) == "cancelled"
        gateway.delete_interaction(interaction_id)
        with pytest.raises(GeminiRequestCancelled):
            gateway.start_background_text(prompt="cancelled", role="research")

    assert files.deleted == ["files/example"]
    assert [call["operation"] for call in interactions.calls] == ["cancel", "get", "delete"]

    # The exception is limited to idempotent control paths. Starting a new
    # stored interaction remains a paid, capped provider request.
    with pytest.raises(GeminiGatewayError, match="cost limit"):
        gateway.start_background_text(prompt="blocked", role="research")
    assert [call["operation"] for call in interactions.calls] == ["cancel", "get", "delete"]


@pytest.mark.parametrize(
    "operation",
    ["cancel_interaction", "get_interaction_status", "delete_interaction"],
)
def test_stored_background_interaction_operations_reject_unexpected_ids(
    tmp_path: Path,
    operation: str,
) -> None:
    interactions = FakeStoredInteractions(
        create_response=SimpleNamespace(id="int_unused", status="in_progress", usage=None),
        cancel_response=SimpleNamespace(status="cancelled"),
        get_responses=[],
    )
    gateway = GeminiGateway(
        settings(tmp_path),
        client=SimpleNamespace(interactions=interactions, files=FakeFiles()),
        sleep=lambda _: None,
    )

    with pytest.raises(ValueError, match="Unexpected Gemini interaction ID"):
        getattr(gateway, operation)("../not-an-interaction")

    assert interactions.calls == []


def test_stored_background_creation_rejects_an_unexpected_provider_id(tmp_path: Path) -> None:
    interactions = FakeStoredInteractions(
        create_response=SimpleNamespace(id="../unexpected", status="in_progress", usage=None),
        cancel_response=SimpleNamespace(status="cancelled"),
        get_responses=[],
    )
    gateway = GeminiGateway(
        settings(tmp_path),
        client=SimpleNamespace(interactions=interactions, files=FakeFiles()),
        sleep=lambda _: None,
    )

    with pytest.raises(ValueError, match="Unexpected Gemini interaction ID"):
        gateway.start_background_text(prompt="safe fixture", role="research")

    assert [call["operation"] for call in interactions.calls] == ["create"]


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


def test_embeddings_record_conservative_usage_and_stop_after_the_cost_cap(
    tmp_path: Path,
) -> None:
    class CostCapSink:
        def __init__(self) -> None:
            self.records: list[UsageRecord] = []
            self.reached = False

        def __call__(self, record: UsageRecord) -> None:
            self.records.append(record)
            self.reached = True

        def limit_reached(self) -> bool:
            return self.reached

    class FakeModels:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def embed_content(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.0] * 768)],
                usage=None,
            )

    models = FakeModels()
    cost_cap = CostCapSink()
    gateway = GeminiGateway(
        settings(tmp_path),
        client=SimpleNamespace(models=models),
        usage_sink=cost_cap,
    )
    source_text = "проверяемое утверждение"

    assert gateway.embed_texts([source_text]) == [[0.0] * 768]
    assert len(cost_cap.records) == 1
    record = cost_cap.records[0]
    assert record.operation == "embed_texts"
    assert record.model == "gemini-embedding-2"
    assert record.input_tokens == len(source_text.encode("utf-8"))
    assert record.estimated_cost > 0
    assert record.metadata["input_tokens_source"] == "conservative_estimate"

    with pytest.raises(GeminiGatewayError, match="cost limit"):
        gateway.embed_texts(["must not reach the provider"])
    assert len(models.calls) == 1


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


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, GeminiAuthenticationError),
        (404, GeminiConfigurationError),
        (418, GeminiGatewayError),
    ],
)
def test_non_retryable_provider_errors_do_not_expose_raw_request_data(
    tmp_path: Path,
    status_code: int,
    error_type: type[GeminiGatewayError],
) -> None:
    private_prompt = "PRIVATE_USER_PROMPT_MUST_NOT_APPEAR"
    private_key = "sentinel-provider-key"

    class PrivateProviderError(RuntimeError):
        def __init__(self) -> None:
            self.status_code = status_code
            self.response = SimpleNamespace(
                headers={
                    "Authorization": "Bearer sentinel-bearer-value",
                    "x-goog-request-id": "safe-provider-request-id",
                }
            )
            super().__init__(
                f"provider rejected {private_prompt}; Authorization: Bearer sentinel-bearer-value; "
                f"https://provider.invalid/request?key={private_key}"
            )

    interactions = FakeInteractions([PrivateProviderError()])
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())

    with pytest.raises(error_type) as raised:
        GeminiGateway(settings(tmp_path), client=client, sleep=lambda _: None).health_check()

    message = str(raised.value)
    assert "safe-provider-request-id" in message
    assert private_prompt not in message
    assert private_key not in message
    assert "sentinel-bearer-value" not in message


def test_429_retry_exhaustion_does_not_expose_raw_provider_error_text(tmp_path: Path) -> None:
    private_prompt = "PRIVATE_USER_PROMPT_MUST_NOT_APPEAR"
    private_key = "sentinel-provider-key"

    class PrivateTransientError(RuntimeError):
        status_code = 429

        def __init__(self) -> None:
            super().__init__(
                f"provider transient failure for {private_prompt}; "
                f"Authorization: Bearer sentinel-bearer-value; key={private_key}"
            )

    interactions = FakeInteractions([PrivateTransientError(), PrivateTransientError()])
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())

    with pytest.raises(GeminiUnavailableError) as raised:
        GeminiGateway(settings(tmp_path), client=client, sleep=lambda _: None).health_check()

    message = str(raised.value)
    assert "failed after 2 attempts" in message
    assert private_prompt not in message
    assert private_key not in message
    assert "sentinel-bearer-value" not in message
    assert len(interactions.calls) == 2


@pytest.mark.parametrize("status_code", [408, 409, 500, 502, 503, 504])
def test_ambiguous_provider_failures_are_not_retried(tmp_path: Path, status_code: int) -> None:
    class Transient(RuntimeError):
        pass

    error = Transient("transient provider failure")
    error.status_code = status_code
    response = SimpleNamespace(output_text="OK", usage=None, status="completed", id="v1_ok")
    interactions = FakeInteractions([error, response])
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    with pytest.raises(GeminiUnavailableError, match="could not be safely retried"):
        GeminiGateway(settings(tmp_path), client=client, sleep=lambda _: None).health_check()
    assert len(interactions.calls) == 1


def test_transport_timeout_is_not_retried(tmp_path: Path) -> None:
    response = SimpleNamespace(output_text="OK", usage=None, status="completed", id="v1_ok")
    interactions = FakeInteractions([TimeoutError("socket timeout"), response])
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    with pytest.raises(GeminiUnavailableError, match="could not be safely retried"):
        GeminiGateway(settings(tmp_path), client=client, sleep=lambda _: None).health_check()
    assert len(interactions.calls) == 1


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
    usage = []
    result = GeminiGateway(settings(tmp_path), client=client, usage_sink=usage.append).generate_structured(
        prompt="build", schema=Payload, role="blueprint"
    )
    assert result == Payload(title="Complete", count=2)
    assert len(interactions.calls) == 2
    assert interactions.calls[0]["input"] == "build"
    repair_input = interactions.calls[1]["input"]
    assert isinstance(repair_input, str)
    assert repair_input.startswith("build")
    assert "did not validate" in repair_input
    assert '"title":"Incomplete"' in repair_input
    assert len(usage) == 2


def test_multimodal_structured_repair_preserves_first_request_and_appends_feedback(
    tmp_path: Path,
) -> None:
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
        prompt="Read this image and return JSON.",
        schema=Payload,
        role="extraction",
        files=[RemoteFile(name="files/image", uri="fake://image", mime_type="image/png")],
    )

    assert result == Payload(title="Complete", count=2)
    assert len(interactions.calls) == 2
    first_input = interactions.calls[0]["input"]
    repair_input = interactions.calls[1]["input"]
    assert first_input == [
        {"type": "text", "text": "Read this image and return JSON."},
        {"type": "image", "uri": "fake://image", "mime_type": "image/png"},
    ]
    assert repair_input[:2] == first_input
    assert repair_input[-1]["type"] == "text"
    assert "did not validate" in repair_input[-1]["text"]


def test_structured_generation_stops_after_three_invalid_outputs(tmp_path: Path) -> None:
    invalid = [
        SimpleNamespace(output_text='{"title":"Incomplete"}', usage=None, status="completed")
        for _ in range(3)
    ]
    interactions = FakeInteractions(invalid)
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())

    with pytest.raises(GeminiGatewayError, match="after three attempts"):
        GeminiGateway(settings(tmp_path), client=client).generate_structured(
            prompt="build", schema=Payload, role="blueprint"
        )

    assert len(interactions.calls) == 3


def test_structured_http_400_is_not_retried_and_exposes_only_safe_diagnostics(
    tmp_path: Path,
) -> None:
    class InvalidStructuredRequest(RuntimeError):
        status_code = 400

        def __init__(self) -> None:
            self.response = SimpleNamespace(
                headers={"x-goog-request-id": "provider-request-for-test"}
            )
            self.details = {
                "error": {
                    "details": [
                        {"fieldViolations": [{"description": "unsupported schema field"}]}
                    ]
                }
            }
            self.message = (
                "invalid schema; Authorization: Bearer sentinel-bearer-value; "
                "https://provider.invalid/request?key=sentinel-provider-key"
            )
            super().__init__(self.message)

    interactions = FakeInteractions([InvalidStructuredRequest()])
    client = SimpleNamespace(interactions=interactions, files=FakeFiles())
    delays: list[float] = []
    private_prompt = "PRIVATE_USER_PROMPT_MUST_NOT_APPEAR"

    with pytest.raises(GeminiGatewayError) as raised:
        GeminiGateway(settings(tmp_path), client=client, sleep=delays.append).generate_structured(
            prompt=private_prompt,
            schema=Payload,
            role="blueprint",
        )

    assert len(interactions.calls) == 1
    assert delays == []
    message = str(raised.value)
    assert "structured generation (blueprint) is invalid" in message
    assert "provider-request-for-test" in message
    assert "unsupported schema field" in message
    assert private_prompt not in message
    assert "sentinel-bearer-value" not in message
    assert "sentinel-provider-key" not in message

    metadata = json.loads(message.rsplit("request_metadata=", maxsplit=1)[1])
    assert metadata["model"] == "gemini-3.7-flash"
    assert metadata["role"] == "blueprint"
    assert metadata["thinking_level"] == "medium"
    assert metadata["schema_name"] == "Payload"
    assert metadata["input_shape"] == "text"
    assert metadata["file_count"] == 0
    assert metadata["has_system_instruction"] is False
    assert metadata["schema_bytes"] > 0
    assert isinstance(metadata["sdk_version"], str) and metadata["sdk_version"]
    assert len(metadata["schema_sha256"]) == 64
    assert all(character in "0123456789abcdef" for character in metadata["schema_sha256"])


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
