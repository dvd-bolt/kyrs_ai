from __future__ import annotations

import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from papercraft.application import PipelineStage, ProjectService
from papercraft.config import AppSettings
from papercraft.domain import (
    Artifact,
    ArtifactKind,
    GenerationRun,
    HeadingBlock,
    Manuscript,
    Outline,
    ParagraphBlock,
    ProjectBlueprint,
    ProjectBrief,
    QAIssue,
    QAReport,
    QASeverity,
    RunStatus,
    SectionSpec,
    StageRun,
    StageStatus,
    TableBlock,
    TableSpec,
)
from papercraft.infrastructure.persistence import sha256_file
from papercraft.ui.run_control import RunControlError, RunController
from papercraft.worker import (
    WorkerAction,
    WorkerAlreadyRunningError,
    WorkerLease,
    WorkerRequest,
    worker_invocation,
)
from papercraft.worker.cli import _action, _parser


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 is not installed")
def test_progress_presentation_uses_durable_progress_and_handles_quota_wait() -> None:
    from papercraft.ui.main_window import _progress_presentation

    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    run = GenerationRun(
        project_id="project",
        status=RunStatus.RUNNING,
        current_stage=PipelineStage.GENERATE_SECTIONS.value,
        started_at=now - timedelta(minutes=5),
    )
    completed = StageRun(
        run_id=run.id,
        name=PipelineStage.PLAN.value,
        order=0,
        status=StageStatus.SUCCEEDED,
        started_at=now - timedelta(minutes=5),
        finished_at=now - timedelta(minutes=4),
    )
    writing = StageRun(
        run_id=run.id,
        name=PipelineStage.GENERATE_SECTIONS.value,
        order=1,
        status=StageStatus.RUNNING,
        started_at=now - timedelta(minutes=2),
        progress_current=3,
        progress_total=8,
        checkpoint={"progress_message": "Подготовлен раздел 3"},
    )

    presentation = _progress_presentation(run, [completed, writing], now=now)

    assert presentation.operation.startswith("Генерация разделов")
    assert presentation.progress == "3 из 8 · этапы: 1 из 2"
    assert presentation.elapsed == "5 мин"
    assert presentation.eta.startswith("≈ ")
    assert presentation.quota_message is None

    writing.checkpoint = {
        "progress_message": "HTTP 429 rate limit",
        "retry_after_seconds": 90,
    }
    quota = _progress_presentation(run, [completed, writing], now=now)
    assert quota.eta == "ожидание доступа к Gemini"
    assert (
        quota.quota_message
        == "Gemini временно ограничил запросы. Повтор примерно через 1 мин 30 с."
    )

    empty = _progress_presentation(
        GenerationRun(
            project_id="project",
            status=RunStatus.QUEUED,
            current_stage=PipelineStage.INGEST.value,
        ),
        [
            StageRun(
                run_id="run",
                name=PipelineStage.INGEST.value,
                checkpoint={"completed_items": {"one": {}, "two": {}}, "total_items": 4},
            )
        ],
        now=now,
    )
    assert empty.progress == "2 из 4 · этапы: 0 из 1"
    assert empty.elapsed == "—"
    assert empty.eta == "рассчитываем…"


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

    refresh_research = WorkerRequest(
        project_id="project",
        projects_root=tmp_path,
        run_id="run",
        refresh_research=True,
    )
    assert refresh_research.action is WorkerAction.REFRESH_RESEARCH
    assert refresh_research.arguments()[-1] == "--refresh-research"
    parsed = _parser().parse_args(refresh_research.arguments())
    assert _action(parsed) is WorkerAction.REFRESH_RESEARCH

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
    with pytest.raises(ValueError, match="mutually exclusive"):
        WorkerRequest(
            project_id="project",
            projects_root=tmp_path,
            run_id="run",
            refresh_research=True,
            cancel=True,
        )

    program, arguments = worker_invocation(retry, executable=tmp_path / "python.exe", frozen=False)
    assert program.endswith("python.exe")
    assert arguments[:2] == ["-m", "papercraft.worker.cli"]
    frozen_executable = tmp_path / "PaperCraft.exe"
    frozen_executable.touch()
    program, arguments = worker_invocation(retry, executable=frozen_executable, frozen=True)
    assert program == str(frozen_executable.resolve())
    assert arguments[0] == "--papercraft-worker"
    assert arguments[1:] == retry.arguments()


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
def test_main_window_renders_russian_progress_card(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from papercraft.ui.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow(AppSettings(projects_root=tmp_path))
    try:
        workspace = ProjectService(AppSettings(projects_root=tmp_path)).create(
            ProjectBrief(topic="UI progress")
        )
        window.workspace = workspace
        now = datetime.now(UTC)
        run = GenerationRun(
            project_id=workspace.project.id,
            status=RunStatus.RETRYING,
            current_stage=PipelineStage.GENERATE_SECTIONS.value,
            started_at=now - timedelta(minutes=3),
        )
        workspace.repository.save_run(run)
        workspace.repository.save_stage(
            StageRun(
                run_id=run.id,
                name=PipelineStage.GENERATE_SECTIONS.value,
                order=0,
                status=StageStatus.RETRYING,
                started_at=now - timedelta(minutes=1),
                progress_current=2,
                progress_total=4,
                checkpoint={
                    "progress_message": "HTTP 429 rate limit",
                    "retry_after_seconds": 30,
                },
            )
        )
        window.active_run_id = run.id

        window._poll_run()

        assert window.run_status.text() == "Повторная попытка"
        assert window.run_status.property("tone") == "warning"
        assert window.run_stage.text() == "Генерация разделов"
        assert window.run_operation.text().startswith("Генерация разделов")
        assert window.run_progress.text() == "2 из 4 · этапы: 0 из 1"
        assert window.run_elapsed.text() != "—"
        assert window.run_eta.text() == "ожидание доступа к Gemini"
        assert "Gemini временно ограничил запросы" in window.run_quota_wait.text()
        assert window.overall_progress.value() == 50
        assert window.progress_percent_label.text() == "50% завершено"
        assert window.stages_table.item(0, 3).text() == "2 из 4"
        assert window.stages_table.item(0, 4).text() != "—"
    finally:
        window.close()
        application.processEvents()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 is not installed")
def test_main_window_refuses_lossy_text_edit_for_section_with_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plain-text editor must not replace a mixed-content section body."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog

    from papercraft.ui.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow(AppSettings(projects_root=tmp_path))
    try:
        workspace = ProjectService(AppSettings(projects_root=tmp_path)).create(
            ProjectBrief(topic="Смешанный раздел")
        )
        run = GenerationRun(project_id=workspace.project.id, status=RunStatus.SUCCEEDED)
        workspace.repository.save_run(run)
        manuscript = Manuscript(
            project_id=workspace.project.id,
            title="Смешанный раздел",
            blocks=[
                HeadingBlock(text="Введение", section_id="intro"),
                ParagraphBlock(text="Текст до таблицы."),
                TableBlock(spec=TableSpec(headers=["Год", "Значение"], rows=[[2025, 1]])),
                ParagraphBlock(text="Текст после таблицы."),
            ],
        )
        workspace.repository.save_manuscript(manuscript)
        window.workspace = workspace
        window.active_run_id = run.id
        window.result_section_combo.addItem("Введение", "intro")
        errors: list[str] = []
        monkeypatch.setattr(window, "_error", errors.append)

        def unexpected_dialog(*_args: object, **_kwargs: object) -> tuple[str, bool]:
            raise AssertionError("The lossy plain-text editor must not open")

        monkeypatch.setattr(QInputDialog, "getMultiLineText", unexpected_dialog)
        window._edit_selected_section()

        assert len(errors) == 1
        assert "нетекстовые блоки" in errors[0]
        assert "не может безопасно сохранить" in errors[0]
        assert workspace.repository.get_latest_manuscript(workspace.project.id) == manuscript
        assert workspace.repository.list_section_revisions(workspace.project.id, "intro") == []
    finally:
        window.close()
        application.processEvents()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 is not installed")
def test_dark_studio_result_preview_qa_and_confirmations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication, QMessageBox

    from papercraft.ui.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow(AppSettings(projects_root=tmp_path))
    try:
        workspace = ProjectService(AppSettings(projects_root=tmp_path)).create(
            ProjectBrief(topic="Проверка результата")
        )
        run = GenerationRun(project_id=workspace.project.id, status=RunStatus.SUCCEEDED)
        workspace.repository.save_run(run)
        paths = {
            ArtifactKind.DOCX: tmp_path / "result.docx",
            ArtifactKind.PDF: tmp_path / "result.pdf",
            ArtifactKind.PAGE_PREVIEW: tmp_path / "page-001.png",
        }
        paths[ArtifactKind.DOCX].write_bytes(b"docx")
        paths[ArtifactKind.PDF].write_bytes(b"pdf")
        image = QImage(80, 120, QImage.Format.Format_RGB32)
        image.fill(QColor("#7C5CFC"))
        assert image.save(str(paths[ArtifactKind.PAGE_PREVIEW]))
        for kind, path in paths.items():
            workspace.repository.save_artifact(
                Artifact(
                    project_id=workspace.project.id,
                    run_id=run.id,
                    kind=kind,
                    path=str(path),
                    sha256=sha256_file(path),
                    size_bytes=path.stat().st_size,
                )
            )
        workspace.repository.save_qa_report(
            QAReport(
                project_id=workspace.project.id,
                run_id=run.id,
                issues=[
                    QAIssue(
                        severity=QASeverity.WARNING,
                        category="layout",
                        message="Проверьте отступы",
                    )
                ],
                summary="Есть одно замечание.",
            )
        )
        window.workspace = workspace
        window.active_run_id = run.id
        window._refresh_results()

        assert window.docx_card.value_label.text() == "Готов"
        assert window.pdf_card.value_label.text() == "Готов"
        assert window.open_docx_button.isEnabled()
        # A diagnostic-only QA report is not a release scope. The artifacts
        # remain inspectable, but export stays fail-closed until Package
        # records its matching document/manuscript/plan identity.
        assert not window.export_docx_button.isEnabled()
        assert not window.export_pdf_button.isEnabled()
        assert "release-QA scope" in window.export_docx_button.toolTip()
        assert window.qa_badge.text() == "Есть замечания"
        assert window.qa_badge.property("tone") == "warning"
        assert window.preview_page_label.text() == "Страница 1 из 1"
        assert not window.preview_image.pixmap().isNull()

        second_preview = tmp_path / "page-002.png"
        image.fill(QColor("#35C2FF"))
        assert image.save(str(second_preview))
        workspace.repository.save_artifact(
            Artifact(
                project_id=workspace.project.id,
                run_id=run.id,
                kind=ArtifactKind.PAGE_PREVIEW,
                path=str(second_preview),
                sha256=sha256_file(second_preview),
                size_bytes=second_preview.stat().st_size,
            )
        )
        window._refresh_results()
        window._move_preview(1)
        assert window.preview_page_label.text() == "Страница 2 из 2"
        window._refresh_results()
        assert window.preview_page_label.text() == "Страница 2 из 2"

        calls: list[object] = []
        monkeypatch.setattr(window, "_cancel_run", lambda: calls.append("cancel"))
        monkeypatch.setattr(window, "_rebuild_selected_section", lambda: calls.append("section"))
        monkeypatch.setattr(window, "_retry_from_stage", calls.append)
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
        )
        window._confirm_cancel_run()
        window._confirm_rebuild_section()
        window._confirm_rebuild_document()
        assert calls == []

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
        )
        window._confirm_cancel_run()
        window._confirm_rebuild_section()
        window._confirm_rebuild_document()
        assert calls == ["cancel", "section", PipelineStage.GENERATE_SECTIONS]
    finally:
        window.close()
        application.processEvents()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 is not installed")
