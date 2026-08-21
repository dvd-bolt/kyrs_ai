"""Cooperative worker controls kept independent from any GUI toolkit."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from papercraft.domain import GenerationRun, RunStatus, StageRun, StageStatus

from .ports import RepositoryPort


class RunCancelled(RuntimeError):
    """Raised by a cooperative stage checkpoint after a cancel request."""


@dataclass(frozen=True, slots=True)
class StageProgress:
    current: int
    total: int
    message: str = ""

    def __post_init__(self) -> None:
        if self.current < 0 or self.total < 0 or (self.total and self.current > self.total):
            raise ValueError("invalid stage progress")


class CancellationToken:
    """Reads durable run state at internal stage boundaries."""

    def __init__(self, repository: RepositoryPort, run_id: str, stage_id: str) -> None:
        self.repository = repository
        self.run_id = run_id
        self.stage_id = stage_id

    def checkpoint(self, progress: StageProgress | None = None) -> None:
        run = self.repository.get_run(self.run_id)
        if run is None or run.status == RunStatus.CANCELLED:
            raise RunCancelled("run was cancelled")
        stage = self.repository.get_stage(self.stage_id)
        if stage is None:
            raise RunCancelled("stage no longer exists")
        stage.heartbeat_at = datetime.now(UTC)
        if progress is not None:
            stage.progress_current = progress.current
            stage.progress_total = progress.total
            stage.checkpoint["progress_message"] = progress.message
        self.repository.save_stage(stage)
        if run.status == RunStatus.PAUSED:
            raise RunCancelled("run was paused")


class StageDependencyGraph:
    """Directed dependency graph used for precise retry invalidation."""

    def __init__(self, dependencies: dict[str, set[str]]) -> None:
        self.dependencies = {name: set(items) for name, items in dependencies.items()}
        known = set(self.dependencies)
        if any(not values <= known for values in self.dependencies.values()):
            raise ValueError("dependency references an unknown stage")
        self._assert_acyclic()

    @classmethod
    def linear(cls, stages: tuple[str, ...]) -> StageDependencyGraph:
        return cls({name: ({stages[index - 1]} if index else set()) for index, name in enumerate(stages)})

    def affected_by(self, stage: str) -> set[str]:
        if stage not in self.dependencies:
            raise KeyError(stage)
        reverse: dict[str, set[str]] = defaultdict(set)
        for child, parents in self.dependencies.items():
            for parent in parents:
                reverse[parent].add(child)
        affected = {stage}
        queue: deque[str] = deque([stage])
        while queue:
            parent = queue.popleft()
            for child in reverse[parent]:
                if child not in affected:
                    affected.add(child)
                    queue.append(child)
        return affected

    def _assert_acyclic(self) -> None:
        remaining = {name: set(items) for name, items in self.dependencies.items()}
        while remaining:
            ready = {name for name, dependencies in remaining.items() if not dependencies}
            if not ready:
                raise ValueError("stage dependency graph contains a cycle")
            for name in ready:
                remaining.pop(name)
            for dependencies in remaining.values():
                dependencies.difference_update(ready)


def recover_stale_stages(
    repository: RepositoryPort, run: GenerationRun, *, lease_timeout: timedelta = timedelta(minutes=5)
) -> list[StageRun]:
    """Return abandoned running stages to queued state without replaying outputs."""

    cutoff = datetime.now(UTC) - lease_timeout
    recovered: list[StageRun] = []
    for stage in repository.list_stages(run.id):
        if stage.status != StageStatus.RUNNING or (stage.heartbeat_at and stage.heartbeat_at >= cutoff):
            continue
        stage.status = StageStatus.QUEUED
        stage.failure_code = "stale_lease_recovered"
        stage.failure_details = {"heartbeat_at": stage.heartbeat_at.isoformat() if stage.heartbeat_at else ""}
        stage.error = "worker lease became stale; stage will resume from durable checkpoint"
        repository.save_stage(stage)
        recovered.append(stage)
    return recovered
