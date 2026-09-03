from decimal import Decimal
from pathlib import Path

import pytest

from papercraft.application import (
    AutopilotService,
    PipelineStage,
    ProjectService,
    SourceService,
    StageContext,
    StageOutcome,
)
from papercraft.application.release import build_submission_release, stable_hash
from papercraft.config import AppSettings
from papercraft.domain import (
    Artifact,
    ArtifactKind,
    AutopilotOptions,
    GenerationRun,
    Manuscript,
    Outline,
    ParagraphBlock,
    ProjectBlueprint,
    ProjectBrief,
    QAReport,
    RequirementSet,
    RunStatus,
    SectionSpec,
    SourceRole,
)
from papercraft.infrastructure.persistence import sha256_file
from papercraft.profiles.models import ProfilePolicy, ProfileSectionTemplate, WorkProfile


def _release_outcome(context: StageContext) -> StageOutcome:
    if context.stage.name in {
        PipelineStage.CONSISTENCY_QA.value,
        PipelineStage.FINAL_GEMINI_REVIEW.value,
    }:
        return StageOutcome(
            checkpoint={"accepted": True, "blocker_issues": [], "factual_issues": []}
        )
    if context.stage.name != PipelineStage.PACKAGE.value:
        return StageOutcome(checkpoint={"ok": True})

    requirements = RequirementSet(project_id=context.project.id)
    context.repository.save_requirement_set(requirements)
    blueprint = ProjectBlueprint(
        project_id=context.project.id,
        topic=context.project.brief.topic,
        outline=Outline(sections=[SectionSpec(id="body", title="Body")]),
    )
    context.repository.save_blueprint(blueprint)
    manuscript = Manuscript(
        project_id=context.project.id,
        title=context.project.brief.title,
        blocks=[ParagraphBlock(text="Release fixture")],
    )
    context.repository.save_manuscript(manuscript)
    path = context.paths.artifacts / context.run.id / "release.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"release-fixture")
    artifact = Artifact(
        project_id=context.project.id,
        run_id=context.run.id,
        stage_id=context.stage.id,
        kind=ArtifactKind.DOCX,
        path=str(path),
        sha256=sha256_file(path),
        size_bytes=path.stat().st_size,
        metadata={
            "phase": "final",
            "finalizer": "libreoffice",
            "fields_updated": True,
        },
    )
    context.repository.save_artifact(artifact)
    report = QAReport(
        project_id=context.project.id,
        run_id=context.run.id,
        metadata={
            "deterministic": True,
            "gate_version": 2,
            "release_hashes": {
                "input_hash": context.run.input_hash,
                "manuscript_hash": stable_hash(manuscript.model_dump(mode="json")),
                "docx_hash": artifact.sha256,
                "pdf_hash": None,
            },
        },
    )
    context.repository.save_qa_report(report)
    project = context.repository.get_project(context.project.id)
    assert project is not None
    release = build_submission_release(
        project=project,
        run=GenerationRun.model_validate(context.run),
        manuscript=manuscript,
        docx_artifact=artifact,
        report=report,
        requirements=requirements,
        blueprint=blueprint,
        profile=WorkProfile(
            id="test-profile",
            version="1",
            display_name="Test",
            work_type="coursework",
            description="Test",
            sections=[
                ProfileSectionTemplate(
                    key="body", title="Body", target_words=100, purpose="Test"
                )
            ],
            policy=ProfilePolicy(voice="academic", minimum_sources=0),
        ),
    )
    return StageOutcome(
        artifacts=[artifact],
        checkpoint={"release": release.model_dump(mode="json")},
    )


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
        return _release_outcome(context)

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


def test_precommitted_parallel_artifact_is_not_added_to_stage_twice(tmp_path: Path) -> None:
    """A durable item checkpoint must remain a single stage output on commit."""

    settings = AppSettings(projects_root=tmp_path)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Artifact checkpoint"))
    created: list[Artifact] = []

    def handler(context):
        if context.stage.name != PipelineStage.PREFLIGHT.value:
            return StageOutcome()
        path = context.artifact_store.write_json(f"{context.run.id}/item.json", {"ok": True})
        artifact = Artifact(
            project_id=context.project.id,
            run_id=context.run.id,
            stage_id=context.stage.id,
            kind=ArtifactKind.OTHER,
            path=str(path),
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
        )
        # This mirrors a parallel stage callback: persist the item before
        # reporting its aggregate StageOutcome.
        context.repository.save_artifact(artifact)
        context.stage.output_artifact_ids.append(artifact.id)
        context.repository.save_stage(context.stage)
        created.append(artifact)
        return StageOutcome(artifacts=[artifact])

    service = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        {stage: handler for stage in PipelineStage},
    )
    run = service.start()

    preflight = next(
        stage for stage in workspace.repository.list_stages(run.id) if stage.name == PipelineStage.PREFLIGHT.value
    )
    assert preflight.output_artifact_ids == [created[0].id]


