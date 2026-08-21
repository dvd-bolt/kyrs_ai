from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from papercraft.domain import (
    AppendixBlock,
    Artifact,
    ArtifactKind,
    Citation,
    Claim,
    Dataset,
    DatasetColumn,
    DomainProfile,
    Evidence,
    FactOrigin,
    FactRecord,
    FigureBlock,
    GenerationRun,
    HeadingBlock,
    ImageSpec,
    Locator,
    Manuscript,
    Outline,
    ParagraphBlock,
    Project,
    ProjectBlueprint,
    ProjectBrief,
    QAIssue,
    QAReport,
    QASeverity,
    QAStatus,
    RequirementCategory,
    RequirementRule,
    RequirementSet,
    RunEvent,
    RunStatus,
    SectionSpec,
    Source,
    SourceFragment,
    SourceRole,
    StageRun,
    StageStatus,
    TableBlock,
    TableSpec,
    WorkType,
)
from papercraft.infrastructure.persistence import (
    AtomicArtifactStore,
    ImmutableFileStorage,
    LegacyCourseProjectImporter,
    ProjectPaths,
    SQLiteRepository,
    sha256_file,
)


def make_project() -> Project:
    return Project(
        brief=ProjectBrief(
            topic="Разработка аналитической системы",
            prompt="Подготовить курсовую работу",
            work_type=WorkType.COURSEWORK,
            domain_profile=DomainProfile.IT,
        )
    )


def test_domain_models_validate_and_round_trip_discriminated_blocks() -> None:
    source_id = "source-1"
    fragment = SourceFragment(
        source_id=source_id,
        content="Подтверждаемый фрагмент",
        locator=Locator(page=3, line_start=4, line_end=7),
    )
    assert fragment.locator.source_id == source_id
    with pytest.raises(ValidationError):
        Locator(line_start=4, line_end=3)

    outline = Outline(
        sections=[
            SectionSpec(id="intro", title="Введение", order=0),
            SectionSpec(id="chapter", title="Глава 1", order=1, depends_on=["intro"]),
        ]
    )
    assert outline.sections[1].depends_on == ["intro"]
    with pytest.raises(ValidationError):
        Outline(sections=[SectionSpec(id="bad", title="Bad", depends_on=["missing"])])

    manuscript = Manuscript(
        project_id="project-1",
        title="Работа",
        blocks=[
            HeadingBlock(text="Введение", section_id="intro"),
            ParagraphBlock(text="Текст", citation_ids=["citation-1"]),
            TableBlock(spec=TableSpec(headers=["Год", "Значение"], rows=[[2025, 10], [2026, 12]])),
            FigureBlock(caption="Архитектура", image_spec=ImageSpec(prompt="clean system diagram")),
            AppendixBlock(title="Приложение A", blocks=[ParagraphBlock(text="Материал")]),
        ],
    )
    restored = Manuscript.model_validate_json(manuscript.model_dump_json())
    assert isinstance(restored.blocks[2], TableBlock)
    assert isinstance(restored.blocks[4], AppendixBlock)
    assert isinstance(restored.blocks[4].blocks[0], ParagraphBlock)

    report = QAReport(
        project_id="project-1",
        run_id="run-1",
        issues=[QAIssue(severity=QASeverity.CRITICAL, category="citations", message="No evidence")],
    )
    assert report.status is QAStatus.FAIL


