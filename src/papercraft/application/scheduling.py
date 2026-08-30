"""Small, dependency-aware scheduling primitives for parallel pipeline work.

The scheduler intentionally knows nothing about repositories, Gemini, or the
desktop UI.  Worker functions run in a bounded thread pool, while lifecycle
callbacks always run on the caller thread.  That split lets a stage persist a
completed section or research item atomically before another dependent item is
started.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import (
    FIRST_COMPLETED,
    CancelledError,
    Future,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic
from types import MappingProxyType
from typing import cast

_CANCELLATION_POLL_SECONDS = 0.1


class SchedulingError(RuntimeError):
    """Base error raised for invalid scheduler inputs or impossible states."""


class DependencyGraphError(SchedulingError):
    """Raised when work-item dependencies reference invalid items or form a cycle."""


class WorkCancelled(RuntimeError):
    """Raised by a worker which observes a cooperative cancellation request."""


class WorkStatus(StrEnum):
    """A terminal status for a single work item."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class FailurePolicy(StrEnum):
    """Whether independent pending work should survive another item's failure."""

    CONTINUE_INDEPENDENT = "continue_independent"
    FAIL_FAST = "fail_fast"


@dataclass(frozen=True, slots=True)
class WorkItem[PayloadT]:
    """One independently executable unit of work.

    ``id`` is stable across retries and is used by the dependencies mapping.
    The scheduler preserves the input sequence in its final records and
    result mapping, rather than relying on thread completion order.
    """

    id: str
    payload: PayloadT

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("work item id must not be blank")


@dataclass(frozen=True, slots=True)
class WorkExecutionContext[PayloadT, ResultT]:
    """Read-only input supplied to a worker running in the pool."""

    item: WorkItem[PayloadT]
    dependency_results: Mapping[str, ResultT]
    _cancellation_requested: Callable[[], bool] = field(repr=False, compare=False)

    @property
    def cancellation_requested(self) -> bool:
        """Return whether the owner has asked this scheduler to stop."""

        return self._cancellation_requested()

    def check_cancelled(self) -> None:
        """Raise :class:`WorkCancelled` when cancellation was requested.

        Long-running workers can call this at safe local checkpoints.  Python
        cannot safely terminate an already running thread, so workers which
        make external requests should use this cooperative boundary.
        """

        if self.cancellation_requested:
            raise WorkCancelled(f"work item {self.item.id!r} was cancelled")

    @property
    def cancellation_probe(self) -> Callable[[], bool]:
        """Return the cooperative cancellation probe for a downstream adapter.

        A worker normally calls :meth:`check_cancelled` at local boundaries.
        An adapter which can block while waiting for an external resource (for
        example, a provider-rate coordinator) can use this probe to avoid
        admitting a request after the run has been paused or cancelled.
        """

        return self._cancellation_requested


@dataclass(frozen=True, slots=True)
class WorkRecord[ResultT]:
    """Safe, in-memory diagnostics for one terminal work item."""

    work_item_id: str
    input_index: int
    status: WorkStatus
    started_at: datetime | None
    finished_at: datetime
    duration_ms: int
    attempts: int
    cache_hit: bool = False
    result: ResultT | None = None
    error_type: str | None = None
    error_message: str | None = None
    blocked_by: tuple[str, ...] = ()
    error: BaseException | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ScheduleProgress:
    """A snapshot supplied with lifecycle callbacks on the caller thread."""

    completed: int
    total: int
    running: int
    pending: int
    cancellation_requested: bool = False
    admission_stopped: bool = False
    succeeded: int = 0

    @property
    def remaining(self) -> int:
        return self.total - self.completed


@dataclass(frozen=True, slots=True)
class ScheduleResult[ResultT](Mapping[str, ResultT]):
    """Terminal scheduler state with successful values exposed as a mapping.

    Iterating this object (or its :attr:`results`) yields successful work item
    IDs in *input* order, even when workers completed in a different order.
    All outcomes, including failures and dependency blocks, stay available in
    :attr:`records` for durable checkpointing by the caller.
    """

    records: tuple[WorkRecord[ResultT], ...]
    cancellation_requested: bool = False
    admission_stopped: bool = False

    @property
    def results(self) -> Mapping[str, ResultT]:
        values: OrderedDict[str, ResultT] = OrderedDict()
        for record in self.records:
            if record.status is WorkStatus.SUCCEEDED:
                values[record.work_item_id] = cast(ResultT, record.result)
        return MappingProxyType(values)

    @property
    def records_by_id(self) -> Mapping[str, WorkRecord[ResultT]]:
        return MappingProxyType({record.work_item_id: record for record in self.records})

    @property
    def failures(self) -> tuple[WorkRecord[ResultT], ...]:
        return tuple(record for record in self.records if record.status is WorkStatus.FAILED)

    @property
    def all_succeeded(self) -> bool:
        return not self.cancellation_requested and not self.admission_stopped and all(
            record.status is WorkStatus.SUCCEEDED for record in self.records
        )

    def __getitem__(self, work_item_id: str) -> ResultT:
        return self.results[work_item_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)


