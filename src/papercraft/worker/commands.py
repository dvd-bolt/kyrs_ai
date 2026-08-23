"""Typed command contract shared by the desktop and background worker."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class WorkerAction(StrEnum):
    EXECUTE = "execute"
    RETRY_FROM = "retry_from"
    REBUILD_SECTION = "rebuild_section"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    project_id: str
    projects_root: Path
    run_id: str | None = None
    retry_from: str | None = None
    rebuild_section_id: str | None = None
    acknowledge_checkpoint: bool = False
    cancel: bool = False

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("project_id must not be empty")
        actions = sum(
            value is not None
            for value in (self.retry_from, self.rebuild_section_id)
        ) + int(self.cancel)
        if actions > 1:
            raise ValueError("retry, section rebuild and cancel are mutually exclusive")
        if (actions or self.acknowledge_checkpoint) and not self.run_id:
            raise ValueError("an existing run_id is required for this worker command")
        if self.retry_from is not None and not self.retry_from.strip():
            raise ValueError("retry_from must not be empty")
        if self.rebuild_section_id is not None and not self.rebuild_section_id.strip():
            raise ValueError("rebuild_section_id must not be empty")

    @property
    def action(self) -> WorkerAction:
        if self.retry_from is not None:
            return WorkerAction.RETRY_FROM
        if self.rebuild_section_id is not None:
            return WorkerAction.REBUILD_SECTION
        if self.cancel:
            return WorkerAction.CANCEL
        return WorkerAction.EXECUTE

    def arguments(self) -> list[str]:
        result = [
            "--project-id",
            self.project_id,
            "--projects-root",
            str(self.projects_root),
        ]
        if self.run_id:
            result.extend(["--run-id", self.run_id])
        if self.retry_from is not None:
            result.extend(["--retry-from", self.retry_from])
        if self.rebuild_section_id is not None:
            result.extend(["--rebuild-section", self.rebuild_section_id])
        if self.acknowledge_checkpoint:
            result.append("--acknowledge-checkpoint")
        if self.cancel:
            result.append("--cancel")
        return result


def worker_invocation(
    request: WorkerRequest,
    *,
    executable: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
) -> tuple[str, list[str]]:
    """Resolve the development or PyInstaller worker executable."""

    program = Path(executable or sys.executable).expanduser().resolve()
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not is_frozen:
        return str(program), ["-m", "papercraft.worker.cli", *request.arguments()]
    if not program.is_file():
        raise FileNotFoundError(f"Исполняемый файл PaperCraft не найден: {program}")
    return str(program), ["--papercraft-worker", *request.arguments()]


__all__ = ["WorkerAction", "WorkerRequest", "worker_invocation"]