def test_project_paths_immutable_storage_and_atomic_json(tmp_path: Path) -> None:
    paths = ProjectPaths.for_project("safe-project", tmp_path, create=True)
    assert paths.database == paths.root / "project.db"
    assert all(path.is_dir() for path in (paths.originals, paths.derived, paths.runs, paths.artifacts, paths.backups))
    with pytest.raises(ValueError):
        ProjectPaths.for_project("../escape", tmp_path)
    with pytest.raises(ValueError):
        ProjectPaths.for_project("CON", tmp_path)

    source = tmp_path / "методичка.txt"
    source.write_text("immutable payload", encoding="utf-8")
    storage = ImmutableFileStorage(paths.originals)
    stored = storage.store(source)
    assert stored.sha256 == hashlib.sha256(b"immutable payload").hexdigest()
    assert sha256_file(stored.path) == stored.sha256
    assert storage.store(source).path == stored.path

    source.write_text("a new revision", encoding="utf-8")
    revised = storage.store(source)
    assert revised.path != stored.path
    assert stored.path.read_text(encoding="utf-8") == "immutable payload"

    artifacts = AtomicArtifactStore(paths.derived)
    target = artifacts.write_json("nested/checkpoint.json", {"тема": "Тест", "step": 4})
    assert json.loads(target.read_text(encoding="utf-8"))["step"] == 4
    with pytest.raises(ValueError):
        artifacts.write_json("../escape.json", {})
    with pytest.raises(FileExistsError):
        artifacts.write_json("nested/checkpoint.json", {}, overwrite=False)


def test_sqlite_repository_persists_pipeline_state_with_wal(tmp_path: Path) -> None:
    project = make_project()
    paths = ProjectPaths.for_project(project.id, tmp_path, create=True)
    repository = SQLiteRepository(paths.database)
    assert repository.journal_mode == "wal"
    repository.save_project(project)
    assert repository.get_project(project.id) == project

    payload = tmp_path / "methodology.pdf"
    payload.write_bytes(b"%PDF synthetic fixture")
    stored = ImmutableFileStorage(paths.originals).store(payload)
    source = Source(
        project_id=project.id,
        role=SourceRole.METHODOLOGY,
        original_name=payload.name,
        stored_path=stored.path.relative_to(paths.root).as_posix(),
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        mime_type="application/pdf",
    )
    repository.save_source(source)
    fragment = SourceFragment(source_id=source.id, content="Шрифт Times New Roman", locator=Locator(page=2))
    repository.save_fragment(fragment)
    assert repository.list_sources(project.id, role=SourceRole.METHODOLOGY.value) == [source]
    assert repository.list_fragments(source.id) == [fragment]

    requirements = RequirementSet(
        project_id=project.id,
        rules=[
            RequirementRule(
                category=RequirementCategory.TYPOGRAPHY,
                key="font_name",
                statement="Use Times New Roman",
                value="Times New Roman",
            )
        ],
    )
    repository.save_requirement_set(requirements)
    assert repository.get_latest_requirement_set(project.id) == requirements

    blueprint = ProjectBlueprint(
        project_id=project.id,
        topic=project.brief.topic,
        goal="Разработать систему",
        outline=Outline(sections=[SectionSpec(id="intro", title="Введение")]),
    )
    repository.save_blueprint(blueprint)
    assert repository.get_latest_blueprint(project.id) == blueprint

    manuscript = Manuscript(
        project_id=project.id,
        title=project.brief.title,
        blocks=[HeadingBlock(text="Введение"), ParagraphBlock(text="Основной текст")],
    )
    repository.save_manuscript(manuscript)
    assert repository.get_latest_manuscript(project.id) == manuscript

    run = GenerationRun(project_id=project.id, status=RunStatus.RUNNING, current_stage="ingest")
    repository.save_run(run)
    stage = StageRun(run_id=run.id, name="ingest", order=2, status=StageStatus.RUNNING, attempts=1)
    repository.save_stage(stage)
    event = RunEvent(run_id=run.id, stage_id=stage.id, event_type="progress", message="50%")
    sequence = repository.append_event(event)
    assert repository.get_run(run.id) == run
    assert repository.list_stages(run.id) == [stage]
    assert repository.list_events(run.id, after_sequence=sequence - 1) == [(sequence, event)]

    artifact = Artifact(
        project_id=project.id,
        run_id=run.id,
        stage_id=stage.id,
        kind=ArtifactKind.EXTRACTED_TEXT,
        path="derived/extracted.json",
        sha256="0" * 64,
        size_bytes=10,
        mime_type="application/json",
    )
    repository.save_artifact(artifact)
    report = QAReport(
        project_id=project.id,
        run_id=run.id,
        issues=[QAIssue(severity=QASeverity.WARNING, category="layout", message="Check heading")],
    )
    repository.save_qa_report(report)
    assert repository.list_artifacts(project.id, run_id=run.id) == [artifact]
    assert repository.get_latest_qa_report(run.id) == report

    claim = Claim(project_id=project.id, text="Система повышает точность")
    evidence = Evidence(
        claim_id=claim.id,
        source_id=source.id,
        locator=Locator(page=2),
        excerpt="Точность повысилась",
    )
    fact = FactRecord(project_id=project.id, name="Точность", value=95, unit="%", origin=FactOrigin.USER)
    dataset = Dataset(
        project_id=project.id,
        name="Динамика",
        columns=[DatasetColumn(name="year"), DatasetColumn(name="value")],
        rows=[{"year": "2026", "value": "95"}],
        origin=FactOrigin.USER,
    )
    repository.save_claim(claim)
    repository.save_evidence(project.id, evidence)
    citation = Citation(
        claim_id=claim.id,
        evidence_id=evidence.id,
        bibliography_entry_id="bib-1",
        marker="[1]",
    )
    repository.save_citation(project.id, citation)
    repository.save_fact(fact)
    repository.save_dataset(dataset)
    assert repository.list_claims(project.id) == [claim]
    assert repository.list_evidence(project.id) == [evidence]
    assert repository.list_citations(project.id) == [citation]
    assert repository.list_facts(project.id) == [fact]
    assert repository.list_datasets(project.id) == [dataset]

    backup = repository.backup_to(paths.backups / "checkpoint.db")
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_foreign_keys_prevent_orphan_pipeline_rows(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "project.db")
    with pytest.raises(sqlite3.IntegrityError):
        repository.save_run(GenerationRun(project_id="missing"))


