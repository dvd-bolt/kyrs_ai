from pathlib import Path

from papercraft.application import (
    AutopilotService,
    PipelineStage,
    ProjectService,
    SourceService,
    StageOutcome,
)
from papercraft.config import AppSettings
from papercraft.domain import ProjectBrief, RunStatus, SourceRole


def test_project_and_source_services(tmp_path: Path) -> None:
    workspace = ProjectService(AppSettings(projects_root=tmp_path)).create(
        ProjectBrief(topic="Автоматизация тестирования")
    )
    source_file = tmp_path / "methodology.txt"
    source_file.write_text("Требования к курсовой работе", encoding="utf-8")
    result = SourceService(workspace).import_files([source_file], SourceRole.METHODOLOGY)
    assert len(result.sources) == 1
    assert workspace.repository.list_fragments(result.sources[0].id)


def test_pipeline_is_checkpointed_and_resumable(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Тестовый проект"))
    calls: list[str] = []

    def handler(context):
        calls.append(context.stage.name)
        return StageOutcome(checkpoint={"ok": True})

    handlers = {stage: handler for stage in PipelineStage}
    service = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        handlers,
    )
    run = service.start()
    assert run.status == RunStatus.SUCCEEDED
    assert calls == [stage.value for stage in PipelineStage]
    assert all(stage.status.value == "succeeded" for stage in workspace.repository.list_stages(run.id))


def test_pipeline_failure_can_retry_from_stage(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Retry"))
    fail_once = {"value": True}

    def handler(context):
        if context.stage.name == PipelineStage.PLAN.value and fail_once["value"]:
            fail_once["value"] = False
            raise RuntimeError("bad plan")
        return StageOutcome()

    service = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        {stage: handler for stage in PipelineStage},
    )
    failed = service.start()
    assert failed.status == RunStatus.FAILED
    completed = service.retry_from(failed.id, PipelineStage.PLAN)
    assert completed.status == RunStatus.SUCCEEDED