def test_pipeline_failure_can_retry_from_stage(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Retry"))
    fail_once = {"value": True}
    calls: list[str] = []

    def handler(context: StageContext) -> StageOutcome:
        calls.append(context.stage.name)
        if context.stage.name == PipelineStage.PLAN.value and fail_once["value"]:
            fail_once["value"] = False
            raise RuntimeError("bad plan")
        return _release_outcome(context)

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
    assert calls.count(PipelineStage.PREFLIGHT.value) == 1
    assert calls.count(PipelineStage.INGEST.value) == 1
    assert calls.count(PipelineStage.PLAN.value) == 2
    assert calls.count(PipelineStage.BUILD_FACTS_AND_DATASETS.value) == 1


def test_retry_from_cannot_resurrect_cancelled_run_or_reset_its_stages(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Cancelled retry"))
    service = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        {},
    )
    created = service.create_run()
    cancelled = service.cancel(created.id)
    before_run = cancelled.model_dump(mode="json")
    before_stages = [
        stage.model_dump(mode="json")
        for stage in workspace.repository.list_stages(cancelled.id)
    ]

    with pytest.raises(RuntimeError, match="not eligible for retry"):
        service.retry_from(cancelled.id, PipelineStage.PLAN)

    persisted = workspace.repository.get_run(cancelled.id)
    assert persisted is not None
    assert persisted.model_dump(mode="json") == before_run
    assert [
        stage.model_dump(mode="json")
        for stage in workspace.repository.list_stages(cancelled.id)
    ] == before_stages


def test_retry_from_does_not_reset_failed_run_when_cost_limit_is_exhausted(tmp_path: Path) -> None:
    maximum_cost = Decimal("0.01")
    settings = AppSettings(projects_root=tmp_path)
    workspace = ProjectService(settings).create(
        ProjectBrief(topic="Cost-limited retry"),
        options=AutopilotOptions(maximum_cost=maximum_cost),
    )

    def handler(context: StageContext) -> StageOutcome:
        if context.stage.name == PipelineStage.PLAN.value:
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
    workspace.repository.add_run_usage(
        failed.id,
        maximum_cost,
        maximum_cost=maximum_cost,
    )
    before_run = workspace.repository.get_run(failed.id)
    assert before_run is not None
    before_run_payload = before_run.model_dump(mode="json")
    before_stages = [
        stage.model_dump(mode="json")
        for stage in workspace.repository.list_stages(failed.id)
    ]

    with pytest.raises(RuntimeError, match="configured maximum"):
        service.retry_from(failed.id, PipelineStage.PLAN)

    persisted = workspace.repository.get_run(failed.id)
    assert persisted is not None
    assert persisted.model_dump(mode="json") == before_run_payload
    assert [
        stage.model_dump(mode="json")
        for stage in workspace.repository.list_stages(failed.id)
    ] == before_stages


def test_refresh_research_refuses_changed_project_inputs_without_mutating_run(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path)
    projects = ProjectService(settings)
    workspace = projects.create(ProjectBrief(topic="Original assignment"))

    def handler(context: StageContext) -> StageOutcome:
        return _release_outcome(context)

    initial = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        {stage: handler for stage in PipelineStage},
    ).start()
    assert initial.status == RunStatus.SUCCEEDED

    updated = projects.update(
        workspace.project.id,
        brief=ProjectBrief(topic="Changed assignment"),
    )
    service = AutopilotService(
        settings,
        updated.project,
        updated.repository,
        updated.paths,
        {},
    )

    with pytest.raises(RuntimeError, match="Project inputs changed"):
        service.refresh_research(initial.id)

    persisted = updated.repository.get_run(initial.id)
    assert persisted is not None
    assert persisted.input_hash == initial.input_hash
    assert "force_research_refresh" not in persisted.metadata
    assert all(
        stage.status.value == "succeeded"
        for stage in updated.repository.list_stages(initial.id)
    )
