from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from papercraft.application import (
    AutopilotRuntime,
    DocumentService,
    PipelineStage,
    ProjectService,
    ProjectWorkspace,
    prepare_autopilot,
)
from papercraft.config import AppSettings
from papercraft.domain import GenerationRun, RunStatus

from .commands import WorkerAction
from .lease import WorkerLease


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PaperCraft AI background worker")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--projects-root", type=Path)
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--retry-from", choices=[stage.value for stage in PipelineStage])
    operations.add_argument("--rebuild-section")
    operations.add_argument("--cancel", action="store_true")
    parser.add_argument("--acknowledge-checkpoint", action="store_true")
    return parser


def _action(arguments: argparse.Namespace) -> WorkerAction:
    if arguments.retry_from:
        return WorkerAction.RETRY_FROM
    if arguments.rebuild_section:
        return WorkerAction.REBUILD_SECTION
    if arguments.cancel:
        return WorkerAction.CANCEL
    return WorkerAction.EXECUTE


def _validate(arguments: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    needs_run = bool(
        arguments.retry_from
        or arguments.rebuild_section
        or arguments.cancel
        or arguments.acknowledge_checkpoint
    )
    if needs_run and not arguments.run_id:
        parser.error("--run-id is required for retry, rebuild, cancel and checkpoint approval")


def _dispatch(
    arguments: argparse.Namespace,
    runtime: AutopilotRuntime,
    workspace: ProjectWorkspace,
) -> GenerationRun:
    """Dispatch one validated operation; separated for deterministic tests."""

    run_id = runtime.run.id
    if arguments.cancel:
        return runtime.service.cancel(run_id)
    if arguments.rebuild_section:
        return DocumentService(workspace.project.id, workspace.repository).rebuild_section(
            runtime.service,
            run_id,
            str(arguments.rebuild_section),
        )
    if arguments.retry_from:
        return runtime.service.retry_from(run_id, PipelineStage(arguments.retry_from))
    if arguments.acknowledge_checkpoint:
        current = workspace.repository.get_run(run_id)
        if current is None or current.current_stage is None:
            raise RuntimeError("The run is not waiting at a named checkpoint")
        return runtime.service.acknowledge_checkpoint(
            run_id,
            PipelineStage(current.current_stage),
        )
    return runtime.service.execute(run_id)


def _emit(payload: dict[str, object], *, error: bool = False) -> None:
    print(
        json.dumps(payload, ensure_ascii=False),
        file=sys.stderr if error else sys.stdout,
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    _validate(arguments, parser)
    settings = AppSettings.from_environment()
    if arguments.projects_root:
        settings.projects_root = arguments.projects_root.expanduser().resolve()
    try:
        workspace = ProjectService(settings).open(arguments.project_id)
        with WorkerLease(workspace.paths.runs / "worker.lock"):
            runtime = prepare_autopilot(settings, workspace, run_id=arguments.run_id)
            action = _action(arguments)
            _emit({"event": "run_ready", "run_id": runtime.run.id, "action": action.value})
            run = _dispatch(arguments, runtime, workspace)
        _emit(
            {
                "event": "run_finished",
                "run_id": run.id,
                "action": action.value,
                "status": run.status.value,
                "cost": str(run.cost),
                "error": run.error,
            }
        )
        successful = {
            RunStatus.SUCCEEDED,
            RunStatus.WAITING_INPUT,
            RunStatus.PAUSED,
            RunStatus.CANCELLED,
        }
        return 0 if run.status in successful else 1
    except Exception as exc:
        _emit(
            {"event": "worker_error", "error": f"{type(exc).__name__}: {exc}"},
            error=True,
        )
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
