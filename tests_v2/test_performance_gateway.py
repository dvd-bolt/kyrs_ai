from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from typing import Any, cast

import pytest

from papercraft.application import ProjectService, RunUsageTracker, prepare_autopilot
from papercraft.application.scheduling import WorkItem, WorkStatus, run_dependency_aware
from papercraft.application.stages import _gateway_work_item_scope
from papercraft.config import AppSettings, PerformancePolicy, RetryPolicy
from papercraft.domain import GenerationRun, ProjectBrief
from papercraft.infrastructure.gemini import (
    FakeGeminiGateway,
    GeminiGateway,
    GeminiGatewayError,
    GeminiRequestCancelled,
    ProviderRequestCoordinator,
    UsageRecord,
)


class _Interactions:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)

    def create(self, **_: Any) -> Any:
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        projects_root=tmp_path,
        retry_policy=RetryPolicy(
            max_attempts=2,
            base_delay_seconds=0,
            jitter_seconds=0,
            maximum_delay_seconds=10,
        ),
    )


def test_performance_policy_defaults_are_conservative(tmp_path: Path) -> None:
    policy = AppSettings(projects_root=tmp_path).performance_policy

    assert policy.max_concurrent_requests == 3
    assert policy.max_research_requests == 2
    assert policy.max_section_requests == 3
    assert policy.max_image_requests == 2
    assert policy.web_cache_ttl_hours == 168
    assert policy.adaptive_throttling is True
    assert policy.recovery_successes == 8
    assert policy.parallel_generation_enabled is False


def test_provider_coordinator_enforces_global_and_research_limits() -> None:
    def measure_peak(policy: PerformancePolicy, lane: str, expected_peak: int) -> None:
        coordinator = ProviderRequestCoordinator(policy)
        entered = Event()
        release = Event()
        active_lock = Lock()
        active = 0
        peak = 0

        def request() -> None:
            nonlocal active, peak
            with coordinator.request(lane):
                with active_lock:
                    active += 1
                    peak = max(peak, active)
                    if peak >= expected_peak:
                        entered.set()
                assert release.wait(timeout=2)
                with active_lock:
                    active -= 1

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(request) for _ in range(4)]
            assert entered.wait(timeout=1)
            snapshot = coordinator.snapshot()
            assert snapshot["active_requests"] == expected_peak
            assert snapshot["active_by_lane"][lane] == expected_peak
            release.set()
            for future in futures:
                future.result(timeout=2)

        assert peak == expected_peak

    measure_peak(PerformancePolicy(max_concurrent_requests=3), "default", 3)
    measure_peak(
        PerformancePolicy(max_concurrent_requests=3, max_research_requests=2),
        "research",
        2,
    )


def test_provider_coordinator_cancelled_waiter_never_acquires_a_permit() -> None:
    coordinator = ProviderRequestCoordinator(PerformancePolicy(max_concurrent_requests=1))
    held = coordinator.acquire()
    cancelled = Event()
    entered_admission = Event()

    def cancellation_requested() -> bool:
        entered_admission.set()
        return cancelled.is_set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        waiting = executor.submit(
            coordinator.acquire,
            cancellation_requested=cancellation_requested,
        )
        assert entered_admission.wait(timeout=1)
        cancelled.set()
        with pytest.raises(GeminiRequestCancelled):
            waiting.result(timeout=2)

    # The held request is the only one which ever entered the provider lane.
    assert coordinator.snapshot()["active_requests"] == 1
    coordinator.release(held)