def run_dependency_aware[PayloadT, ResultT](
    items: Sequence[WorkItem[PayloadT]],
    dependencies: Mapping[str, Iterable[str]] | None,
    worker: Callable[[WorkExecutionContext[PayloadT, ResultT]], ResultT],
    *,
    max_workers: int = 3,
    cancellation_requested: Callable[[], bool] | None = None,
    admission_stop_requested: Callable[[], bool] | None = None,
    on_started: Callable[[WorkItem[PayloadT], ScheduleProgress], None] | None = None,
    on_result: Callable[[WorkRecord[ResultT], ScheduleProgress], None] | None = None,
    failure_policy: FailurePolicy = FailurePolicy.CONTINUE_INDEPENDENT,
) -> ScheduleResult[ResultT]:
    """Execute dependency-aware work in a bounded thread pool.

    ``dependencies`` maps a work item ID to the IDs which must successfully
    finish before it can run.  Every callback is deliberately invoked by the
    calling thread, never a worker thread.  This makes ``on_result`` a safe
    place to persist a finished item's checkpoint/artifact before a dependent
    item is submitted.

    Cancellation is cooperative: once ``cancellation_requested`` returns
    true, the scheduler submits no further work and marks never-started work
    as cancelled.  Already-running workers receive the same signal through
    :class:`WorkExecutionContext`; they may call ``check_cancelled`` at safe
    boundaries.  Running workers are allowed to finish so their completed
    result can still be durably recorded by ``on_result``.

    ``admission_stop_requested`` is a softer circuit-breaker for conditions
    such as a cost cap. It prevents new work from starting without making an
    already-running worker observe cancellation, so a billable response can
    still be returned and checkpointed.

    A failed or cancelled prerequisite blocks its dependents.  With the
    default ``CONTINUE_INDEPENDENT`` policy unrelated work continues; use
    ``FAIL_FAST`` to cancel all remaining independent pending work after the
    first failure.
    """

    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")

    ordered_items = tuple(items)
    item_by_id = _validate_items(ordered_items)
    ordered_ids = tuple(item.id for item in ordered_items)
    input_index = {item_id: index for index, item_id in enumerate(ordered_ids)}
    normalized_dependencies = _normalize_dependencies(item_by_id, dependencies)
    _assert_acyclic(ordered_ids, normalized_dependencies)

    if not ordered_items:
        return ScheduleResult(records=())

    cancel_probe = cancellation_requested or (lambda: False)
    admission_probe = admission_stop_requested or cancel_probe
    records: dict[str, WorkRecord[ResultT]] = {}
    outputs: dict[str, ResultT] = {}
    pending: set[str] = set(ordered_ids)
    running: dict[Future[ResultT], str] = {}
    started_at: dict[str, datetime] = {}
    started_monotonic: dict[str, float] = {}
    saw_cancellation = False
    saw_admission_stop = False

    def progress() -> ScheduleProgress:
        return ScheduleProgress(
            completed=len(records),
            total=len(ordered_ids),
            running=len(running),
            pending=len(pending),
            cancellation_requested=saw_cancellation or cancel_probe(),
            admission_stopped=saw_admission_stop or admission_probe(),
            succeeded=sum(
                record.status is WorkStatus.SUCCEEDED for record in records.values()
            ),
        )

    def finalize(record: WorkRecord[ResultT]) -> None:
        if record.work_item_id in records:
            raise SchedulingError(f"work item recorded twice: {record.work_item_id}")
        records[record.work_item_id] = record
        if on_result is not None:
            on_result(record, progress())

    def finalize_cancelled(work_item_id: str, message: str) -> None:
        pending.discard(work_item_id)
        now = datetime.now(UTC)
        finalize(
            WorkRecord(
                work_item_id=work_item_id,
                input_index=input_index[work_item_id],
                status=WorkStatus.CANCELLED,
                started_at=None,
                finished_at=now,
                duration_ms=0,
                attempts=0,
                error_type=WorkCancelled.__name__,
                error_message=message,
            )
        )

    def finalize_blocked(work_item_id: str, blocked_by: tuple[str, ...]) -> None:
        pending.discard(work_item_id)
        now = datetime.now(UTC)
        finalize(
            WorkRecord(
                work_item_id=work_item_id,
                input_index=input_index[work_item_id],
                status=WorkStatus.BLOCKED,
                started_at=None,
                finished_at=now,
                duration_ms=0,
                attempts=0,
                error_type="DependencyFailed",
                error_message="one or more dependencies did not succeed",
                blocked_by=blocked_by,
            )
        )

    def block_unrunnable_pending() -> None:
        """Propagate terminal prerequisite failures through the dependency graph."""

        changed = True
        while changed:
            changed = False
            for work_item_id in ordered_ids:
                if work_item_id not in pending:
                    continue
                blocked_by = tuple(
                    dependency_id
                    for dependency_id in normalized_dependencies[work_item_id]
                    if dependency_id in records
                    and records[dependency_id].status is not WorkStatus.SUCCEEDED
                )
                if blocked_by:
                    finalize_blocked(work_item_id, blocked_by)
                    changed = True

    def submit_ready(executor: ThreadPoolExecutor) -> None:
        def run_worker(execution: WorkExecutionContext[PayloadT, ResultT]) -> ResultT:
            # This boundary closes the race between scheduling a future and
            # the worker function beginning.  Stage workers retain their own
            # finer-grained checkpoints around expensive work.
            execution.check_cancelled()
            return worker(execution)

        while len(running) < max_workers:
            # A pause can arrive while this caller is walking the ready queue.
            # Do not submit another future in that window; the outer loop will
            # record the still-pending item as cancelled/admission-stopped.
            if cancel_probe() or admission_probe():
                return
            next_id = next(
                (
                    work_item_id
                    for work_item_id in ordered_ids
                    if work_item_id in pending
                    and all(
                        dependency_id in outputs
                        for dependency_id in normalized_dependencies[work_item_id]
                    )
                ),
                None,
            )
            if next_id is None:
                return
            item = item_by_id[next_id]
            dependency_outputs = MappingProxyType(
                {
                    dependency_id: outputs[dependency_id]
                    for dependency_id in normalized_dependencies[next_id]
                }
            )
            execution = WorkExecutionContext(
                item=item,
                dependency_results=dependency_outputs,
                _cancellation_requested=cancel_probe,
            )
            started_at[next_id] = datetime.now(UTC)
            started_monotonic[next_id] = monotonic()
            pending.remove(next_id)
            future = executor.submit(run_worker, execution)
            running[future] = next_id
            if on_started is not None:
                on_started(item, progress())

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="papercraft-work") as executor:
        while pending or running:
            if cancel_probe():
                saw_cancellation = True
                for work_item_id in ordered_ids:
                    if work_item_id in pending:
                        finalize_cancelled(work_item_id, "scheduler cancellation was requested")
            elif admission_probe():
                # Do not interrupt a running worker. It can have a provider
                # response which is already billable and needs the caller's
                # on_result callback to persist it before the stage stops.
                saw_admission_stop = True
                if not running:
                    for work_item_id in ordered_ids:
                        if work_item_id in pending:
                            finalize_cancelled(
                                work_item_id,
                                "scheduler stopped admitting new work",
                            )
            elif failure_policy is FailurePolicy.FAIL_FAST and any(
                record.status is WorkStatus.FAILED for record in records.values()
            ):
                block_unrunnable_pending()
                for work_item_id in ordered_ids:
                    if work_item_id in pending:
                        finalize_cancelled(work_item_id, "scheduler stopped after another work item failed")
            else:
                block_unrunnable_pending()
                submit_ready(executor)

            if not running:
                if pending:
                    # ``submit_ready`` can intentionally decline to add work
                    # when a lifecycle signal arrived in its small admission
                    # window.  Re-enter the outer loop so that signal is
                    # converted to durable terminal records instead of being
                    # misreported as a graph invariant violation.
                    if cancel_probe() or admission_probe():
                        continue
                    # Validation rejects cycles, so reaching this state means a
                    # caller violated a scheduler invariant rather than needing
                    # a busy wait.
                    raise SchedulingError("no runnable work items remain")
                continue

            # A bounded wait lets the scheduler observe a pause even when all
            # active workers are themselves waiting on an external admission
            # gate.  It cannot kill a running thread, but it promptly stops
            # scheduler-level admission and gives cancel-aware adapters a
            # fresh chance to return before they issue a paid request.
            done, _ = wait(
                tuple(running),
                timeout=_CANCELLATION_POLL_SECONDS,
                return_when=FIRST_COMPLETED,
            )
            for future in sorted(done, key=lambda candidate: input_index[running[candidate]]):
                work_item_id = running.pop(future)
                finished_at = datetime.now(UTC)
                started = started_at.pop(work_item_id)
                duration_ms = max(0, round((monotonic() - started_monotonic.pop(work_item_id)) * 1000))
                try:
                    result = future.result()
                except (WorkCancelled, CancelledError) as error:
                    saw_cancellation = True
                    finalize(
                        WorkRecord(
                            work_item_id=work_item_id,
                            input_index=input_index[work_item_id],
                            status=WorkStatus.CANCELLED,
                            started_at=started,
                            finished_at=finished_at,
                            duration_ms=duration_ms,
                            attempts=1,
                            error_type=type(error).__name__,
                            error_message=str(error),
                            error=error,
                        )
                    )
                except Exception as error:
                    cancelled_during_work = cancel_probe()
                    if cancelled_during_work:
                        saw_cancellation = True
                    finalize(
                        WorkRecord(
                            work_item_id=work_item_id,
                            input_index=input_index[work_item_id],
                            status=(
                                WorkStatus.CANCELLED
                                if cancelled_during_work
                                else WorkStatus.FAILED
                            ),
                            started_at=started,
                            finished_at=finished_at,
                            duration_ms=duration_ms,
                            attempts=1,
                            error_type=type(error).__name__,
                            error_message=str(error),
                            error=error,
                        )
                    )
                else:
                    outputs[work_item_id] = result
                    finalize(
                        WorkRecord(
                            work_item_id=work_item_id,
                            input_index=input_index[work_item_id],
                            status=WorkStatus.SUCCEEDED,
                            started_at=started,
                            finished_at=finished_at,
                            duration_ms=duration_ms,
                            attempts=1,
                            result=result,
                        )
                    )

    return ScheduleResult(
        records=tuple(records[work_item_id] for work_item_id in ordered_ids),
        cancellation_requested=saw_cancellation or cancel_probe(),
        admission_stopped=saw_admission_stop or admission_probe(),
    )


