from __future__ import annotations

import json
from pathlib import Path

import pytest

from papercraft.application import ProjectService
from papercraft.application.api import DesktopApplication, Money, RunSnapshot, WorkerRequest
from papercraft.config import AppSettings
from papercraft.domain import GenerationRun, ProjectBrief, RunStatus
from papercraft.worker.protocol import JsonlWorker


def _snapshot(run: GenerationRun) -> RunSnapshot:
    return RunSnapshot(
        id=run.id,
        project_id=run.project_id,
        status="RUNNING",
        progress=0,
        actual_cost=Money(amount="0", currency="USD"),
        can_pause=True,
        can_resume=False,
        can_cancel=True,
        can_retry=False,
    )


def test_desktop_application_creates_versioned_project_and_run(tmp_path: Path) -> None:
    application = DesktopApplication(AppSettings(projects_root=tmp_path / "projects"))

    project = application.create_project({"topic": "Facade boundary", "work_type": "coursework"})
    run = application.start_generation(project["id"])

    assert project["api_version"] == 1
    assert run.api_version == 1
    assert run.project_id == project["id"]
    assert run.status == "RUNNING"


def test_jsonl_worker_replays_identical_request_and_rejects_conflict(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects")
    workspace = ProjectService(settings).create(ProjectBrief(topic="Worker protocol"))
    run = GenerationRun(project_id=workspace.project.id, status=RunStatus.QUEUED)
    workspace.repository.save_run(run)

    class StubApplication:
        def __init__(self, _settings: AppSettings) -> None:
            self._projects = ProjectService(settings)
            self.calls = 0

        def execute_worker_request(self, request: WorkerRequest) -> RunSnapshot:
            self.calls += 1
            assert request.run_id == run.id
            return _snapshot(run)

    stub = StubApplication(settings)
    worker = JsonlWorker(settings, application_factory=lambda _settings: stub)  # type: ignore[arg-type]
    payload = {
        "protocol_version": 1,
        "request_id": "request-1",
        "action": "pause_generation",
        "project_id": workspace.project.id,
        "run_id": run.id,
        "stage_id": None,
        "section_id": None,
    }

    first = worker.handle(payload)
    replay = worker.handle(payload)
    conflict = worker.handle({**payload, "action": "cancel_generation"})

    assert stub.calls == 1
    assert [event.sequence for event in first] == sorted(event.sequence for event in first)
    assert [event.model_dump(mode="json") for event in replay] == [
        event.model_dump(mode="json") for event in first
    ]
    assert conflict[0].event_type == "request_failed"
    assert conflict[0].error_code == "REQUEST_ID_CONFLICT"


@pytest.mark.parametrize(
    "payload",
    [
        {"protocol_version": 1},
        {
            "protocol_version": 1,
            "request_id": "bad",
            "action": "start_generation",
            "project_id": "p",
            "run_id": "must-be-null",
            "stage_id": None,
            "section_id": None,
        },
    ],
)
def test_worker_request_rejects_corrupted_or_invalid_command(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        WorkerRequest.model_validate(payload)


def test_jsonl_cli_rejects_corrupted_line(monkeypatch: pytest.MonkeyPatch) -> None:
    from io import StringIO

    from papercraft.worker import cli

    monkeypatch.setattr(cli.sys, "stdin", StringIO("{not-json}\n"))
    assert cli.jsonl_main() == 2


def test_jsonl_cli_emits_safe_event_when_request_id_is_recoverable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from io import StringIO

    from papercraft.worker import cli

    monkeypatch.setattr(
        cli.sys,
        "stdin",
        StringIO('{"protocol_version": 1, "request_id": "known", "project_id": "project"}\n'),
    )
    assert cli.jsonl_main() == 1
    event = json.loads(capsys.readouterr().out)
    assert event["event_type"] == "request_failed"
    assert event["error_code"] == "VALIDATION_ERROR"


def test_terminal_outcome_is_json_safe(tmp_path: Path) -> None:
    """The request cache stores event DTOs, never tracebacks or domain objects."""

    settings = AppSettings(projects_root=tmp_path / "projects")
    workspace = ProjectService(settings).create(ProjectBrief(topic="Safe outcome"))
    run = GenerationRun(project_id=workspace.project.id, status=RunStatus.QUEUED)
    workspace.repository.save_run(run)

    class FailingApplication:
        def __init__(self, _settings: AppSettings) -> None:
            self._projects = ProjectService(settings)

        def execute_worker_request(self, _request: WorkerRequest) -> RunSnapshot:
            raise RuntimeError("secret prompt must not reach the event")

    worker = JsonlWorker(settings, application_factory=FailingApplication)  # type: ignore[arg-type]
    events = worker.handle(
        {
            "protocol_version": 1,
            "request_id": "failure-1",
            "action": "pause_generation",
            "project_id": workspace.project.id,
            "run_id": run.id,
            "stage_id": None,
            "section_id": None,
        }
    )
    cached = workspace.repository.get_worker_request("failure-1")

    assert events[0].message == "Worker request could not be completed."
    assert cached is not None and cached[2] is not None
    assert "secret prompt" not in cached[2]
    assert json.loads(cached[2])[0]["event_type"] == "request_failed"