def test_cancelled_scheduled_section_never_calls_gemini_after_waiting_for_permit(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.performance_policy.max_concurrent_requests = 1
    coordinator = ProviderRequestCoordinator(settings.performance_policy)
    held = coordinator.acquire()
    response = SimpleNamespace(
        output_text="unexpected provider call",
        usage=None,
        status="completed",
        id="unexpected",
    )
    interactions = _Interactions([response])
    gateway = GeminiGateway(
        settings,
        client=SimpleNamespace(interactions=interactions),
        request_coordinator=coordinator,
    )
    cancelled = Event()
    queued = Event()

    def worker(context: Any) -> str:
        def cancellation_requested() -> bool:
            queued.set()
            return context.cancellation_probe()

        with _gateway_work_item_scope(
            gateway,
            context.item.id,
            cancellation_requested=cancellation_requested,
        ):
            gateway.health_check()
        return context.item.id

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            scheduled = executor.submit(
                run_dependency_aware,
                [WorkItem("section-introduction", "Introduction")],
                {},
                worker,
                max_workers=1,
                cancellation_requested=cancelled.is_set,
            )
            assert queued.wait(timeout=1)
            cancelled.set()
            result = scheduled.result(timeout=2)
    finally:
        coordinator.release(held)

    assert result.cancellation_requested
    assert result.records_by_id["section-introduction"].status is WorkStatus.CANCELLED
    # The queued worker was never admitted, so it could not issue a paid
    # Gemini request.  Its fake response stays untouched in the client.
    assert interactions.responses == [response]


def test_429_downshifts_and_eight_successes_restore_one_slot() -> None:
    coordinator = ProviderRequestCoordinator(
        PerformancePolicy(max_concurrent_requests=3, recovery_successes=8),
        sleep=lambda _: None,
    )
    ticket = coordinator.throttled(0)
    coordinator.wait_for_retry(ticket, 0)

    assert coordinator.current_limit == 1
    for _ in range(8):
        with coordinator.request() as permit:
            pass
        coordinator.succeeded(permit)
    assert coordinator.current_limit == 2

    for _ in range(8):
        with coordinator.request() as permit:
            pass
        coordinator.succeeded(permit)
    assert coordinator.current_limit == 3


def test_adaptive_downshift_survives_a_new_worker_coordinator() -> None:
    policy = PerformancePolicy(max_concurrent_requests=3, recovery_successes=8)
    persisted_states: list[dict[str, int]] = []
    first_worker = ProviderRequestCoordinator(
        policy,
        sleep=lambda _: None,
        on_adaptive_state_change=persisted_states.append,
    )
    first_worker.throttled(0)

    assert persisted_states[-1]["current_limit"] == 1
    resumed_worker = ProviderRequestCoordinator(
        policy,
        sleep=lambda _: None,
        initial_adaptive_state=persisted_states[-1],
    )
    assert resumed_worker.current_limit == 1

    for _ in range(8):
        with resumed_worker.request() as permit:
            pass
        resumed_worker.succeeded(permit)
    assert resumed_worker.current_limit == 2


def test_active_429_cooldown_survives_worker_restart() -> None:
    policy = PerformancePolicy(max_concurrent_requests=3)
    clock = {"monotonic": 10.0, "wall": 1_000.0}
    persisted_states: list[dict[str, int]] = []

    def monotonic() -> float:
        return clock["monotonic"]

    def wall_time() -> float:
        return clock["wall"]

    first_worker = ProviderRequestCoordinator(
        policy,
        sleep=lambda _: None,
        monotonic=monotonic,
        wall_time=wall_time,
        on_adaptive_state_change=persisted_states.append,
    )
    first_worker.throttled(30)
    persisted = persisted_states[-1]
    assert persisted["cooldown_until_epoch_ms"] == 1_030_000

    # A new process has an unrelated monotonic-clock origin.  Only the
    # serializable epoch deadline tells it that 25 seconds remain.
    clock.update(monotonic=500.0, wall=1_005.0)
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["monotonic"] += seconds
        clock["wall"] += seconds

    resumed_worker = ProviderRequestCoordinator(
        policy,
        sleep=sleep,
        monotonic=monotonic,
        wall_time=wall_time,
    )
    resumed_worker.restore_adaptive_state(persisted)

    assert resumed_worker.snapshot()["cooldown_remaining_ms"] == 25_000
    permit = resumed_worker.acquire()
    resumed_worker.release(permit)
    assert slept == [25.0]


def test_runtime_restores_and_persists_adaptive_state_for_resume(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Durable throttle"))
    run = GenerationRun(
        project_id=workspace.project.id,
        metadata={
            "provider_adaptive_state": {
                "current_limit": 1,
                "successes_since_throttle": 0,
            }
        },
    )
    workspace.repository.save_run(run)
    gateway = GeminiGateway(
        settings,
        client=SimpleNamespace(interactions=_Interactions([])),
    )

    prepare_autopilot(settings, workspace, run_id=run.id, gateway=gateway)
    assert gateway.request_coordinator.current_limit == 1
    for _ in range(8):
        with gateway.request_coordinator.request() as permit:
            pass
        gateway.request_coordinator.succeeded(permit)

    persisted = workspace.repository.get_run(run.id)
    assert persisted is not None
    assert persisted.metadata["provider_adaptive_state"] == {
        "current_limit": 2,
        "successes_since_throttle": 0,
        "throttle_generation": 0,
        "revision": 8,
    }


def test_runtime_ignores_out_of_order_adaptive_state_callbacks(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Ordered throttle state"))
    run = GenerationRun(project_id=workspace.project.id)
    workspace.repository.save_run(run)
    gateway = GeminiGateway(
        settings,
        client=SimpleNamespace(interactions=_Interactions([])),
    )

    prepare_autopilot(settings, workspace, run_id=run.id, gateway=gateway)
    callback = gateway.request_coordinator._on_adaptive_state_change
    assert callable(callback)
    # This models a newer 429 callback finishing before a delayed success
    # callback from another worker. The old state must not raise the persisted
    # admission limit back to three on the next worker restart.
    callback(
        {
            "current_limit": 1,
            "successes_since_throttle": 0,
            "throttle_generation": 4,
            "revision": 12,
        }
    )
    callback(
        {
            "current_limit": 3,
            "successes_since_throttle": 7,
            "throttle_generation": 3,
            "revision": 11,
        }
    )

    persisted = workspace.repository.get_run(run.id)
    assert persisted is not None
    assert persisted.metadata["provider_adaptive_state"] == {
        "current_limit": 1,
        "successes_since_throttle": 0,
        "throttle_generation": 4,
        "revision": 12,
    }


def test_gateway_records_safe_retry_timing_and_honours_retry_after(tmp_path: Path) -> None:
    class Throttled(RuntimeError):
        status_code = 429
        response = SimpleNamespace(headers={"Retry-After": "17"})

    response = SimpleNamespace(output_text="OK", usage=None, status="completed", id="ok")
    client = SimpleNamespace(interactions=_Interactions([Throttled("quota"), response]))
    usage = []
    sleeps: list[float] = []

    gateway = GeminiGateway(
        _settings(tmp_path),
        client=client,
        usage_sink=usage.append,
        sleep=sleeps.append,
    )
    gateway.health_check()

    # Provider Retry-After wins even when it is longer than our ordinary
    # exponential-backoff cap.
    assert sleeps == [17]
    assert gateway.request_coordinator.current_limit == 1
    assert usage[0].metadata["attempts"] == 2
    assert usage[0].metadata["retry_wait_ms"] == 17000
    assert usage[0].metadata["duration_ms"] >= 0
    assert set(usage[0].metadata).isdisjoint({"prompt", "input", "headers", "api_key"})


def test_gateway_usage_binds_parallel_work_item_without_prompt_data(tmp_path: Path) -> None:
    response = SimpleNamespace(output_text="OK", usage=None, status="completed", id="ok")
    usage: list[Any] = []
    gateway = GeminiGateway(
        _settings(tmp_path),
        client=SimpleNamespace(interactions=_Interactions([response])),
        usage_sink=usage.append,
    )

    with gateway.work_item_scope("section-introduction"):
        gateway.health_check()

    assert usage[0].metadata["work_item_id"] == "section-introduction"
    assert set(usage[0].metadata).isdisjoint({"prompt", "input", "headers", "api_key"})


def test_cost_tracker_preserves_paid_response_and_blocks_only_the_next_request(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Cost cap"))
    run = GenerationRun(project_id=workspace.project.id)
    workspace.repository.save_run(run)
    tracker = RunUsageTracker(
        workspace.repository,
        run.id,
        maximum_cost=Decimal("0.01"),
    )

    # This represents a response which has already reached the client and is
    # therefore billable. Recording it must not raise through the gateway.
    tracker(
        UsageRecord(
            operation="generate_structured",
            model="gemini-3.7-flash",
            estimated_cost=Decimal("0.02"),
            metadata={"work_item_id": "section-a"},
        )
    )
    persisted = workspace.repository.get_run(run.id)
    assert persisted is not None and persisted.metadata["cost_limit_exceeded"] is True
    event = workspace.repository.list_events(run.id)[-1][1]
    assert event.data["work_item_id"] == "section-a"

    response = SimpleNamespace(output_text="OK", usage=None, status="completed", id="unused")
    interactions = _Interactions([response])
    gateway = GeminiGateway(
        settings,
        client=SimpleNamespace(interactions=interactions),
        usage_sink=tracker,
    )
    with pytest.raises(GeminiGatewayError, match="cost limit"):
        gateway.health_check()
    assert interactions.responses == [response]


def test_gateway_rechecks_cost_limit_after_waiting_for_a_permit(tmp_path: Path) -> None:
    class CostProbe:
        reached = False

        def __call__(self, _: UsageRecord) -> None:
            return None

        def limit_reached(self) -> bool:
            return self.reached

    class WaitingCoordinator:
        def __init__(self) -> None:
            self.admitted = Event()
            self.release = Event()

        @contextmanager
        def request(self, _: str) -> Iterator[SimpleNamespace]:
            self.admitted.set()
            assert self.release.wait(timeout=2)
            yield SimpleNamespace(throttle_generation=0)

        def succeeded(self, _: SimpleNamespace) -> None:
            return None

    probe = CostProbe()
    coordinator = WaitingCoordinator()
    gateway = GeminiGateway(
        _settings(tmp_path),
        client=SimpleNamespace(),
        usage_sink=probe,
        request_coordinator=cast(ProviderRequestCoordinator, coordinator),
    )
    provider_was_called = Event()

    def issue_request() -> str:
        return cast(
            str,
            gateway._call(
                "queued request",
                lambda: (provider_was_called.set(), "unexpected")[1],
            ),
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(issue_request)
        assert coordinator.admitted.wait(timeout=1)
        probe.reached = True
        coordinator.release.set()
        with pytest.raises(GeminiGatewayError, match="cost limit"):
            future.result(timeout=2)

    assert provider_was_called.is_set() is False


def test_fake_gateway_response_queue_is_safe_for_parallel_calls() -> None:
    fake = FakeGeminiGateway()
    for index in range(24):
        fake.enqueue("generate_text", f"result-{index}")

    def generate(index: int) -> str:
        return fake.generate_text(prompt=f"request-{index}", role="writer")

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(generate, range(24)))

    assert sorted(results) == sorted(f"result-{index}" for index in range(24))
    assert len(fake.calls) == 24