def test_main_window_ignores_worker_events_from_another_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from papercraft.ui.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    settings = AppSettings(projects_root=tmp_path)
    window = MainWindow(settings)
    try:
        workspace = ProjectService(settings).create(ProjectBrief(topic="Текущий проект"))
        window.workspace = workspace
        window.active_run_id = None
        window.worker_project_id = "other-project"
        window._handle_worker_line('{"run_id": "foreign-run", "event": "run_ready"}')
        assert window.active_run_id is None

        monkeypatch.setattr(window, "_worker_is_running", lambda: True)
        window.new_topic.setPlainText("Новый проект")
        window._create_project()
        assert window.workspace.project.id == workspace.project.id
        assert "Нельзя создать другой проект" in window.notice_banner.text()
    finally:
        window.close()
        application.processEvents()


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 is not installed")
def test_main_window_has_four_connected_workspace_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from papercraft.ui.main_window import PAGE_NAMES, MainWindow, WorkspaceStep

    application = QApplication.instance() or QApplication([])
    window = MainWindow(AppSettings(projects_root=tmp_path))
    try:
        assert PAGE_NAMES == ["Проект", "План", "Генерация", "Результат"]
        assert window.pages.count() == 4
        assert window.navigation.currentRow() == WorkspaceStep.PROJECT
        for index in range(1, window.navigation.count()):
            assert not window.navigation.item(index).flags() & Qt.ItemFlag.ItemIsEnabled
        assert window.pause_button.text() == "Пауза"
        assert window.retry_stage_combo.count() == len(PipelineStage)
        assert window.result_section_combo.count() == 0

        window.new_topic.setPlainText("Автоматизированная академическая работа")
        window._create_project()
        assert window.workspace is not None
        assert window.pages.currentIndex() == WorkspaceStep.PROJECT
        for index in range(window.navigation.count()):
            assert window.navigation.item(index).flags() & Qt.ItemFlag.ItemIsEnabled
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
        assert requests[-1].acknowledge_checkpoint is False

        checkpoint = GenerationRun(
            project_id=window.workspace.project.id,
            status=RunStatus.WAITING_INPUT,
            current_stage=PipelineStage.EXTRACT_REQUIREMENTS.value,
        )
        window.workspace.repository.save_run(checkpoint)
        window.workspace.repository.save_stage(
            StageRun(
                run_id=checkpoint.id,
                name=PipelineStage.EXTRACT_REQUIREMENTS.value,
                status=StageStatus.SUCCEEDED,
            )
        )
        window.active_run_id = checkpoint.id
        window._resume_run()
        assert requests[-1].acknowledge_checkpoint is True

        authentication = GenerationRun(
            project_id=window.workspace.project.id,
            status=RunStatus.WAITING_INPUT,
            current_stage=PipelineStage.PREFLIGHT.value,
        )
        window.workspace.repository.save_run(authentication)
        window.workspace.repository.save_stage(
            StageRun(
                run_id=authentication.id,
                name=PipelineStage.PREFLIGHT.value,
                status=StageStatus.FAILED,
            )
        )
        window.active_run_id = authentication.id
        window._resume_run()
        assert requests[-1].acknowledge_checkpoint is False

        window.active_run_id = run.id
        run.status = RunStatus.RUNNING
        window.workspace.repository.save_run(run)
        window._pause_run()
        assert window.workspace.repository.get_run(run.id).status is RunStatus.PAUSED  # type: ignore[union-attr]
        window._cancel_run()
        assert requests[-1].cancel is True
    finally:
        window.close()
        application.processEvents()
