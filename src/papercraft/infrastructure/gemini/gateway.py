from __future__ import annotations

import base64
import io
import mimetypes
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, TypeVar, cast
from uuid import uuid4

import httpx
from pydantic import BaseModel, ValidationError

from papercraft.config import AppSettings

from .secrets import CredentialSecretStore, SecretStore

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class GeminiGatewayError(RuntimeError):
    """Base error raised at the provider boundary."""


class GeminiAuthenticationError(GeminiGatewayError):
    waiting_input = True


class GeminiConfigurationError(GeminiGatewayError):
    """A pinned model or provider feature is not available."""


class GeminiSafetyError(GeminiGatewayError):
    """The provider rejected content under its safety policy."""


class GeminiUnavailableError(GeminiGatewayError):
    pass


class GeminiStructuredOutputError(GeminiGatewayError):
    pass


@dataclass(frozen=True, slots=True)
class _InteractionResponse:
    payload: Any
    request_id: str | None = None
    client_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class UsageRecord:
    operation: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: Decimal = Decimal("0")
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RemoteFile:
    name: str
    uri: str
    mime_type: str | None = None


@dataclass(frozen=True, slots=True)
class GroundedResult:
    text: str
    model: str
    annotations: list[dict[str, Any]] = field(default_factory=list)
    raw_steps: list[dict[str, Any]] = field(default_factory=list)


