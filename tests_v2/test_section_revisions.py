from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from papercraft.application import (
    PipelineStage,
    ProjectService,
    ProjectWorkspace,
    SectionRevisionService,
)
from papercraft.config import AppSettings
from papercraft.domain import (
    AppendixBlock,
    ChartBlock,
    ChartSpec,
    ChartType,
    DiagramBlock,
    DiagramSpec,
    GenerationRun,
    HeadingBlock,
    Manuscript,
    Outline,
    ParagraphBlock,
    ProjectBlueprint,
    ProjectBrief,
    SectionSpec,
    TableBlock,
    TableSpec,
)
from papercraft.infrastructure.persistence import SQLiteRepository


def _workspace_with_manuscript(tmp_path: Path) -> tuple[ProjectWorkspace, Manuscript]:
    workspace = ProjectService(AppSettings(projects_root=tmp_path)).create(
        ProjectBrief(title="Тестовая работа", topic="Тестовая работа")
    )
    blueprint = ProjectBlueprint(
        project_id=workspace.project.id,
        topic=workspace.project.brief.topic,
        outline=Outline(
            sections=[
                SectionSpec(id="intro", title="Введение", order=0),
                SectionSpec(id="chapter", title="Глава", order=1, depends_on=["intro"]),
                SectionSpec(id="conclusion", title="Заключение", order=2),
            ]
        ),
    )
    workspace.repository.save_blueprint(blueprint)
    manuscript = Manuscript(
        project_id=workspace.project.id,
        title=workspace.project.brief.title,
        blocks=[
            HeadingBlock(text="Введение", section_id="intro"),
            ParagraphBlock(text="Исходный текст введения."),
            HeadingBlock(text="Глава", section_id="chapter"),
            ParagraphBlock(text="Исходный текст главы."),
            HeadingBlock(text="Заключение", section_id="conclusion"),
            ParagraphBlock(text="Исходный текст заключения."),
        ],
    )
    workspace.repository.save_manuscript(manuscript)
    return workspace, manuscript


def _section_text(manuscript: Manuscript, section_id: str) -> list[str]:
    active = False
    values: list[str] = []
    for block in manuscript.blocks:
        if isinstance(block, HeadingBlock) and block.section_id is not None:
            active = block.section_id == section_id
        if active and isinstance(block, ParagraphBlock):
            values.append(block.text)
    return values


def _edited_plan(blueprint: ProjectBlueprint, *, title: str, target_words: int) -> ProjectBlueprint:
    sections = [
        section.model_copy(update={"title": title, "target_words": target_words})
        if section.id == "intro"
        else section.model_copy(deep=True)
        for section in blueprint.outline.sections
    ]
    return blueprint.model_copy(
        update={"outline": blueprint.outline.model_copy(update={"sections": sections})}
    )


def test_section_revision_preserves_generated_baseline_and_only_invalidates_qa_render(tmp_path: Path) -> None:
    workspace, generated = _workspace_with_manuscript(tmp_path)
    revisions = SectionRevisionService(workspace.project.id, workspace.repository)

    result = revisions.revise_section("intro", "Пользовательский текст введения.")

    current = workspace.repository.get_latest_manuscript(workspace.project.id)
    assert current is not None
    assert current.id != generated.id
    assert current.revision == generated.revision + 1
    assert _section_text(current, "intro") == ["Пользовательский текст введения."]
    edited_block = next(
        block
        for block in current.blocks
        if isinstance(block, ParagraphBlock) and block.text == "Пользовательский текст введения."
    )
    assert edited_block.metadata["user_override"] is True
    assert edited_block.metadata["evidence_review_required"] is True
    assert _section_text(current, "chapter") == ["Исходный текст главы."]
    assert _section_text(generated, "intro") == ["Исходный текст введения."]

    history = revisions.list_revisions("intro")
    assert [item.source for item in history] == ["user_override", "generated"]
    assert _section_text(
        Manuscript(project_id=workspace.project.id, title="history", blocks=list(history[1].blocks)), "intro"
    ) == ["Исходный текст введения."]
    assert workspace.repository.get_section_revision_payload(workspace.project.id, result.record.id) is not None

    assert result.invalidation.start_stage is PipelineStage.CITATION_AUDIT
    assert result.invalidation.section_ids == ("intro",)
    assert result.invalidation.stage_names == (
        "citation_audit",
        "consistency_qa",
        "render_docx",
        "word_finalize",
        "export_pdf",
        "pdf_visual_qa",
        "final_gemini_review",
        "package",
    )


