"""Qt-independent run state transitions used by the desktop interface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from papercraft.domain import GenerationRun, RunEvent, RunStatus


class RunRepository(Protocol):
    def get_run(self, run_id: str) -> GenerationRun | None: ...

    def save_run(self, run: GenerationRun) -> None: ...

    def append_event(self, event: RunEvent) -> int: ...


class RunControlError(RuntimeError):
    pass


@dataclass(slots=True)
class RunController:
    repository: RunRepository

    def require(self, run_id: str) -> GenerationRun:
        run = self.repository.get_run(run_id)
        if run is None:
            raise RunControlError(f"Запуск не найден: {run_id}")
        return run

    def pause(self, run_id: str) -> GenerationRun:
        run = self.require(run_id)
        if run.status not in {
            RunStatus.RUNNING,
            RunStatus.RETRYING,
            RunStatus.WAITING_INPUT,
        }:
            raise RunControlError(f"Запуск в состоянии {run.status.value} нельзя поставить на паузу")
        run.status = RunStatus.PAUSED
        self.repository.save_run(run)
        self.repository.append_event(
            RunEvent(
                run_id=run.id,
                event_type="run_pause_requested",
                message="Пауза запрошена пользователем",
            )
        )
        return run

    def cancel(self, run_id: str) -> GenerationRun:
        run = self.require(run_id)
        if run.status == RunStatus.SUCCEEDED:
            raise RunControlError("Завершённый запуск нельзя отменить")
        if run.status == RunStatus.CANCELLED:
            return run
        run.status = RunStatus.CANCELLED
        run.finished_at = datetime.now(UTC)
        self.repository.save_run(run)
        self.repository.append_event(
            RunEvent(
                run_id=run.id,
                event_type="run_cancel_requested",
                message="Отмена запрошена пользователем",
            )
        )
        return run


__all__ = ["RunControlError", "RunController", "RunRepository"]