def test_legacy_courseproject_import_is_additive_and_complete(tmp_path: Path) -> None:
    knowledge = tmp_path / "input.csv"
    knowledge.write_text("year,value\n2025,10\n", encoding="utf-8")
    legacy = tmp_path / "legacy.courseproject"
    legacy.write_text(
        json.dumps(
            {
                "project_id": "legacy-project",
                "topic": "Импортированная курсовая",
                "project_type": "coursework_finance",
                "current_step": 4,
                "title_page_data": {"student_info": "Иванов И.И."},
                "formatting_rules": {"font_name": "Times New Roman", "margin_left_cm": 3.0},
                "plan_structure": [
                    {"id": "intro", "title": "Введение", "target_words": 500},
                    {"id": "chapter-1", "title": "Глава 1", "target_words": 1500},
                ],
                "sections_content": {"intro": "Вводный текст", "chapter-1": "Текст главы"},
                "knowledge_base_files": [str(knowledge), str(tmp_path / "missing.pdf")],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    projects_root = tmp_path / "projects"
    result = LegacyCourseProjectImporter(projects_root).import_file(legacy)
    assert result.project.brief.work_type is WorkType.COURSEWORK
    assert result.project.brief.domain_profile is DomainProfile.FINANCE
    assert result.requirements is not None and len(result.requirements.rules) == 2
    assert result.blueprint is not None and len(result.blueprint.outline.sections) == 2
    assert result.manuscript is not None and len(result.manuscript.blocks) == 4
    assert len(result.sources) == 1
    assert result.warnings and "missing.pdf" in result.warnings[0]
    assert legacy.exists()  # migration does not mutate or remove the input
    assert (result.paths.derived / "legacy_import.json").is_file()

    repository = SQLiteRepository(result.paths.database)
    assert repository.get_project(result.project.id) == result.project
    assert repository.get_latest_manuscript(result.project.id) == result.manuscript
    assert sha256_file(result.paths.originals / Path(result.sources[0].stored_path).name) == result.sources[0].sha256
