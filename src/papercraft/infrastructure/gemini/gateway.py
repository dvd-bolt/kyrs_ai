from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import random
import re
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from math import ceil
from pathlib import Path
from threading import Condition, RLock
from typing import Any, TypeVar, cast
from uuid import uuid4

import httpx
from pydantic import BaseModel, ValidationError

from papercraft.config import AppSettings, PerformancePolicy

from .ports import validate_interaction_id
from .secrets import CredentialSecretStore, SecretStore

SchemaT = TypeVar("SchemaT", bound=BaseModel)


_MAX_PROVIDER_DIAGNOSTIC_CHARS = 2048
_MAX_PROVIDER_FIELD_VIOLATIONS = 4
_CANCELLATION_POLL_SECONDS = 0.1
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)(\bauthorization\b\s*[:=]\s*)(?:bearer\s+)?[^\s,;\"']+"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?i)\bbearer\s+[^\s,;\"']+"),
        "Bearer [REDACTED]",
    ),
    (
        re.compile(r"(?i)([?&](?:key|api[_-]?key|token|access_token)=)(?!\[REDACTED\])[^&#\s]+"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)(\b(?:api[_-]?key|key|access[_-]?token|token|secret)\b"
            r"\s*[:=]\s*[\"']?)(?!\[REDACTED\])[^,\s\"'}\]]+"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"\b(?:AIza|ghp_|github_pat_|sk-|AQ\.)[A-Za-z0-9._~+/=-]+\b"),
        "[REDACTED]",
    ),
)


class GeminiGatewayError(RuntimeError):
    """Base error raised at the provider boundary."""


class GeminiRequestCancelled(GeminiGatewayError):
    """A local lifecycle request stopped admission before a provider call."""


class _GeminiCostLimitError(GeminiGatewayError):
    """A local admission refusal, not a provider response to sanitize/retry."""


class GeminiAuthenticationError(GeminiGatewayError):
    waiting_input = True


class GeminiConfigurationError(GeminiGatewayError):
    """A pinned model or provider feature is not available."""


class GeminiSafetyError(GeminiGatewayError):
    """The provider rejected content under its safety policy."""


