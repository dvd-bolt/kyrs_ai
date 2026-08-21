from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from papercraft.application import PipelineStage, ProjectService
from papercraft.config import AppSettings
from papercraft.domain import (
    GenerationRun,
    Outline,
    ProjectBlueprint,
    ProjectBrief,
    RunStatus,
    SectionSpec,
)
from papercraft.ui.run_control import RunControlError, RunController
from papercraft.worker import (
    WorkerAction,
    WorkerAlreadyRunningError,
    WorkerLease,
    WorkerRequest,
    worker_invocation,
)
from papercraft.worker.cli import _action, _parser


def test_worker_request_builds_exclusive_commands(tmp_path: Path) -> None:
    execute = WorkerRequest(project_id="project", projects_root=tmp_path)
    assert execute.action is WorkerAction.EXECUTE
    assert execute.arguments() == [
        "--project-id",
        "project",
        "--projects-root",
        str(tmp_path),
    ]

    retry = WorkerRequest(
        project_id="project",
        projects_root=tmp_path,
        run_id="run",
        retry_from=PipelineStage.PLAN.value,
    )
    assert retry.action is WorkerAction.RETRY_FROM
    assert retry.arguments()[-2:] == ["--retry-from", "plan"]
    parsed = _parser().parse_args(retry.arguments())
    assert _action(parsed) is WorkerAction.RETRY_FROM

    section = WorkerRequest(
        project_id="project",
        projects_root=tmp_path,
        run_id="run",
        rebuild_section_id="section-1",
    )
    assert section.action is WorkerAction.REBUILD_SECTION
    assert section.arguments()[-2:] == ["--rebuild-section", "section-1"]

    with pytest.raises(ValueError, match="run_id"):
        WorkerRequest(project_id="project", projects_root=tmp_path, retry_from="plan")
    with pytest.raises(ValueError, match="mutually exclusive"):
        WorkerRequest(
            project_id="project",
            projects_root=tmp_path,
            run_id="run",
            retry_from="plan",
            rebuild_section_id="section",
        )

    program, arguments = worker_invocation(
        retry, executable=tmp_path / "python.exe", frozen=False
    )
    assert program.endswith("python.exe")
    assert arguments[:2] == ["-m", "papercraft.worker.cli"]
    with pytest.raises(FileNotFoundError, match="worker"):
        worker_invocation(retry, executable=tmp_path / "PaperCraft.exe", frozen=True)


def test_worker_lease_prevents_duplicate_project_workers(tmp_path: Path) -> None:
    lock = tmp_path / "worker.lock"
    first = WorkerLease(lock).acquire()
    try:
        with pytest.raises(WorkerAlreadyRunningError):
            WorkerLease(lock).acquire()
    finally:
        first.release()
    with WorkerLease(lock):
        assert lock.exists()


def test_run_controller_persists_pause_cancel_and_events(tmp_path: Path) -> None:
    workspace = ProjectService(AppSettings(projects_root=tmp_path)).create(
        ProjectBrief(topic="UI workflow")
    )
    run = GenerationRun(project_id=workspace.project.id, status=RunStatus.RUNNING)
    workspace.repository.save_run(run)
    controller = RunController(workspace.repository)

    paused = controller.pause(run.id)
    assert paused.status is RunStatus.PAUSED
    assert workspace.repository.get_run(run.id).status is RunStatus.PAUSED  # type: ignore[union-attr]
    cancelled = controller.cancel(run.id)
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.finished_at is not None
    events = [event.event_type for _, event in workspace.repository.list_events(run.id)]
    assert events == ["run_pause_requested", "run_cancel_requested"]

    completed = GenerationRun(project_id=workspace.project.id, status=RunStatus.SUCCEEDED)
    workspace.repository.save_run(completed)
    with pytest.raises(RunControlError, match="нельзя отменить"):
        controller.cancel(completed.id)


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 is not installed")
def test_main_window_has_six_connected_screens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from papercraft.ui.main_window import PAGE_NAMES, MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow(AppSettings(projects_root=tmp_path))
    try:
        assert len(PAGE_NAMES) == 6
        assert window.pages.count() == 6
        assert window.pause_button.text() == "Пауза"
        assert window.retry_stage_combo.count() == len(PipelineStage)
        assert window.result_section_combo.count() == 0

        window.new_topic.setPlainText("Автоматизированная академическая работа")
        window._create_project()
        assert window.workspace is not None
        assert window.pages.currentIndex() == 1
        window.consent_checkbox.setChecked(True)
        window.title_page_fields["university"].setText("Тестовый университет")
        window.title_page_fields["student"].setText("Иванов И.И.")
        assert window._save_project()
        persisted = window.workspace.repository.get_project(window.workspace.project.id)
        assert persisted is not None
        assert persisted.brief.title_page["university"] == "Тестовый университет"

        blueprint = ProjectBlueprint(
            project_id=window.workspace.project.id,
            topic=window.workspace.project.brief.topic,
            outline=Outline(
                sections=[SectionSpec(id="introduction", title="Введение", target_words=500)]
            ),
        )
        window.workspace.repository.save_blueprint(blueprint)
        window._refresh_plan()
        item = window.plan_tree.topLevelItem(0)
        item.setText(0, "Новое введение")
        item.setText(1, "650 слов")
        window._save_plan_edits()
        updated = window.workspace.repository.get_latest_blueprint(window.workspace.project.id)
        assert updated is not None
        assert updated.outline.sections[0].title == "Новое введение"
        assert updated.outline.sections[0].target_words == 650

        run = GenerationRun(project_id=window.workspace.project.id, status=RunStatus.SUCCEEDED)
        window.workspace.repository.save_run(run)
        window.active_run_id = run.id
        window._refresh_results()
        assert window.result_section_combo.currentData() == "introduction"

        requests: list[WorkerRequest] = []
        monkeypatch.setattr(window, "_launch_worker", requests.append)
        window._retry_from_stage(PipelineStage.PLAN)
        assert requests[-1].retry_from == PipelineStage.PLAN.value
        window._rebuild_selected_section()
        assert requests[-1].rebuild_section_id == "introduction"

        run.status = RunStatus.PAUSED
        window.workspace.repository.save_run(run)
        window._resume_run()
        assert requests[-1].run_id == run.id
        run.status = RunStatus.RUNNING
        window.workspace.repository.save_run(run)
        window._pause_run()
        assert window.workspace.repository.get_run(run.id).status is RunStatus.PAUSED  # type: ignore[union-attr]
        window._cancel_run()
        assert requests[-1].cancel is True
    finally:
        window.close()
        application.processEvents()
