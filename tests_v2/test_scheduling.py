from __future__ import annotations

from threading import Barrier, Event, Lock, get_ident
from time import sleep

import pytest

from papercraft.application.scheduling import (
    DependencyGraphError,
    FailurePolicy,
    WorkItem,
    WorkStatus,
    run_dependency_aware,
)


def test_dependencies_finish_before_child_starts_and_callbacks_use_caller_thread() -> None:
    started: list[str] = []
    completed: list[str] = []
    callback_threads: list[int] = []
    caller_thread = get_ident()

    def worker(context: object) -> str:
        # The concrete context is deliberately exercised through the public
        # fields expected by a section/research integration.
        item = context.item  # type: ignore[attr-defined]
        dependency_results = context.dependency_results  # type: ignore[attr-defined]
        started.append(item.id)
        if item.id == "chapter":
            assert dependency_results == {"intro": "INTRO", "method": "METHOD"}
            assert "intro" in completed
            assert "method" in completed
        sleep(0.01)
        return item.payload.upper()

    result = run_dependency_aware(
        [
            WorkItem("method", "method"),
            WorkItem("intro", "intro"),
            WorkItem("chapter", "chapter"),
        ],
        {"chapter": ("intro", "method")},
        worker,
        max_workers=2,
        on_result=lambda record, _progress: (
            completed.append(record.work_item_id),
            callback_threads.append(get_ident()),
        ),
    )

    assert list(result) == ["method", "intro", "chapter"]
    assert [record.work_item_id for record in result.records] == ["method", "intro", "chapter"]
    assert started[-1] == "chapter"
    assert set(completed[:2]) == {"intro", "method"}
    assert callback_threads == [caller_thread] * 3


def test_scheduler_never_exceeds_worker_cap() -> None:
    active = 0
    maximum_active = 0
    lock = Lock()
    pair_barrier = Barrier(2)

    def worker(context: object) -> str:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            pair_barrier.wait(timeout=1)
            sleep(0.01)
            return context.item.id  # type: ignore[attr-defined]
        finally:
            with lock:
                active -= 1

    result = run_dependency_aware(
        [WorkItem(f"item-{index}", index) for index in range(6)],
        {},
        worker,
        max_workers=2,
    )

    assert result.all_succeeded
    assert maximum_active == 2
    assert all(record.status is WorkStatus.SUCCEEDED for record in result.records)


def test_results_and_records_remain_in_input_order_after_reverse_completion() -> None:
    delays = {"first": 0.04, "second": 0.02, "third": 0.0}

    def worker(context: object) -> str:
        sleep(delays[context.item.id])  # type: ignore[attr-defined]
        return context.item.payload  # type: ignore[attr-defined]

    result = run_dependency_aware(
        [WorkItem("first", "1"), WorkItem("second", "2"), WorkItem("third", "3")],
        {},
        worker,
        max_workers=3,
    )

    assert list(result.results) == ["first", "second", "third"]
    assert list(result.results.values()) == ["1", "2", "3"]
    assert [record.work_item_id for record in result.records] == ["first", "second", "third"]


def test_failure_blocks_dependents_but_keeps_independent_work_for_resume() -> None:
    called: list[str] = []

    def worker(context: object) -> str:
        item = context.item  # type: ignore[attr-defined]
        called.append(item.id)
        if item.id == "broken":
            raise LookupError("source unavailable")
        return item.payload

    result = run_dependency_aware(
        [
            WorkItem("broken", "broken"),
            WorkItem("dependent", "dependent"),
            WorkItem("independent", "independent"),
        ],
        {"dependent": ("broken",)},
        worker,
        max_workers=2,
    )

    by_id = result.records_by_id
    assert by_id["broken"].status is WorkStatus.FAILED
    assert by_id["broken"].error_type == "LookupError"
    assert by_id["dependent"].status is WorkStatus.BLOCKED
    assert by_id["dependent"].blocked_by == ("broken",)
    assert by_id["independent"].status is WorkStatus.SUCCEEDED
    assert "dependent" not in called
    assert list(result) == ["independent"]


def test_fail_fast_cancels_unstarted_independent_work() -> None:
    started = Event()
    release = Event()

    def worker(context: object) -> str:
        if context.item.id == "broken":  # type: ignore[attr-defined]
            started.set()
            raise RuntimeError("cannot continue")
        release.wait(timeout=1)
        return context.item.payload  # type: ignore[attr-defined]

    result = run_dependency_aware(
        [WorkItem("broken", "broken"), WorkItem("later", "later")],
        {},
        worker,
        max_workers=1,
        failure_policy=FailurePolicy.FAIL_FAST,
    )

    assert started.is_set()
    assert result.records_by_id["broken"].status is WorkStatus.FAILED
    assert result.records_by_id["later"].status is WorkStatus.CANCELLED


def test_cancellation_stops_new_work_and_records_completed_work() -> None:
    cancel = Event()
    callback_ids: list[str] = []

    def worker(context: object) -> str:
        if context.item.id == "first":  # type: ignore[attr-defined]
            cancel.set()
            return "saved-before-cancel"
        pytest.fail("scheduler started work after cancellation")

    result = run_dependency_aware(
        [WorkItem("first", 1), WorkItem("second", 2), WorkItem("third", 3)],
        {},
        worker,
        max_workers=1,
        cancellation_requested=cancel.is_set,
        on_result=lambda record, _progress: callback_ids.append(record.work_item_id),
    )

    assert result.cancellation_requested
    assert result.records_by_id["first"].status is WorkStatus.SUCCEEDED
    assert result.records_by_id["second"].status is WorkStatus.CANCELLED
    assert result.records_by_id["third"].status is WorkStatus.CANCELLED
    assert callback_ids == ["first", "second", "third"]


def test_admission_stop_drains_a_billable_worker_without_cancelling_it() -> None:
    stop_admission = Event()
    completed: list[str] = []

    def worker(context: object) -> str:
        if context.item.id == "first":  # type: ignore[attr-defined]
            # Model a cost tracker firing after a provider response. The
            # in-flight worker must still be allowed to return that response.
            stop_admission.set()
            context.check_cancelled()  # type: ignore[attr-defined]
            return "checkpoint-this"
        pytest.fail("scheduler started work after the admission stop")

    result = run_dependency_aware(
        [WorkItem("first", 1), WorkItem("second", 2)],
        {},
        worker,
        max_workers=1,
        admission_stop_requested=stop_admission.is_set,
        on_result=lambda record, _progress: completed.append(record.work_item_id),
    )

    assert result.admission_stopped
    assert not result.cancellation_requested
    assert result.records_by_id["first"].status is WorkStatus.SUCCEEDED
    assert result.records_by_id["second"].status is WorkStatus.CANCELLED
    assert completed == ["first", "second"]


def test_invalid_dependency_graphs_are_rejected_before_any_worker_starts() -> None:
    with pytest.raises(DependencyGraphError, match="cycle"):
        run_dependency_aware(
            [WorkItem("first", 1), WorkItem("second", 2)],
            {"first": ("second",), "second": ("first",)},
            lambda _context: "unreachable",
        )