def test_section_revision_marks_user_authored_table_for_numeric_provenance_qa(tmp_path: Path) -> None:
    workspace, _ = _workspace_with_manuscript(tmp_path)
    revisions = SectionRevisionService(workspace.project.id, workspace.repository)

    revisions.revise_section(
        "intro",
        [
            TableBlock(
                spec=TableSpec(headers=["Year", "Value"], rows=[[2025, 1_000_000]])
            )
        ],
    )

    manuscript = workspace.repository.get_latest_manuscript(workspace.project.id)
    assert manuscript is not None
    table = next(block for block in manuscript.blocks if isinstance(block, TableBlock))
    assert table.metadata["user_override"] is True


def test_section_revision_with_visual_only_invalidates_visual_suffix_including_appendix(
    tmp_path: Path,
) -> None:
    workspace, _ = _workspace_with_manuscript(tmp_path)
    revisions = SectionRevisionService(workspace.project.id, workspace.repository)

    chart_result = revisions.revise_section(
        "intro",
        [
            ChartBlock(
                spec=ChartSpec(
                    chart_type=ChartType.BAR,
                    title="Динамика",
                    dataset_id="known-dataset",
                    x_column="year",
                    y_columns=["value"],
                )
            )
        ],
    )

    assert chart_result.invalidation.start_stage is PipelineStage.GENERATE_VISUALS
    assert chart_result.invalidation.stage_names == (
        "generate_visuals",
        "citation_audit",
        "consistency_qa",
        "render_docx",
        "word_finalize",
        "export_pdf",
        "pdf_visual_qa",
        "final_gemini_review",
        "package",
    )

    appendix_result = revisions.revise_section(
        "intro",
        [
            AppendixBlock(
                title="Приложение",
                blocks=[
                    DiagramBlock(
                        spec=DiagramSpec(title="Схема", source="flowchart TD\nA --> B")
                    )
                ],
            )
        ],
    )

    assert appendix_result.invalidation.start_stage is PipelineStage.GENERATE_VISUALS


def test_section_revision_survives_repository_reopen(tmp_path: Path) -> None:
    workspace, _ = _workspace_with_manuscript(tmp_path)
    SectionRevisionService(workspace.project.id, workspace.repository).revise_section(
        "intro", "Долговечная пользовательская правка."
    )

    reopened = SQLiteRepository(workspace.paths.database)
    manuscript = reopened.get_latest_manuscript(workspace.project.id)
    assert manuscript is not None
    assert _section_text(manuscript, "intro") == ["Долговечная пользовательская правка."]
    history = SectionRevisionService(workspace.project.id, reopened).list_revisions("intro")
    assert history[0].source == "user_override"
    assert _section_text(
        Manuscript(project_id=workspace.project.id, title="history", blocks=list(history[0].blocks)),
        "intro",
    ) == ["Долговечная пользовательская правка."]


def test_restore_previous_section_revision_creates_new_history_entry(tmp_path: Path) -> None:
    workspace, _ = _workspace_with_manuscript(tmp_path)
    revisions = SectionRevisionService(workspace.project.id, workspace.repository)
    first = revisions.revise_section("intro", "Первая версия.")
    second = revisions.revise_section("intro", "Вторая версия.")

    restored = revisions.restore_previous_revision("intro")
    current = workspace.repository.get_latest_manuscript(workspace.project.id)
    assert current is not None
    assert _section_text(current, "intro") == ["Первая версия."]
    assert restored.revision.source == "restore"
    assert restored.revision.restored_from_id == first.record.id

    history = revisions.list_revisions("intro")
    assert [item.source for item in history] == ["restore", "user_override", "user_override", "generated"]
    assert history[1].record.id == second.record.id
    assert history[2].record.id == first.record.id


def test_plan_edit_rebuilds_from_sections_without_overwriting_user_blueprint(tmp_path: Path) -> None:
    workspace, _ = _workspace_with_manuscript(tmp_path)
    revisions = SectionRevisionService(workspace.project.id, workspace.repository)

    plan_invalidation = revisions.invalidation_for("intro", plan_edit=True)

    assert plan_invalidation.start_stage is PipelineStage.GENERATE_SECTIONS
    assert plan_invalidation.stages[0] is PipelineStage.GENERATE_SECTIONS
    assert plan_invalidation.stages[-1] is PipelineStage.PACKAGE
    assert plan_invalidation.section_ids == ("intro", "chapter")