def _validate_items[PayloadT](items: Sequence[WorkItem[PayloadT]]) -> dict[str, WorkItem[PayloadT]]:
    item_by_id: dict[str, WorkItem[PayloadT]] = {}
    for item in items:
        if item.id in item_by_id:
            raise DependencyGraphError(f"duplicate work item id: {item.id}")
        item_by_id[item.id] = item
    return item_by_id


def _normalize_dependencies[PayloadT](
    item_by_id: Mapping[str, WorkItem[PayloadT]],
    dependencies: Mapping[str, Iterable[str]] | None,
) -> dict[str, tuple[str, ...]]:
    supplied = dependencies or {}
    unknown_items = set(supplied) - set(item_by_id)
    if unknown_items:
        raise DependencyGraphError(f"dependencies supplied for unknown work items: {sorted(unknown_items)}")
    normalized: dict[str, tuple[str, ...]] = {}
    for work_item_id in item_by_id:
        raw_dependencies = supplied.get(work_item_id, ())
        if isinstance(raw_dependencies, str):
            raise DependencyGraphError(
                f"dependencies for {work_item_id!r} must be an iterable of IDs, not a string"
            )
        try:
            values = tuple(raw_dependencies)
        except TypeError as error:
            raise DependencyGraphError(
                f"dependencies for {work_item_id!r} must be iterable"
            ) from error
        if len(values) != len(set(values)):
            raise DependencyGraphError(f"dependencies for {work_item_id!r} contain duplicates")
        if work_item_id in values:
            raise DependencyGraphError(f"work item {work_item_id!r} cannot depend on itself")
        unknown_dependencies = set(values) - set(item_by_id)
        if unknown_dependencies:
            raise DependencyGraphError(
                f"work item {work_item_id!r} depends on unknown items: {sorted(unknown_dependencies)}"
            )
        normalized[work_item_id] = values
    return normalized


def _assert_acyclic(
    ordered_ids: Sequence[str], dependencies: Mapping[str, tuple[str, ...]]
) -> None:
    remaining = {work_item_id: set(dependencies[work_item_id]) for work_item_id in ordered_ids}
    while remaining:
        ready = [work_item_id for work_item_id in ordered_ids if work_item_id in remaining and not remaining[work_item_id]]
        if not ready:
            raise DependencyGraphError("work item dependency graph contains a cycle")
        for work_item_id in ready:
            remaining.pop(work_item_id)
        for prerequisite_ids in remaining.values():
            prerequisite_ids.difference_update(ready)


__all__ = [
    "DependencyGraphError",
    "FailurePolicy",
    "ScheduleProgress",
    "ScheduleResult",
    "SchedulingError",
    "WorkCancelled",
    "WorkExecutionContext",
    "WorkItem",
    "WorkRecord",
    "WorkStatus",
    "run_dependency_aware",
]