class GeminiGateway:
    """Production Gemini adapter using the current Interactions and Files APIs.

    There is deliberately no implicit mock mode.  Tests inject
    :class:`FakeGeminiGateway`; production fails clearly when credentials are
    unavailable.
    """

    def __init__(
        self,
        settings: AppSettings,
        *,
        secret_store: SecretStore | None = None,
        client: Any | None = None,
        usage_sink: Callable[[UsageRecord], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.secret_store = secret_store or CredentialSecretStore()
        self.usage_sink = usage_sink
        self._sleep = sleep

        key = (
            settings.gemini_api_key.get_secret_value()
            if settings.gemini_api_key is not None
            else self.secret_store.get_api_key()
        )
        if client is None:
            if not key:
                raise GeminiAuthenticationError(
                    "Gemini API key is not configured. Add it in Settings before starting autopilot."
                )
            try:
                from google import genai
                from google.genai import types

                # The application owns HTTP retry classification, bounded
                # attempts, provider Retry-After handling and jitter. Override
                # the SDK's retryable HTTP codes with an unused sentinel so a
                # single logical 429/5xx attempt cannot fan out into enough
                # requests to keep a quota window permanently hot. The SDK may
                # still make its one documented transport recovery attempt.
                client = genai.Client(
                    api_key=key,
                    http_options=types.HttpOptions(
                        retry_options=types.HttpRetryOptions(
                            attempts=1,
                            http_status_codes=[599],
                        )
                    ),
                )
            except Exception as exc:  # pragma: no cover - SDK environment dependent
                raise GeminiAuthenticationError(f"Unable to initialise Gemini: {exc}") from exc
        self.client = client

    def _model(self, role: str) -> str:
        policy = self.settings.model_policy
        try:
            value = getattr(policy, role)
        except AttributeError as exc:
            raise ValueError(f"Unknown Gemini model role: {role}") from exc
        if not isinstance(value, str):
            raise ValueError(f"Model role is not textual: {role}")
        return value

    def _thinking_level(self, role: str) -> str:
        try:
            value = getattr(self.settings.thinking_policy, role)
        except AttributeError as exc:
            raise ValueError(f"Unknown Gemini thinking role: {role}") from exc
        if not isinstance(value, str):
            raise ValueError(f"Thinking level is not textual: {role}")
        return value

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        for attribute in ("status_code", "code"):
            value = getattr(exc, attribute, None)
            if isinstance(value, int):
                return value
            if callable(value):
                try:
                    called = value()
                    if isinstance(called, int):
                        return called
                except Exception:
                    pass
        text = str(exc).lower()
        for candidate in (400, 401, 403, 404, 408, 409, 429, 500, 502, 503, 504):
            if re.search(rf"(?<!\d){candidate}(?!\d)", text):
                return candidate
        return None

    @staticmethod
    def _is_transport_error(exc: Exception) -> bool:
        """Return whether an exception represents a request that is safe to retry.

        Arbitrary exceptions used to be retried as though they were network
        failures.  That can duplicate paid POST requests when the defect is a
        local ``TypeError``/``ValueError`` and can hide SDK contract changes.
        """

        return isinstance(exc, (TimeoutError, ConnectionError, httpx.TransportError))

    @staticmethod
    def _retry_after_seconds(exc: Exception) -> float | None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) or getattr(exc, "headers", None)
        raw: Any = None
        if headers is not None:
            try:
                raw = headers.get("Retry-After") or headers.get("retry-after")
            except Exception:
                raw = None
        if raw is not None:
            value = str(raw).strip()
            try:
                return max(0.0, float(value))
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(value)
                except (TypeError, ValueError, OverflowError):
                    parsed = None
                if parsed is not None:
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=UTC)
                    return max(0.0, (parsed - datetime.now(UTC)).total_seconds())

        # The public Gemini endpoint currently puts its precise delay in the
        # structured error message and may omit the standard Retry-After
        # header. Honour that provider instruction instead of exhausting the
        # bounded retry loop before the quota window reopens.
        match = re.search(
            r"(?i)\bretry\s+in\s+([0-9]+(?:\.[0-9]+)?)\s*s\b",
            str(exc),
        )
        return max(0.0, float(match.group(1))) if match else None

    def _call(
        self,
        operation: str,
        function: Callable[[], Any],
        *,
        not_found_ok: bool = False,
    ) -> Any:
        retry = self.settings.retry_policy
        last_error: Exception | None = None
        for attempt in range(1, retry.max_attempts + 1):
            try:
                return function()
            except Exception as exc:
                last_error = exc
                status = self._status_code(exc)
                if status == 404 and not_found_ok:
                    return None
                if status in {400, 401, 403, 404}:
                    if status in {401, 403}:
                        raise GeminiAuthenticationError(f"{operation} was rejected: {exc}") from exc
                    if status == 404:
                        raise GeminiConfigurationError(
                            f"{operation} references an unavailable model or endpoint: {exc}"
                        ) from exc
                    raise GeminiGatewayError(f"{operation} is invalid: {exc}") from exc
                retryable = status in {408, 409, 429, 500, 502, 503, 504} or (
                    status is None and self._is_transport_error(exc)
                )
                if not retryable:
                    raise GeminiGatewayError(f"{operation} failed: {exc}") from exc
                if not retryable or attempt >= retry.max_attempts:
                    break
                delay = min(
                    retry.maximum_delay_seconds,
                    retry.base_delay_seconds * (2 ** (attempt - 1)),
                ) + random.uniform(0, retry.jitter_seconds)
                retry_after = self._retry_after_seconds(exc) if status == 429 else None
                if retry_after is not None:
                    delay = max(delay, min(retry.maximum_delay_seconds, retry_after))
                self._sleep(delay)
        raise GeminiUnavailableError(
            f"{operation} failed after {retry.max_attempts} attempts: {last_error}"
        ) from last_error

    def _create_interaction(self, **body: Any) -> _InteractionResponse:
        client_request_id = uuid4().hex
        interactions = self.client.interactions
        raw_api = getattr(interactions, "with_raw_response", None)
        raw_create = getattr(raw_api, "create", None)
        if callable(raw_create):
            raw = raw_create(**body)
            parsed = raw.parse()
            request_id = self._provider_request_id(raw)
            return _InteractionResponse(
                payload=parsed,
                request_id=request_id,
                client_request_id=client_request_id,
            )
        response = interactions.create(**body)
        request_id = self._provider_request_id(response)
        return _InteractionResponse(
            payload=response,
            request_id=request_id,
            client_request_id=client_request_id,
        )

    @staticmethod
    def _provider_request_id(response: Any) -> str | None:
        """Extract a traceable provider request ID across SDK response modes."""

        candidates: list[Any] = [getattr(response, "request_id", None)]
        headers = getattr(response, "headers", None)
        if headers is None:
            http_response = getattr(response, "http_response", None)
            headers = getattr(http_response, "headers", None)
        if headers is not None:
            for name in (
                "x-request-id",
                "x-goog-request-id",
                "x-cloud-trace-context",
                "traceparent",
            ):
                try:
                    candidates.append(headers.get(name))
                except (AttributeError, TypeError):
                    break
        for candidate in candidates:
            cleaned = str(candidate or "").strip()
            if cleaned:
                return cleaned
        return None

    @staticmethod
    def _payload(response: Any) -> Any:
        return response.payload if isinstance(response, _InteractionResponse) else response

    def _usage(self, response: Any, operation: str, model: str) -> UsageRecord:
        payload = self._payload(response)
        provider_request_id = (
            response.request_id if isinstance(response, _InteractionResponse) else None
        )
        client_request_id = (
            response.client_request_id if isinstance(response, _InteractionResponse) else None
        )
        request_id = provider_request_id or ""
        usage = getattr(payload, "usage", None)
        input_tokens = int(
            getattr(usage, "total_input_tokens", None)
            or getattr(usage, "input_tokens", 0)
            or 0
        )
        output_tokens = int(
            getattr(usage, "total_output_tokens", None)
            or getattr(usage, "output_tokens", 0)
            or 0
        )
        thought_tokens = int(getattr(usage, "total_thought_tokens", 0) or 0)
        tool_use_tokens = int(getattr(usage, "total_tool_use_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", 0) or input_tokens + output_tokens)
        price = self.settings.pricing_policy.models.get(model)
        estimated = Decimal("0")
        if price is not None:
            estimated = (
                Decimal(input_tokens) * price.input_per_million
                + Decimal(output_tokens + thought_tokens) * price.output_per_million
            ) / Decimal(1_000_000)
        if operation == "generate_image":
            estimated += self.settings.pricing_policy.image_2k_estimate
        search_queries = 0
        if operation == "search_grounded":
            for item in getattr(usage, "grounding_tool_count", None) or []:
                item_type = (
                    item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
                )
                if str(item_type or "") == "google_search":
                    raw_count = (
                        item.get("count", 0)
                        if isinstance(item, dict)
                        else getattr(item, "count", 0)
                    )
                    search_queries += max(0, int(raw_count or 0))
            # Search usage may be absent in mocked, older, or partially failed
            # responses. Charging one query is the safe estimate for a request
            # that explicitly enabled Google Search.
            search_queries = max(1, search_queries)
            estimated += Decimal(search_queries) * self.settings.pricing_policy.search_query_estimate
        return UsageRecord(
            operation=operation,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost=estimated,
            metadata={
                "thought_tokens": thought_tokens,
                "tool_use_tokens": tool_use_tokens,
                "search_queries": search_queries,
                "pricing_missing": price is None,
                "interaction_id": str(getattr(payload, "id", "") or ""),
                "request_id": request_id,
                "request_id_source": "provider" if provider_request_id else "unavailable",
                "provider_request_id": provider_request_id or "",
                "client_request_id": client_request_id or "",
                "status": str(getattr(payload, "status", "") or ""),
            },
        )

    def _record(self, response: Any, operation: str, model: str) -> None:
        if self.usage_sink is not None:
            self.usage_sink(self._usage(response, operation, model))

    def _require_usable_response(self, response: Any, *, operation: str) -> Any:
        payload = self._payload(response)
        status = str(getattr(payload, "status", "") or "").casefold()
        errors = getattr(payload, "errors", None) or []
        diagnostic = " ".join(str(item) for item in errors)
        if "safety" in diagnostic.casefold() or "blocked" in status:
            raise GeminiSafetyError(f"{operation} was blocked by Gemini safety policy")
        if status in {"failed", "error", "cancelled", "canceled"}:
            raise GeminiGatewayError(f"{operation} returned provider status {status}")
        return payload

    def health_check(self) -> None:
        """Validate credentials and the pinned Gemini 3.7 production model."""

        role = "requirements"
        model = self._model(role)

        def invoke() -> Any:
            return self._create_interaction(
                model=model,
                input="Reply with OK.",
                store=False,
                generation_config={"thinking_level": self._thinking_level(role)},
                timeout=self.settings.request_timeout_seconds,
            )

        response = self._call("Gemini health check", invoke)
        self._record(response, "health_check", model)
        payload = self._require_usable_response(response, operation="Gemini health check")
        text = str(getattr(payload, "output_text", "") or "").strip()
        if not text:
            raise GeminiUnavailableError("Gemini health check returned an empty response")

    def generate_text(
        self,
        *,
        prompt: str,
        role: str,
        system_instruction: str | None = None,
    ) -> str:
        model = self._model(role)

        def invoke() -> Any:
            body: dict[str, Any] = {
                "model": model,
                "input": prompt,
                "store": False,
                "generation_config": {"thinking_level": self._thinking_level(role)},
                "timeout": self.settings.request_timeout_seconds,
            }
            if system_instruction:
                body["system_instruction"] = system_instruction
            return self._create_interaction(**body)

        response = self._call(f"text generation ({role})", invoke)
        self._record(response, "generate_text", model)
        payload = self._require_usable_response(response, operation=f"text generation ({role})")
        output = str(getattr(payload, "output_text", "") or "").strip()
        if not output:
            raise GeminiGatewayError("Gemini returned an empty text response")
        return output

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: type[SchemaT],
        role: str,
        system_instruction: str | None = None,
        files: list[str | RemoteFile] | None = None,
    ) -> SchemaT:
        model = self._model(role)
        interaction_input: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for remote in files or []:
            uri = remote.uri if isinstance(remote, RemoteFile) else remote
            mime_type = remote.mime_type if isinstance(remote, RemoteFile) else None
            media_type = "document"
            if mime_type:
                media_type = mime_type.split("/", 1)[0]
                if media_type not in {"image", "audio", "video"}:
                    media_type = "document"
            interaction_input.append(
                {"type": media_type, "uri": uri, "mime_type": mime_type or "application/octet-stream"}
            )

        def invoke() -> Any:
            body: dict[str, Any] = {
                "model": model,
                "input": interaction_input,
                "store": False,
                "response_format": {
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": schema.model_json_schema(),
                },
                "generation_config": {"thinking_level": self._thinking_level(role)},
                "timeout": self.settings.request_timeout_seconds,
            }
            if system_instruction:
                body["system_instruction"] = system_instruction
            return self._create_interaction(**body)

        last_error: ValidationError | None = None
        original_input = list(interaction_input)
        for structured_attempt in range(1, 4):
            response = self._call(f"structured generation ({role})", invoke)
            # Every completed provider response is billable, including an
            # invalid JSON response that needs a schema-repair interaction.
            self._record(response, "generate_structured", model)
            payload = self._require_usable_response(
                response, operation=f"structured generation ({role})"
            )
            raw = getattr(payload, "output_text", None)
            if not isinstance(raw, str) or not raw.strip():
                raise GeminiStructuredOutputError("Gemini returned no structured text")
            try:
                parsed = schema.model_validate_json(raw)
            except ValidationError as exc:
                last_error = exc
                if structured_attempt >= 3:
                    break
                interaction_input[:] = [
                    *original_input,
                    {
                        "type": "text",
                        "text": (
                            "Your previous JSON did not validate. Return a corrected complete JSON object only. "
                            f"Validation error: {str(exc)[:4000]}. Previous output: {raw[:12000]}"
                        ),
                    },
                ]
                continue
            return parsed
        raise GeminiStructuredOutputError(
            f"Gemini response failed schema validation after three attempts: {last_error}"
        ) from last_error

    def search_grounded(
        self,
        *,
        prompt: str,
        role: str = "research",
        system_instruction: str | None = None,
    ) -> GroundedResult:
        model = self._model(role)

        def invoke() -> Any:
            body: dict[str, Any] = {
                "model": model,
                "input": prompt,
                "store": False,
                "tools": [{"type": "google_search"}],
                "generation_config": {"thinking_level": self._thinking_level(role)},
                "timeout": self.settings.request_timeout_seconds,
            }
            if system_instruction:
                body["system_instruction"] = system_instruction
            return self._create_interaction(**body)

        response = self._call("grounded Google Search", invoke)
        self._record(response, "search_grounded", model)
        payload = self._require_usable_response(response, operation="grounded Google Search")
        raw_steps: list[dict[str, Any]] = []
        annotations: list[dict[str, Any]] = []
        for step in getattr(payload, "steps", None) or []:
            if hasattr(step, "model_dump"):
                dumped = step.model_dump(mode="json", exclude_none=True)
            elif isinstance(step, dict):
                dumped = step
            else:
                continue
            raw_steps.append(dumped)
            candidate_annotations = dumped.get("annotations")
            if isinstance(candidate_annotations, list):
                annotations.extend(x for x in candidate_annotations if isinstance(x, dict))
            content = dumped.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    candidate_annotations = block.get("annotations")
                    if isinstance(candidate_annotations, list):
                        annotations.extend(
                            annotation
                            for annotation in candidate_annotations
                            if isinstance(annotation, dict)
                        )
        return GroundedResult(
            text=str(getattr(payload, "output_text", "") or ""),
            model=model,
            annotations=annotations,
            raw_steps=raw_steps,
        )

    def upload_file(self, path: Path) -> RemoteFile:
        resolved = path.expanduser().resolve(strict=True)
        if not resolved.is_file():
            raise ValueError(f"Only regular files can be uploaded: {resolved}")
        guessed_type, _ = mimetypes.guess_type(resolved.name)
        def upload() -> Any:
            if guessed_type:
                return self.client.files.upload(file=resolved, config={"mime_type": guessed_type})
            return self.client.files.upload(file=resolved)

        response = self._call("Gemini file upload", upload)
        name = str(getattr(response, "name", "") or "")
        uri = str(getattr(response, "uri", "") or "")
        if not name or not uri:
            raise GeminiGatewayError("Gemini file upload returned incomplete metadata")
        return RemoteFile(name=name, uri=uri, mime_type=getattr(response, "mime_type", None))

    def delete_file(self, name: str) -> None:
        if re.fullmatch(r"files/[A-Za-z0-9._~-]+", name) is None:
            raise ValueError("Unexpected Gemini file name")
        # DELETE is idempotent: a previous cleanup may have succeeded even if
        # the client did not receive its response.
        self._call(
            "Gemini file deletion",
            lambda: self.client.files.delete(name=name),
            not_found_ok=True,
        )

    def generate_image(self, *, prompt: str, destination: Path) -> Path:
        model = self._model("image")

        def invoke() -> Any:
            return self._create_interaction(
                model=model,
                input=prompt,
                store=False,
                response_format={
                    "type": "image",
                    "mime_type": "image/jpeg",
                    "image_size": "2K",
                },
                timeout=self.settings.request_timeout_seconds,
            )

        response = self._call("Gemini image generation", invoke)
        self._record(response, "generate_image", model)
        payload_response = self._require_usable_response(
            response, operation="Gemini image generation"
        )
        content = getattr(payload_response, "output_image", None)
        encoded = getattr(content, "data", None)
        if not encoded:
            raise GeminiGatewayError("Gemini image generation returned no image bytes")
        if isinstance(encoded, str):
            try:
                payload = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise GeminiGatewayError("Gemini returned invalid base64 image data") from exc
        else:
            payload = bytes(encoded)
        if len(payload) < 16:
            raise GeminiGatewayError("Gemini returned an empty image")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            if destination.suffix.casefold() == ".png":
                try:
                    from PIL import Image

                    with Image.open(io.BytesIO(payload)) as image:
                        image.load()
                        image.save(temporary, format="PNG")
                except Exception as exc:
                    raise GeminiGatewayError(
                        "Gemini returned image bytes that could not be converted to PNG"
                    ) from exc
            else:
                temporary.write_bytes(payload)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def embed_texts(
        self,
        texts: list[str],
        *,
        output_dimensionality: int = 768,
    ) -> list[list[float]]:
        if not texts or any(not item.strip() for item in texts):
            raise ValueError("Embedding input must contain non-empty text")
        if output_dimensionality not in {768, 1536, 3072}:
            raise ValueError("Embedding dimensionality must be 768, 1536 or 3072")
        model = self._model("embedding")

        rows: list[list[float]] = []
        # A flat list of strings is interpreted by the SDK as parts of one
        # multimodal Content object. Submit each logical record separately so
        # the returned vector count is deterministic across SDK releases.
        for text in texts:
            def invoke_embedding(text_value: str = text) -> Any:
                return self.client.models.embed_content(
                    model=model,
                    contents=cast(Any, text_value),
                    config={"output_dimensionality": output_dimensionality},
                )

            response = self._call(
                "Gemini embeddings",
                invoke_embedding,
            )
            embeddings = list(getattr(response, "embeddings", None) or [])
            if len(embeddings) != 1:
                raise GeminiGatewayError("Gemini returned the wrong number of embedding vectors")
            values = getattr(embeddings[0], "values", None)
            if values is None and isinstance(embeddings[0], dict):
                values = embeddings[0].get("values")
            if not isinstance(values, list) or len(values) != output_dimensionality:
                raise GeminiGatewayError("Gemini returned an invalid embedding vector")
            rows.append([float(value) for value in values])
        return rows

    def start_background_text(self, *, prompt: str, role: str) -> str:
        model = self._model(role)

        def invoke() -> Any:
            return self._create_interaction(
                model=model,
                input=prompt,
                store=True,
                background=True,
                generation_config={"thinking_level": self._thinking_level(role)},
                extra_headers={"Api-Revision": "2026-05-20"},
                timeout=self.settings.request_timeout_seconds,
            )

        response = self._call(f"background generation ({role})", invoke)
        self._record(response, "start_background_text", model)
        payload = self._require_usable_response(
            response, operation=f"background generation ({role})"
        )
        interaction_id = str(getattr(payload, "id", "") or "")
        if not interaction_id:
            raise GeminiGatewayError("Gemini background request returned no interaction ID")
        return interaction_id

    def cancel_interaction(self, interaction_id: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9._~-]+", interaction_id) is None:
            raise ValueError("Unexpected Gemini interaction ID")
        response = self._call(
            "Gemini interaction cancellation",
            lambda: self.client.interactions.cancel(
                id=interaction_id,
                extra_headers={"Api-Revision": "2026-05-20"},
                timeout=self.settings.request_timeout_seconds,
            ),
        )
        status = str(getattr(response, "status", "") or "").casefold()
        if not status:
            raise GeminiGatewayError("Gemini cancellation returned no status")
        return status
