from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import cast

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
from papercraft.infrastructure.gemini import GeminiAuthenticationError, GeminiPort

from .commands import WorkerAction
from .lease import ProviderWorkerLease, WorkerLease


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PaperCraft AI background worker")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--projects-root", type=Path)
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--retry-from", choices=[stage.value for stage in PipelineStage])
    operations.add_argument("--rebuild-section")
    operations.add_argument("--refresh-research", action="store_true")
    operations.add_argument("--cancel", action="store_true")
    parser.add_argument("--acknowledge-checkpoint", action="store_true")
    return parser


def _action(arguments: argparse.Namespace) -> WorkerAction:
    if arguments.retry_from:
        return WorkerAction.RETRY_FROM
    if arguments.rebuild_section:
        return WorkerAction.REBUILD_SECTION
    if arguments.refresh_research:
        return WorkerAction.REFRESH_RESEARCH
    if arguments.cancel:
        return WorkerAction.CANCEL
    return WorkerAction.EXECUTE


def _validate(arguments: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    needs_run = bool(
        arguments.retry_from
        or arguments.rebuild_section
        or arguments.refresh_research
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
    if arguments.refresh_research:
        return runtime.service.refresh_research(run_id)
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
    current = workspace.repository.get_run(run_id)
    if current is not None and current.status in {
        RunStatus.PAUSED,
        RunStatus.FAILED,
        RunStatus.WAITING_INPUT,
    }:
        return runtime.service.resume(run_id)
    return runtime.service.execute(run_id)


class _DeferredRemoteCleanupGateway:
    """Keep provider-owned files durable when cancellation has no credential.

    A cancellation must always be able to write the terminal run state.  It
    normally also invokes the standard terminal hook, which removes Gemini
    Files resources.  Pretending those deletes succeeded without a configured
    credential would lose the durable retry record, so this narrow adapter
    makes the existing hook retain them as pending cleanup instead.

    The object is only supplied to ``prepare_autopilot`` for ``--cancel``
    after production Gemini composition has explicitly reported that no
    credential is available.  No generation handler runs on this path.
    """

    def delete_file(self, _name: str) -> None:
        raise GeminiAuthenticationError("Gemini credentials are unavailable for remote cleanup")


def _prepare_runtime(
    settings: AppSettings,
    workspace: ProjectWorkspace,
    *,
    run_id: str | None,
    action: WorkerAction,
) -> AutopilotRuntime:
    """Compose the normal runtime, with a credential-free cancellation fallback."""

    if action is not WorkerAction.CANCEL:
        return prepare_autopilot(settings, workspace, run_id=run_id)
    try:
        return prepare_autopilot(settings, workspace, run_id=run_id)
    except GeminiAuthenticationError:
        # Construct the usual stage factory and terminal hook without giving
        # it a fake successful delete operation.  ``cancel`` persists the
        # cancellation first; any provider resource is retained as a durable
        # cleanup-pending record for the next credentialed cancellation.
        return prepare_autopilot(
            settings,
            workspace,
            run_id=run_id,
            gateway=cast(GeminiPort, _DeferredRemoteCleanupGateway()),
        )


@contextmanager
def _execution_leases(
    workspace: ProjectWorkspace,
    action: WorkerAction,
) -> Iterator[None]:
    """Acquire the conservative worker admissions for a provider operation.

    A cancellation is a durable control-path operation.  It must remain able
    to update the run while a worker is executing or another project holds the
    app-wide Gemini lease, so it deliberately bypasses both worker locks.
    """

    if action is WorkerAction.CANCEL:
        yield
        return
    with ExitStack() as stack:
        # Keep the existing same-project guard first so duplicate launches
        # retain their precise user-facing diagnostic.
        stack.enter_context(WorkerLease(workspace.paths.runs / "worker.lock"))
        # Gemini's in-process request coordinator cannot bound calls made by a
        # different project worker.  One root-scoped lease makes the beta
        # policy effective across all local PaperCraft projects.
        stack.enter_context(ProviderWorkerLease(workspace.paths.projects_root))
        yield


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
        action = _action(arguments)
        with _execution_leases(workspace, action):
            runtime = _prepare_runtime(
                settings,
                workspace,
                run_id=arguments.run_id,
                action=action,
            )
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
