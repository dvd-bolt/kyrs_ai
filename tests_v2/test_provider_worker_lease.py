from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from papercraft.application import ProjectService
from papercraft.config import AppSettings
from papercraft.domain import ProjectBrief
from papercraft.worker import (
    ProviderWorkerAlreadyRunningError,
    ProviderWorkerLease,
    WorkerAction,
    WorkerLease,
)
from papercraft.worker.cli import _execution_leases


def test_provider_worker_lease_is_shared_by_all_projects_in_one_root(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    first = ProviderWorkerLease(projects_root).acquire()
    try:
        assert first.path == (projects_root / ".papercraft-gemini-worker.lock").resolve()
        with pytest.raises(ProviderWorkerAlreadyRunningError, match="Другой проект"):
            ProviderWorkerLease(projects_root).acquire()
        # A different local PaperCraft root is deliberately independent.
        with ProviderWorkerLease(tmp_path / "other-projects"):
            pass
    finally:
        first.release()


def test_provider_worker_lease_rejects_another_process(tmp_path: Path) -> None:
    """The admission must work beyond the current worker process."""

    projects_root = tmp_path / "projects"
    first = ProviderWorkerLease(projects_root).acquire()
    script = """
import sys
from papercraft.worker import ProviderWorkerAlreadyRunningError, ProviderWorkerLease

try:
    lease = ProviderWorkerLease(sys.argv[1]).acquire()
except ProviderWorkerAlreadyRunningError:
    raise SystemExit(0)
else:
    lease.release()
    raise SystemExit(1)
"""
    environment = dict(os.environ)
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    current_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = source_root + (
        os.pathsep + current_pythonpath if current_pythonpath else ""
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(projects_root)],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    finally:
        first.release()

    assert result.returncode == 0, result.stderr


def test_provider_admission_does_not_block_cancel_control_path(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects")
    workspace = ProjectService(settings).create(ProjectBrief(topic="Lease admission"))
    provider_worker = ProviderWorkerLease(settings.projects_root).acquire()
    try:
        with pytest.raises(ProviderWorkerAlreadyRunningError), _execution_leases(
            workspace, WorkerAction.EXECUTE
        ):
            raise AssertionError("a second provider worker must not start")

        # Cancellation must not wait behind either a project worker or the
        # app-wide Gemini worker. It writes the durable cancellation state and
        # lets the active process notice it at its next safe boundary.
        with WorkerLease(workspace.paths.runs / "worker.lock"), _execution_leases(
            workspace, WorkerAction.CANCEL
        ):
            pass
    finally:
        provider_worker.release()


def test_cancel_cli_bypasses_worker_leases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stop request must not be rejected behind the active generator."""

    from papercraft.domain import GenerationRun, RunStatus
    from papercraft.worker import cli

    settings = AppSettings(projects_root=tmp_path / "projects")
    workspace = ProjectService(settings).create(ProjectBrief(topic="Cancel control"))
    run = GenerationRun(project_id=workspace.project.id, status=RunStatus.CANCELLED)
    workspace.repository.save_run(run)
    cancelled: list[str] = []

    class Service:
        def cancel(self, run_id: str) -> GenerationRun:
            cancelled.append(run_id)
            return run

    class UnexpectedLease:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("--cancel must not acquire a worker lease")

    monkeypatch.setattr(
        cli,
        "prepare_autopilot",
        lambda *_args, **_kwargs: SimpleNamespace(run=run, service=Service()),
    )
    monkeypatch.setattr(cli, "WorkerLease", UnexpectedLease)
    monkeypatch.setattr(cli, "ProviderWorkerLease", UnexpectedLease)

    assert (
        cli.main(
            [
                "--project-id",
                workspace.project.id,
                "--projects-root",
                str(settings.projects_root),
                "--run-id",
                run.id,
                "--cancel",
            ]
        )
        == 0
    )
    assert cancelled == [run.id]


def test_cancel_cli_without_gemini_credential_persists_cleanup_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation is durable even when composing Gemini would reject a missing key."""

    from papercraft.domain import GenerationRun, RunStatus
    from papercraft.infrastructure.gemini import GeminiAuthenticationError
    from papercraft.worker import cli

    settings = AppSettings(projects_root=tmp_path / "projects")
    workspace = ProjectService(settings).create(ProjectBrief(topic="Credential-free cancel"))
    run = GenerationRun(
        project_id=workspace.project.id,
        status=RunStatus.RUNNING,
        metadata={"remote_files": [{"name": "files/retain-until-credential"}]},
    )
    workspace.repository.save_run(run)
    original_prepare = cli.prepare_autopilot
    gateway_arguments: list[object | None] = []

    def prepare_without_primary_credential(*args: object, **kwargs: object) -> object:
        gateway = kwargs.get("gateway")
        gateway_arguments.append(gateway)
        if gateway is None:
            raise GeminiAuthenticationError("Gemini API key is not configured")
        return original_prepare(*args, **kwargs)

    monkeypatch.setattr(cli, "prepare_autopilot", prepare_without_primary_credential)

    assert (
        cli.main(
            [
                "--project-id",
                workspace.project.id,
                "--projects-root",
                str(settings.projects_root),
                "--run-id",
                run.id,
                "--cancel",
            ]
        )
        == 0
    )

    persisted = workspace.repository.get_run(run.id)
    assert persisted is not None
    assert persisted.status is RunStatus.CANCELLED
    # Without credentials, remote deletion cannot be truthfully marked done.
    # The normal terminal hook retains the resource for a future authenticated
    # cleanup while the cancellation itself is already terminal and durable.
    assert persisted.metadata["remote_files"] == [{"name": "files/retain-until-credential"}]
    assert persisted.metadata["terminal_cleanup_pending"] is True
    assert persisted.metadata["terminal_cleanup_error_type"] == "StageExecutionError"
    assert "terminal_hook_done" not in persisted.metadata
    assert gateway_arguments[0] is None
    assert isinstance(gateway_arguments[1], cli._DeferredRemoteCleanupGateway)
    events = [event.event_type for _, event in workspace.repository.list_events(run.id)]
    assert events == ["run_cancel_requested", "terminal_cleanup_failed"]