class GeminiUnavailableError(GeminiGatewayError):
    """A transient provider failure with an optional safe retry hint."""

    def __init__(
        self,
        message: str = "",
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class GeminiStructuredOutputError(GeminiGatewayError):
    pass


def _raise_if_cancelled(cancellation_requested: Callable[[], bool] | None) -> None:
    """Abort local admission without turning a pause into a provider failure."""

    if cancellation_requested is not None and cancellation_requested():
        raise GeminiRequestCancelled("Gemini request admission was cancelled")


@dataclass(frozen=True, slots=True)
class _InteractionResponse:
    payload: Any
    request_id: str | None = None
    client_request_id: str | None = None
    telemetry: _CallTelemetry | None = None


@dataclass(frozen=True, slots=True)
class _CallTelemetry:
    """Non-sensitive timing data for one logical provider operation."""

    duration_ms: int
    attempts: int
    retry_wait_ms: int


@dataclass(frozen=True, slots=True)
class ProviderRequestPermit:
    """A request slot acquired from :class:`ProviderRequestCoordinator`."""

    lane: str
    throttle_generation: int


@dataclass(frozen=True, slots=True)
class _CooldownTicket:
    """Identifies the specific cooldown that a retry has waited through."""

    requested_deadline: float


class ProviderRequestCoordinator:
    """Thread-safe, adaptive admission control for Gemini provider calls.

    A single coordinator is owned by a :class:`GeminiGateway` instance and
    wraps every provider attempt.  It applies a global cap plus smaller lanes
    for research/search and image requests.  A 429 temporarily opens a
    cooldown window and reduces future admission to one active request.  Eight
    successful calls then restore one slot at a time, avoiding an immediate
    return to the request pattern that caused the throttle.

    ``sleep`` is injectable so gateway retry tests can remain deterministic.
    Returning from an injected sleep is treated as the requested interval
    having elapsed; production uses :func:`time.sleep`.
    """

    def __init__(
        self,
        policy: PerformancePolicy,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        initial_adaptive_state: Mapping[str, Any] | None = None,
        on_adaptive_state_change: Callable[[dict[str, int]], None] | None = None,
    ) -> None:
        self.policy = policy
        self._sleep = sleep
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._condition = Condition(RLock())
        self._active_total = 0
        self._active_by_lane: dict[str, int] = {}
        raw_limit = (
            initial_adaptive_state.get("current_limit")
            if initial_adaptive_state is not None
            else None
        )
        raw_successes = (
            initial_adaptive_state.get("successes_since_throttle")
            if initial_adaptive_state is not None
            else None
        )
        raw_generation = (
            initial_adaptive_state.get("throttle_generation")
            if initial_adaptive_state is not None
            else None
        )
        raw_revision = (
            initial_adaptive_state.get("revision")
            if initial_adaptive_state is not None
            else None
        )
        raw_cooldown_until_epoch_ms = (
            initial_adaptive_state.get("cooldown_until_epoch_ms")
            if initial_adaptive_state is not None
            else None
        )
        try:
            initial_limit = int(raw_limit) if raw_limit is not None else policy.max_concurrent_requests
        except (TypeError, ValueError):
            initial_limit = policy.max_concurrent_requests
        try:
            initial_successes = int(raw_successes) if raw_successes is not None else 0
        except (TypeError, ValueError):
            initial_successes = 0
        try:
            initial_generation = int(raw_generation) if raw_generation is not None else 0
        except (TypeError, ValueError):
            initial_generation = 0
        try:
            initial_revision = int(raw_revision) if raw_revision is not None else 0
        except (TypeError, ValueError):
            initial_revision = 0
        self._current_limit = min(policy.max_concurrent_requests, max(1, initial_limit))
        self._successes_since_throttle = (
            max(0, initial_successes)
            if self._current_limit < policy.max_concurrent_requests
            else 0
        )
        self._adaptive_recovery_active = self._current_limit < policy.max_concurrent_requests
        # A state revision makes persistence safe when callbacks from parallel
        # workers arrive out of order. The throttle generation still guards
        # recovery accounting for permits admitted before a 429.
        self._throttle_generation = max(0, initial_generation)
        self._adaptive_state_revision = max(0, initial_revision)
        self._cooldown_until = 0.0
        self._cooldown_until_epoch_ms = 0
        self._restore_cooldown_locked(raw_cooldown_until_epoch_ms)
        self._on_adaptive_state_change = on_adaptive_state_change

    def _adaptive_state_locked(self) -> dict[str, int]:
        state = {
            "current_limit": self._current_limit,
            "successes_since_throttle": self._successes_since_throttle,
            "throttle_generation": self._throttle_generation,
            "revision": self._adaptive_state_revision,
        }
        if self._cooldown_until > self._monotonic() and self._cooldown_until_epoch_ms > 0:
            state["cooldown_until_epoch_ms"] = self._cooldown_until_epoch_ms
        return state

    def _wall_clock_epoch_ms(self) -> int:
        """Return a conservative serializable wall-clock deadline basis."""

        return ceil(self._wall_time() * 1000)

    def _clear_cooldown_locked(self) -> None:
        self._cooldown_until = 0.0
        self._cooldown_until_epoch_ms = 0

    def _restore_cooldown_locked(self, raw_deadline: Any) -> None:
        """Recreate a monotonic cooldown from a persisted epoch deadline.

        Monotonic clocks intentionally have no stable value across worker
        processes.  Persisting the provider's deadline as wall-clock epoch
        milliseconds lets a resumed worker retain the remaining 429 window
        without trusting a previous process's monotonic value.
        """

        try:
            deadline = int(raw_deadline) if raw_deadline is not None else 0
        except (TypeError, ValueError):
            deadline = 0
        now_epoch_ms = self._wall_clock_epoch_ms()
        if deadline <= now_epoch_ms:
            self._clear_cooldown_locked()
            return
        self._cooldown_until_epoch_ms = deadline
        self._cooldown_until = self._monotonic() + (deadline - now_epoch_ms) / 1000

    def restore_adaptive_state(
        self,
        state: Mapping[str, Any] | None,
        *,
        on_change: Callable[[dict[str, int]], None] | None = None,
    ) -> None:
        """Restore safe adaptive state after a worker process is restarted."""

        raw_limit = state.get("current_limit") if state is not None else None
        raw_successes = state.get("successes_since_throttle") if state is not None else None
        raw_generation = state.get("throttle_generation") if state is not None else None
        raw_revision = state.get("revision") if state is not None else None
        raw_cooldown_until_epoch_ms = (
            state.get("cooldown_until_epoch_ms") if state is not None else None
        )
        try:
            limit = int(raw_limit) if raw_limit is not None else self.policy.max_concurrent_requests
        except (TypeError, ValueError):
            limit = self.policy.max_concurrent_requests
        try:
            successes = int(raw_successes) if raw_successes is not None else 0
        except (TypeError, ValueError):
            successes = 0
        try:
            generation = int(raw_generation) if raw_generation is not None else 0
        except (TypeError, ValueError):
            generation = 0
        try:
            revision = int(raw_revision) if raw_revision is not None else 0
        except (TypeError, ValueError):
            revision = 0
        with self._condition:
            self._current_limit = min(self.policy.max_concurrent_requests, max(1, limit))
            self._successes_since_throttle = (
                max(0, successes)
                if self._current_limit < self.policy.max_concurrent_requests
                else 0
            )
            self._adaptive_recovery_active = (
                self._current_limit < self.policy.max_concurrent_requests
            )
            self._throttle_generation = max(0, generation)
            self._adaptive_state_revision = max(0, revision)
            self._restore_cooldown_locked(raw_cooldown_until_epoch_ms)
            self._on_adaptive_state_change = on_change
            self._condition.notify_all()

    def _publish_adaptive_state(self, state: dict[str, int] | None) -> None:
        if state is not None and self._on_adaptive_state_change is not None:
            self._on_adaptive_state_change(state)

    @property
    def current_limit(self) -> int:
        with self._condition:
            return self._current_limit

    def snapshot(self) -> dict[str, Any]:
        """Return safe scheduling diagnostics without request content."""

        with self._condition:
            remaining = max(0.0, self._cooldown_until - self._monotonic())
            return {
                "active_requests": self._active_total,
                "active_by_lane": dict(self._active_by_lane),
                "current_limit": self._current_limit,
                "maximum_limit": self.policy.max_concurrent_requests,
                "cooldown_remaining_ms": round(remaining * 1000),
                "successes_since_throttle": self._successes_since_throttle,
                "throttle_generation": self._throttle_generation,
                "adaptive_state_revision": self._adaptive_state_revision,
            }

    def _lane_limit(self, lane: str) -> int:
        if lane == "research":
            return self.policy.max_research_requests
        if lane == "image":
            return self.policy.max_image_requests
        return self.policy.max_concurrent_requests

    def _can_acquire_locked(self, lane: str) -> bool:
        return (
            self._active_total < self._current_limit
            and self._active_by_lane.get(lane, 0) < self._lane_limit(lane)
        )

    def acquire(
        self,
        lane: str = "default",
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> ProviderRequestPermit:
        """Block until the caller may start one provider attempt.

        When a lifecycle cancellation probe is supplied, contention and
        cooldown waits are sliced into short intervals.  This prevents a
        worker queued behind another request from waking later and issuing a
        newly paid provider call after its run was paused or cancelled.
        """

        while True:
            _raise_if_cancelled(cancellation_requested)
            wait_seconds = 0.0
            observed_deadline = 0.0
            with self._condition:
                # Re-check while holding the same lock used to grant a
                # permit.  A cancellation observed here wins over admission.
                _raise_if_cancelled(cancellation_requested)
                now = self._monotonic()
                if self._cooldown_until <= now:
                    self._clear_cooldown_locked()
                if self._cooldown_until > now:
                    wait_seconds = self._cooldown_until - now
                    observed_deadline = self._cooldown_until
                elif self._can_acquire_locked(lane):
                    self._active_total += 1
                    self._active_by_lane[lane] = self._active_by_lane.get(lane, 0) + 1
                    return ProviderRequestPermit(
                        lane=lane,
                        throttle_generation=self._throttle_generation,
                    )
                else:
                    self._condition.wait(
                        timeout=(
                            _CANCELLATION_POLL_SECONDS
                            if cancellation_requested is not None
                            else None
                        )
                    )
                    continue

            # Do not sleep while holding the lock: other in-flight attempts
            # must be able to release their permits or report a new 429.
            if cancellation_requested is None:
                self._sleep(wait_seconds)
            else:
                remaining = wait_seconds
                while remaining > 0:
                    _raise_if_cancelled(cancellation_requested)
                    interval = min(_CANCELLATION_POLL_SECONDS, remaining)
                    self._sleep(interval)
                    remaining -= interval
                _raise_if_cancelled(cancellation_requested)
            with self._condition:
                # In tests the injected sleep may deliberately not advance a
                # real clock.  Treat its return as the requested wait having
                # completed, but never erase a newer/longer cooldown.
                if self._cooldown_until <= observed_deadline:
                    self._clear_cooldown_locked()
                    self._condition.notify_all()

    def release(self, permit: ProviderRequestPermit) -> None:
        with self._condition:
            active_for_lane = self._active_by_lane.get(permit.lane, 0)
            if active_for_lane <= 0 or self._active_total <= 0:
                raise RuntimeError("Provider request coordinator released an unknown permit")
            if active_for_lane == 1:
                self._active_by_lane.pop(permit.lane, None)
            else:
                self._active_by_lane[permit.lane] = active_for_lane - 1
            self._active_total -= 1
            self._condition.notify_all()

    @contextmanager
    def request(
        self,
        lane: str = "default",
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> Iterator[ProviderRequestPermit]:
        permit = self.acquire(lane, cancellation_requested=cancellation_requested)
        try:
            yield permit
        finally:
            self.release(permit)

    def throttled(self, retry_after_seconds: float) -> _CooldownTicket:
        """Publish a 429 cooldown before the failed call is retried."""

        cooldown_seconds = max(0.0, retry_after_seconds)
        requested_deadline = self._monotonic() + cooldown_seconds
        requested_epoch_ms = self._wall_clock_epoch_ms() + ceil(cooldown_seconds * 1000)
        state: dict[str, int] | None = None
        with self._condition:
            if requested_deadline > self._cooldown_until:
                self._cooldown_until = requested_deadline
                self._cooldown_until_epoch_ms = max(
                    self._cooldown_until_epoch_ms,
                    requested_epoch_ms,
                )
                self._throttle_generation += 1
            self._successes_since_throttle = 0
            if self.policy.adaptive_throttling:
                self._current_limit = 1
                self._adaptive_recovery_active = True
            self._adaptive_state_revision += 1
            self._condition.notify_all()
            state = self._adaptive_state_locked()
        self._publish_adaptive_state(state)
        return _CooldownTicket(requested_deadline=requested_deadline)

    def wait_for_retry(
        self,
        ticket: _CooldownTicket,
        delay_seconds: float,
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> None:
        """Wait once for the throttled call and unblock matching cooldowns.

        Retry waits use the same bounded cancellation behaviour as first-time
        admission, so a paused run cannot wake up and issue a retry later.
        """

        if cancellation_requested is None:
            self._sleep(delay_seconds)
        else:
            remaining = max(0.0, delay_seconds)
            while remaining > 0:
                _raise_if_cancelled(cancellation_requested)
                interval = min(_CANCELLATION_POLL_SECONDS, remaining)
                self._sleep(interval)
                remaining -= interval
            _raise_if_cancelled(cancellation_requested)
        with self._condition:
            # A different request may have extended the cooldown while this
            # caller slept.  Only this caller's own (or a shorter) deadline
            # may be cleared here.
            if ticket.requested_deadline >= self._cooldown_until:
                self._clear_cooldown_locked()
                self._condition.notify_all()

    def succeeded(self, permit: ProviderRequestPermit) -> None:
        """Record a successful provider attempt and gradually restore slots."""

        state: dict[str, int] | None = None
        with self._condition:
            # Calls admitted before a later 429 do not count toward recovery.
            if permit.throttle_generation != self._throttle_generation:
                return
            self._successes_since_throttle += 1
            if (
                self.policy.adaptive_throttling
                and self._current_limit < self.policy.max_concurrent_requests
                and self._successes_since_throttle >= self.policy.recovery_successes
            ):
                self._current_limit += 1
                self._successes_since_throttle = 0
                self._condition.notify_all()
            if self._adaptive_recovery_active:
                self._adaptive_state_revision += 1
                state = self._adaptive_state_locked()
                if self._current_limit >= self.policy.max_concurrent_requests:
                    self._adaptive_recovery_active = False
        self._publish_adaptive_state(state)


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
        request_coordinator: ProviderRequestCoordinator | None = None,
    ) -> None:
        self.settings = settings
        self.secret_store = secret_store or CredentialSecretStore()
        self.usage_sink = usage_sink
        self._sleep = sleep
        self.request_coordinator = request_coordinator or ProviderRequestCoordinator(
            settings.performance_policy,
            sleep=sleep,
        )
        self._usage_lock = RLock()
        self._work_item_id: ContextVar[str] = ContextVar(
            "papercraft_gemini_work_item_id",
            default="",
        )
        self._cancellation_requested: ContextVar[Callable[[], bool] | None] = ContextVar(
            "papercraft_gemini_cancellation_requested",
            default=None,
        )

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
                # the SDK's retryable HTTP codes with an unused sentinel.
                # ``attempts=1`` means no SDK retries, so one logical call
                # cannot fan out into duplicate paid POST requests.
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
                # SDK initialisation errors can include a serialized request or
                # credential/header details.  Keep the durable/UI-facing error
                # classification-only; the original exception remains chained
                # for a local debugger without being persisted by the runner.
                raise GeminiAuthenticationError(
                    f"Unable to initialise Gemini client ({type(exc).__name__})"
                ) from exc
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
    def _lane_for_role(role: str) -> str:
        """Map provider roles to the narrow lanes enforced by the coordinator."""

        return "research" if role == "research" else "default"

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
        """Return whether an exception is an ambiguous transport failure.

        Transport failures must be surfaced as unavailable, not retried: the
        provider might have accepted the paid POST before its response was
        lost.  Keep local programming errors out of that classification too.
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

    @staticmethod
    def _sdk_version() -> str:
        """Return the installed SDK version without importing provider internals."""

        try:
            return package_version("google-genai")
        except PackageNotFoundError:
            # Unit tests can inject a client without installing the production
            # dependency. The diagnostic remains useful without fabricating a
            # version number.
            return "unavailable"

    @staticmethod
    def _canonical_json_bytes(value: Any) -> bytes:
        """Encode schema metadata deterministically for fingerprinting only."""

        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")

    @staticmethod
    def _provider_json_schema(schema_document: dict[str, Any]) -> dict[str, Any]:
        """Return the Interactions-compatible copy of a Pydantic JSON Schema.

        Gemini's current Interactions endpoint rejects ``maxItems`` for the
        production ``ResearchPlan`` schema with HTTP 400, even though the
        keyword is accepted by Pydantic and documented for structured output.
        Keep the domain schema strict locally and omit only that provider-side
        constraint from the request.  The recursion also covers nested arrays
        without altering any other JSON Schema keyword.
        """

        def transform(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {
                    key: transform(item)
                    for key, item in value.items()
                    if key != "maxItems"
                }
            if isinstance(value, list):
                return [transform(item) for item in value]
            return value

        transformed = transform(schema_document)
        if not isinstance(transformed, dict):  # pragma: no cover - schema root is an object
            raise TypeError("Pydantic JSON Schema root must be an object")
        return transformed

    @staticmethod
    def _sanitize_provider_text(value: Any, *, limit: int = _MAX_PROVIDER_DIAGNOSTIC_CHARS) -> str:
        """Keep provider diagnostics compact without exposing credentials.

        This helper intentionally accepts only text selected from provider error
        fields. It is not used to serialize a request or response body, because
        either may contain a user prompt or uploaded document content.
        """

        if not isinstance(value, str):
            return ""
        cleaned = re.sub(r"\s+", " ", value).strip()
        for pattern, replacement in _SECRET_PATTERNS:
            cleaned = pattern.sub(replacement, cleaned)
        if len(cleaned) > limit:
            return f"{cleaned[:limit - 1]}…"
        return cleaned

    @classmethod
    def _error_mapping(cls, value: Any) -> dict[str, Any] | None:
        """Return a structured provider error object, never a raw text body."""

        if isinstance(value, Mapping):
            return dict(value)
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump(mode="json", exclude_none=True)
            except Exception:
                dumped = None
            if isinstance(dumped, Mapping):
                return dict(dumped)
        response_json = getattr(value, "json", None)
        if callable(response_json):
            try:
                decoded = response_json()
            except Exception:
                decoded = None
            if isinstance(decoded, Mapping):
                return dict(decoded)
        return None

    @classmethod
    def _provider_error_payload(cls, exc: Exception) -> dict[str, Any] | None:
        """Find the SDK's structured error payload without reading raw bodies."""

        for candidate in (
            getattr(exc, "details", None),
            getattr(exc, "body", None),
            getattr(exc, "error", None),
            getattr(exc, "response", None),
        ):
            payload = cls._error_mapping(candidate)
            if payload is not None:
                return payload
        return None

    @classmethod
    def _field_violation_descriptions(cls, payload: Any) -> list[str]:
        """Extract only documented field-violation descriptions from an error."""

        results: list[str] = []

        def visit(value: Any, *, depth: int) -> None:
            if depth > 8 or len(results) >= _MAX_PROVIDER_FIELD_VIOLATIONS:
                return
            if isinstance(value, Mapping):
                violations = value.get("fieldViolations") or value.get("field_violations")
                if isinstance(violations, list):
                    for violation in violations:
                        if not isinstance(violation, Mapping):
                            continue
                        description = cls._sanitize_provider_text(
                            violation.get("description"), limit=256
                        )
                        if description:
                            results.append(description)
                        if len(results) >= _MAX_PROVIDER_FIELD_VIOLATIONS:
                            return
                for key in ("error", "details", "violations"):
                    nested = value.get(key)
                    if isinstance(nested, (Mapping, list)):
                        visit(nested, depth=depth + 1)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested, depth=depth + 1)
                    if len(results) >= _MAX_PROVIDER_FIELD_VIOLATIONS:
                        return

        visit(payload, depth=0)
        return results

    @classmethod
    def _provider_error_message(
        cls,
        exc: Exception,
        payload: dict[str, Any] | None,
    ) -> str:
        """Select a safe, provider-supplied message without falling back to ``str(exc)``."""

        candidates: list[Any] = []
        if payload is not None:
            candidates.append(payload.get("message"))
            nested_error = payload.get("error")
            if isinstance(nested_error, Mapping):
                candidates.append(nested_error.get("message"))
        for candidate in candidates:
            cleaned = cls._sanitize_provider_text(candidate)
            if cleaned:
                return cleaned
        return "Provider did not supply a safe error message."

    @classmethod
    def _safe_provider_error_summary(cls, exc: Exception, *, status: int | None) -> str:
        """Return bounded provider diagnostics suitable for an exception message."""

        payload = cls._provider_error_payload(exc)
        summary: dict[str, Any] = {
            "exception_type": type(exc).__name__,
            "message": cls._provider_error_message(exc, payload),
            "status_code": status,
        }
        request_id = cls._sanitize_provider_text(cls._provider_request_id(exc), limit=512)
        if request_id:
            summary["provider_request_id"] = request_id
        violations = cls._field_violation_descriptions(payload)
        if violations:
            summary["field_violations"] = violations
        return cls._canonical_json_bytes(summary).decode("utf-8")

    @classmethod
    def _structured_request_metadata(
        cls,
        *,
        model: str,
        role: str,
        thinking_level: str,
        schema: type[BaseModel],
        schema_document: dict[str, Any],
        has_files: bool,
        file_count: int,
        has_system_instruction: bool,
    ) -> dict[str, Any]:
        """Build non-sensitive metadata for a structured request failure.

        The JSON Schema is fingerprinted rather than emitted so exception
        messages provide reproducibility data without leaking static schema
        descriptions or request content.
        """

        encoded_schema = cls._canonical_json_bytes(schema_document)
        return {
            "file_count": file_count,
            "has_system_instruction": has_system_instruction,
            "input_shape": "multimodal" if has_files else "text",
            "model": model,
            "role": role,
            "schema_bytes": len(encoded_schema),
            "schema_name": schema.__name__,
            "schema_sha256": hashlib.sha256(encoded_schema).hexdigest(),
            "sdk_version": cls._sdk_version(),
            "thinking_level": thinking_level,
        }

    def _call(
        self,
        operation: str,
        function: Callable[[], Any],
        *,
        not_found_ok: bool = False,
        enforce_cost_limit: bool = True,
        honor_cancellation: bool = True,
        error_context: dict[str, Any] | None = None,
        lane: str = "default",
        max_attempts: int | None = None,
    ) -> Any:
        retry = self.settings.retry_policy
        attempt_limit = max_attempts if max_attempts is not None else retry.max_attempts
        if attempt_limit < 1:
            raise ValueError("max_attempts must be at least 1")
        last_error: Exception | None = None
        last_retry_after_seconds: float | None = None
        started_at = time.monotonic()
        retry_wait_seconds = 0.0
        # A cancellation scope stops paid work before it can be admitted.
        # Idempotent remote cleanup/lifecycle calls are the exception: they
        # often run *because* the scope was cancelled and must still reach
        # Gemini to avoid retaining remote data.
        cancellation_requested = (
            self._cancellation_requested.get() if honor_cancellation else None
        )
        for attempt in range(1, attempt_limit + 1):
            throttle_ticket: _CooldownTicket | None = None
            throttle_delay: float | None = None
            try:
                _raise_if_cancelled(cancellation_requested)
                request = (
                    self.request_coordinator.request(lane)
                    if cancellation_requested is None
                    else self.request_coordinator.request(
                        lane,
                        cancellation_requested=cancellation_requested,
                    )
                )
                with request as permit:
                    # A queued worker can be cancelled after the coordinator
                    # wakes it but before this thread reaches its provider
                    # call.  Preserve any already-returned response, but do
                    # not create a new billable request in that narrow gap.
                    _raise_if_cancelled(cancellation_requested)
                    # Check *after* admission on every paid attempt. A request
                    # can spend time queued behind another worker (or sleeping
                    # for a retry) while that worker crosses the durable cost
                    # cap. It must then give its permit back without issuing a
                    # new paid provider call. Idempotent remote cleanup and
                    # stored-interaction lifecycle calls deliberately bypass
                    # this gate: blocking them would strand provider data after
                    # a run reaches its cap.
                    limit_probe = getattr(self.usage_sink, "limit_reached", None)
                    if enforce_cost_limit and callable(limit_probe) and bool(limit_probe()):
                        raise _GeminiCostLimitError(
                            "Gemini request skipped because the configured cost limit was reached"
                        )
                    try:
                        response = function()
                    except Exception as exc:
                        # Publish the 429 while this permit is still held.
                        # Releasing first would leave a small window in which
                        # another waiting worker could start a new request.
                        if self._status_code(exc) == 429:
                            throttle_delay = min(
                                retry.maximum_delay_seconds,
                                retry.base_delay_seconds * (2 ** (attempt - 1)),
                            ) + random.uniform(0, retry.jitter_seconds)
                            retry_after = self._retry_after_seconds(exc)
                            if retry_after is not None:
                                # Retry-After is a provider instruction, not
                                # an ordinary exponential-backoff preference.
                                # Capping it at our normal retry maximum
                                # causes a new paid request before the quota
                                # window has reopened.
                                last_retry_after_seconds = retry_after
                                throttle_delay = max(
                                    throttle_delay,
                                    retry_after,
                                )
                            throttle_ticket = self.request_coordinator.throttled(throttle_delay)
                        raise
                self.request_coordinator.succeeded(permit)
                if isinstance(response, _InteractionResponse):
                    return replace(
                        response,
                        telemetry=_CallTelemetry(
                            duration_ms=max(0, round((time.monotonic() - started_at) * 1000)),
                            attempts=attempt,
                            retry_wait_ms=max(0, round(retry_wait_seconds * 1000)),
                        ),
                    )
                return response
            except (_GeminiCostLimitError, GeminiRequestCancelled):
                raise
            except Exception as exc:
                last_error = exc
                status = self._status_code(exc)
                if status == 404 and not_found_ok:
                    return None
                if status in {400, 401, 403, 404}:
                    diagnostic = self._safe_provider_error_summary(exc, status=status)
                    if status in {401, 403}:
                        raise GeminiAuthenticationError(
                            f"{operation} was rejected: {diagnostic}"
                        ) from exc
                    if status == 404:
                        raise GeminiConfigurationError(
                            f"{operation} references an unavailable model or endpoint: {diagnostic}"
                        ) from exc
                    if error_context is not None:
                        context = self._canonical_json_bytes(error_context).decode("utf-8")
                        diagnostic = f"{diagnostic}; structured_request_metadata={context}"
                    raise GeminiGatewayError(f"{operation} is invalid: {diagnostic}") from exc
                # Interactions are paid POST requests.  A 429 confirms that
                # Gemini rejected the attempt before it could run, so the
                # bounded retry below is safe.  Timeouts, transport errors,
                # 408/409, and 5xx responses are ambiguous: Gemini may have
                # accepted and billed them despite the missing response.  Do
                # not silently duplicate those operations; surface a
                # resumable provider failure instead.
                if status != 429:
                    diagnostic = self._safe_provider_error_summary(exc, status=status)
                    if status in {408, 409, 500, 502, 503, 504} or (
                        status is None and self._is_transport_error(exc)
                    ):
                        raise GeminiUnavailableError(
                            f"{operation} could not be safely retried: {diagnostic}"
                        ) from exc
                    raise GeminiGatewayError(f"{operation} failed: {diagnostic}") from exc
                delay = throttle_delay
                if delay is None:
                    delay = min(
                        retry.maximum_delay_seconds,
                        retry.base_delay_seconds * (2 ** (attempt - 1)),
                    ) + random.uniform(0, retry.jitter_seconds)
                if attempt >= attempt_limit:
                    break
                retry_wait_seconds += delay
                if throttle_ticket is not None:
                    self.request_coordinator.wait_for_retry(
                        throttle_ticket,
                        delay,
                        cancellation_requested=cancellation_requested,
                    )
                else:
                    if cancellation_requested is None:
                        self._sleep(delay)
                    else:
                        remaining = max(0.0, delay)
                        while remaining > 0:
                            _raise_if_cancelled(cancellation_requested)
                            interval = min(_CANCELLATION_POLL_SECONDS, remaining)
                            self._sleep(interval)
                            remaining -= interval
                        _raise_if_cancelled(cancellation_requested)
        diagnostic = (
            self._safe_provider_error_summary(
                last_error,
                status=self._status_code(last_error),
            )
            if last_error is not None
            else '{"exception_type":"Unknown","message":"No provider error was captured.","status_code":null}'
        )
        raise GeminiUnavailableError(
            f"{operation} failed after {attempt_limit} attempts: {diagnostic}",
            retry_after_seconds=last_retry_after_seconds,
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
        response_headers = getattr(response, "headers", None)
        http_response = getattr(response, "http_response", None)
        nested_response = getattr(response, "response", None)
        header_sets = (
            response_headers,
            getattr(http_response, "headers", None),
            getattr(nested_response, "headers", None),
        )
        for headers in header_sets:
            if headers is None:
                continue
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

    def _usage(
        self,
        response: Any,
        operation: str,
        model: str,
        *,
        conservative_input_tokens: int = 0,
    ) -> UsageRecord:
        payload = self._payload(response)
        provider_request_id = (
            response.request_id if isinstance(response, _InteractionResponse) else None
        )
        client_request_id = (
            response.client_request_id if isinstance(response, _InteractionResponse) else None
        )
        telemetry = response.telemetry if isinstance(response, _InteractionResponse) else None
        request_id = provider_request_id or ""
        usage = getattr(payload, "usage", None) or getattr(payload, "usage_metadata", None)
        reported_input_tokens = int(
            getattr(usage, "total_input_tokens", None)
            or getattr(usage, "input_tokens", 0)
            or getattr(usage, "prompt_token_count", 0)
            or 0
        )
        # The Embed Content response does not consistently expose token usage
        # across API surfaces. Its input is nevertheless billable. A UTF-8
        # byte count is a deliberately conservative upper-bound estimate that
        # lets the durable/live budget stop later embedding calls even when
        # the provider omits usage metadata. Never persist the text itself.
        fallback_input_tokens = max(0, int(conservative_input_tokens))
        input_tokens = max(reported_input_tokens, fallback_input_tokens)
        output_tokens = int(
            getattr(usage, "total_output_tokens", None)
            or getattr(usage, "output_tokens", 0)
            or getattr(usage, "response_token_count", 0)
            or 0
        )
        thought_tokens = int(
            getattr(usage, "total_thought_tokens", 0)
            or getattr(usage, "thoughts_token_count", 0)
            or 0
        )
        tool_use_tokens = int(
            getattr(usage, "total_tool_use_tokens", 0)
            or getattr(usage, "tool_use_prompt_token_count", 0)
            or 0
        )
        total_tokens = int(
            getattr(usage, "total_tokens", 0)
            or getattr(usage, "total_token_count", 0)
            or input_tokens + output_tokens
        )
        total_tokens = max(total_tokens, input_tokens + output_tokens)
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
                "input_tokens_source": (
                    "conservative_estimate"
                    if fallback_input_tokens > reported_input_tokens
                    else "provider"
                    if reported_input_tokens > 0
                    else "unavailable"
                ),
                "search_queries": search_queries,
                "pricing_missing": price is None,
                "interaction_id": str(getattr(payload, "id", "") or ""),
                "request_id": request_id,
                "request_id_source": "provider" if provider_request_id else "unavailable",
                "provider_request_id": provider_request_id or "",
                "client_request_id": client_request_id or "",
                "status": str(getattr(payload, "status", "") or ""),
                "duration_ms": telemetry.duration_ms if telemetry is not None else 0,
                "attempts": telemetry.attempts if telemetry is not None else 1,
                "retry_wait_ms": telemetry.retry_wait_ms if telemetry is not None else 0,
                "work_item_id": self._work_item_id.get(),
            },
        )

    @contextmanager
    def work_item_scope(self, work_item_id: str) -> Iterator[None]:
        """Attach a stable stage item ID to safe usage telemetry in this thread."""

        token = self._work_item_id.set(work_item_id)
        try:
            yield
        finally:
            self._work_item_id.reset(token)

    @contextmanager
    def cancellation_scope(self, cancellation_requested: Callable[[], bool]) -> Iterator[None]:
        """Bind a stage worker's cooperative lifecycle probe to this thread.

        The scope is deliberately opt-in and thread-local.  Direct provider
        calls preserve their existing behaviour, while parallel stage workers
        can cancel a queued admission without affecting another run or an
        already-billable request in a different worker.
        """

        token = self._cancellation_requested.set(cancellation_requested)
        try:
            yield
        finally:
            self._cancellation_requested.reset(token)

    def _record(
        self,
        response: Any,
        operation: str,
        model: str,
        *,
        conservative_input_tokens: int = 0,
    ) -> None:
        if self.usage_sink is not None:
            # Parallel stage workers can complete together.  Serialising the
            # sink keeps repository-backed cost tracking deterministic without
            # exposing caller prompts or response bodies in telemetry.
            with self._usage_lock:
                self.usage_sink(
                    self._usage(
                        response,
                        operation,
                        model,
                        conservative_input_tokens=conservative_input_tokens,
                    )
                )

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

    def health_check(self, *, fail_fast: bool = False) -> None:
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

        response = self._call(
            "Gemini health check",
            invoke,
            max_attempts=1 if fail_fast else None,
        )
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

        response = self._call(
            f"text generation ({role})",
            invoke,
            lane=self._lane_for_role(role),
        )
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
        thinking_level = self._thinking_level(role)
        schema_document = schema.model_json_schema()
        provider_schema_document = self._provider_json_schema(schema_document)
        supplied_files = files or []
        base_multimodal_input: list[dict[str, Any]] | None = None
        if supplied_files:
            base_multimodal_input = [{"type": "text", "text": prompt}]
            for remote in supplied_files:
                uri = remote.uri if isinstance(remote, RemoteFile) else remote
                mime_type = remote.mime_type if isinstance(remote, RemoteFile) else None
                media_type = "document"
                if mime_type:
                    media_type = mime_type.split("/", 1)[0]
                    if media_type not in {"image", "audio", "video"}:
                        media_type = "document"
                base_multimodal_input.append(
                    {
                        "type": media_type,
                        "uri": uri,
                        "mime_type": mime_type or "application/octet-stream",
                    }
                )

        request_metadata = self._structured_request_metadata(
            model=model,
            role=role,
            thinking_level=thinking_level,
            schema=schema,
            schema_document=provider_schema_document,
            has_files=base_multimodal_input is not None,
            file_count=len(supplied_files),
            has_system_instruction=bool(system_instruction),
        )
        interaction_input: str | list[dict[str, Any]] = (
            base_multimodal_input if base_multimodal_input is not None else prompt
        )

        def invoke() -> Any:
            body: dict[str, Any] = {
                "model": model,
                "input": interaction_input,
                "store": False,
                "response_format": {
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": provider_schema_document,
                },
                "generation_config": {"thinking_level": thinking_level},
                "timeout": self.settings.request_timeout_seconds,
            }
            if system_instruction:
                body["system_instruction"] = system_instruction
            return self._create_interaction(**body)

        last_error: ValidationError | None = None
        for structured_attempt in range(1, 4):
            response = self._call(
                f"structured generation ({role})",
                invoke,
                error_context=request_metadata,
                lane=self._lane_for_role(role),
            )
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
                feedback = (
                    "Your previous JSON did not validate. Return a corrected complete JSON object only. "
                    f"Validation error: {str(exc)[:4000]}. Previous output: {raw[:12000]}"
                )
                if base_multimodal_input is None:
                    # Text-only Interactions requests use a string. Include the
                    # original instruction and repair feedback in the next
                    # string rather than changing the request shape.
                    interaction_input = f"{prompt}\n\n{feedback}"
                else:
                    # Build a new list. Mutating the first request's list
                    # retrospectively would corrupt audit/test evidence.
                    interaction_input = [
                        *base_multimodal_input,
                        {
                            "type": "text",
                            "text": feedback,
                        },
                    ]
                continue
            return parsed
        # A pydantic validation error may include values copied from the model
        # response.  It is useful only for the in-process repair prompt above,
        # not for a durable stage/run event.
        raise GeminiStructuredOutputError(
            "Gemini response failed schema validation after three attempts"
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

        response = self._call("grounded Google Search", invoke, lane="research")
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
            enforce_cost_limit=False,
            honor_cancellation=False,
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

        response = self._call("Gemini image generation", invoke, lane="image")
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
            self._record(
                response,
                "embed_texts",
                model,
                conservative_input_tokens=len(text.encode("utf-8")),
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

        response = self._call(
            f"background generation ({role})",
            invoke,
            lane=self._lane_for_role(role),
        )
        self._record(response, "start_background_text", model)
        payload = self._require_usable_response(
            response, operation=f"background generation ({role})"
        )
        interaction_id = str(getattr(payload, "id", "") or "")
        if not interaction_id:
            raise GeminiGatewayError("Gemini background request returned no interaction ID")
        return validate_interaction_id(interaction_id)

    def cancel_interaction(self, interaction_id: str) -> str:
        interaction_id = validate_interaction_id(interaction_id)
        response = self._call(
            "Gemini interaction cancellation",
            lambda: self.client.interactions.cancel(
                id=interaction_id,
                extra_headers={"Api-Revision": "2026-05-20"},
                timeout=self.settings.request_timeout_seconds,
            ),
            enforce_cost_limit=False,
            honor_cancellation=False,
        )
        status = str(getattr(response, "status", "") or "").casefold()
        if not status:
            raise GeminiGatewayError("Gemini cancellation returned no status")
        return status

    def get_interaction_status(self, interaction_id: str) -> str | None:
        """Return the normalized background status, or ``None`` after a safe 404.

        A deleted interaction is deliberately indistinguishable from any other
        not-found response.  Neither the provider error text nor the ID is
        copied into diagnostics.
        """

        interaction_id = validate_interaction_id(interaction_id)
        response = self._call(
            "Gemini interaction lookup",
            lambda: self.client.interactions.get(
                id=interaction_id,
                extra_headers={"Api-Revision": "2026-05-20"},
                timeout=self.settings.request_timeout_seconds,
            ),
            not_found_ok=True,
            enforce_cost_limit=False,
            honor_cancellation=False,
        )
        if response is None:
            return None
        payload = self._payload(response)
        status = str(getattr(payload, "status", "") or "").casefold()
        if not status:
            raise GeminiGatewayError("Gemini interaction lookup returned no status")
        return status

    def delete_interaction(self, interaction_id: str) -> None:
        """Delete a stored background interaction; a prior deletion is success."""

        interaction_id = validate_interaction_id(interaction_id)
        self._call(
            "Gemini interaction deletion",
            lambda: self.client.interactions.delete(
                id=interaction_id,
                extra_headers={"Api-Revision": "2026-05-20"},
                timeout=self.settings.request_timeout_seconds,
            ),
            not_found_ok=True,
            enforce_cost_limit=False,
            honor_cancellation=False,
        )