def test_saved_plan_revision_targets_changed_section_and_downstream_dependents(tmp_path: Path) -> None:
    workspace, _ = _workspace_with_manuscript(tmp_path)
    revisions = SectionRevisionService(workspace.project.id, workspace.repository)
    original = workspace.repository.get_latest_blueprint(workspace.project.id)
    assert original is not None

    result = revisions.revise_plan(
        _edited_plan(original, title="Уточнённое введение", target_words=650)
    )

    assert result.invalidation.start_stage is PipelineStage.GENERATE_SECTIONS
    assert result.invalidation.section_ids == ("intro", "chapter")
    assert "conclusion" not in result.invalidation.section_ids


def test_prepare_plan_rebuild_persists_selection_and_fresh_token(tmp_path: Path) -> None:
    workspace, _ = _workspace_with_manuscript(tmp_path)
    revisions = SectionRevisionService(workspace.project.id, workspace.repository)
    original = workspace.repository.get_latest_blueprint(workspace.project.id)
    assert original is not None
    result = revisions.revise_plan(
        _edited_plan(original, title="Уточнённое введение", target_words=650)
    )
    run = GenerationRun(
        project_id=workspace.project.id,
        metadata={
            "rebuild_section_ids": ["conclusion"],
            "rebuild_section_token": "obsolete-token",
            "unrelated": "retained",
        },
    )
    workspace.repository.save_run(run)

    prepared = revisions.prepare_plan_rebuild(run.id, result)
    persisted = workspace.repository.get_run(run.id)

    assert persisted == prepared
    assert persisted is not None
    assert persisted.metadata["rebuild_section_ids"] == ["intro", "chapter"]
    assert persisted.metadata["rebuild_section_token"] != "obsolete-token"
    assert isinstance(persisted.metadata["rebuild_section_token"], str)
    assert persisted.metadata["unrelated"] == "retained"


def test_plan_revision_preserves_baseline_and_can_restore_previous_edit(tmp_path: Path) -> None:
    workspace, _ = _workspace_with_manuscript(tmp_path)
    revisions = SectionRevisionService(workspace.project.id, workspace.repository)
    original = workspace.repository.get_latest_blueprint(workspace.project.id)
    assert original is not None

    first = revisions.revise_plan(_edited_plan(original, title="Новое введение", target_words=650))
    second = revisions.revise_plan(_edited_plan(first.blueprint, title="Ещё одно введение", target_words=720))

    restored = revisions.restore_previous_plan_revision()
    current = workspace.repository.get_latest_blueprint(workspace.project.id)
    assert current is not None
    assert current.id != original.id
    assert current.outline.sections[0].title == "Новое введение"
    assert current.outline.sections[0].target_words == 650
    assert restored.revision.source == "restore"
    assert restored.revision.restored_from_id == first.record.id
    assert restored.invalidation.start_stage is PipelineStage.GENERATE_SECTIONS
    assert restored.invalidation.stages[0] is PipelineStage.GENERATE_SECTIONS
    assert restored.invalidation.stages[-1] is PipelineStage.PACKAGE
    assert restored.invalidation.section_ids == ("intro", "chapter")

    history = revisions.list_plan_revisions()
    assert [item.source for item in history] == ["restore", "user_override", "user_override", "generated"]
    assert history[1].record.id == second.record.id
    assert history[2].record.id == first.record.id
    assert history[-1].blueprint.outline.sections[0].title == "Введение"


def test_invalid_replacement_does_not_write_a_revision(tmp_path: Path) -> None:
    workspace, generated = _workspace_with_manuscript(tmp_path)
    revisions = SectionRevisionService(workspace.project.id, workspace.repository)

    with pytest.raises(ValueError, match="different section"):
        revisions.revise_section("intro", [HeadingBlock(text="Глава", section_id="chapter")])

    assert workspace.repository.get_latest_manuscript(workspace.project.id) == generated
    assert revisions.list_revisions("intro") == []


def test_existing_v2_database_migrates_to_v5_with_backup_without_losing_manuscript(
    tmp_path: Path,
) -> None:
    workspace, generated = _workspace_with_manuscript(tmp_path)

    with sqlite3.connect(workspace.paths.database) as connection:
        connection.execute("DROP TABLE plan_revision_payloads")
        connection.execute("DROP TABLE section_revision_payloads")
        connection.execute("PRAGMA user_version = 2")

    upgraded = SQLiteRepository(workspace.paths.database)

    assert upgraded.schema_version == 5
    assert upgraded.get_latest_manuscript(workspace.project.id) == generated
    with sqlite3.connect(workspace.paths.database) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {
        "section_revision_payloads",
        "plan_revision_payloads",
        "submission_releases",
    } <= tables
    backups = upgraded.list_backup_records(workspace.project.id)
    assert backups and Path(backups[0].path).is_file()
    result = SectionRevisionService(workspace.project.id, upgraded).revise_section("intro", "После миграции.")
    assert result.record.revision == 2
