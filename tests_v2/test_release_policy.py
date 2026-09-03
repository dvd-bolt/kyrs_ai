from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from papercraft.application.documents import DocumentExportBlocked, DocumentService
from papercraft.application.release import ReleasePolicyError, build_submission_release, stable_hash
from papercraft.application.schemas import GlobalReview, SectionCritique
from papercraft.domain import (
    Artifact,
    ArtifactKind,
    BibliographyEntry,
    DomainProfile,
    GenerationRun,
    Manuscript,
    Outline,
    ParagraphBlock,
    Project,
    ProjectBlueprint,
    ProjectBrief,
    QAIssue,
    QAReport,
    QASeverity,
    RequirementSet,
    RunStatus,
    SectionSpec,
    Source,
    SourceRole,
    StageRun,
    StageStatus,
    SubmissionRelease,
    SubmissionStatus,
)
from papercraft.infrastructure.persistence import SQLiteRepository
from papercraft.infrastructure.qa import DeterministicQualityGate, QAGateContext
from papercraft.profiles.models import ProfilePolicy, ProfileSectionTemplate, WorkProfile


def _profile(minimum_sources: int = 0) -> WorkProfile:
    return WorkProfile(
        id="test-profile",
        version="1",
        display_name="Test",
        work_type="coursework",
        description="Release policy fixture",
        sections=[
            ProfileSectionTemplate(
                key="body",
                title="Body",
                target_words=100,
                purpose="Test",
            )
        ],
        policy=ProfilePolicy(voice="academic", minimum_sources=minimum_sources),
    )


def _ready_candidate(tmp_path: Path) -> tuple[
    SQLiteRepository,
    Project,
    GenerationRun,
    Artifact,
    SubmissionRelease,
]:
    repository = SQLiteRepository(tmp_path / "project.db")
    project = Project(brief=ProjectBrief(topic="Strict release"))
    repository.save_project(project)
    run = GenerationRun(
        project_id=project.id,
        status=RunStatus.RUNNING,
        input_hash="input-v1",
        model_policy={"writer": "test-model"},
    )
    repository.save_run(run)
    requirements = RequirementSet(project_id=project.id, revision=1)
    repository.save_requirement_set(requirements)
    blueprint = ProjectBlueprint(
        project_id=project.id,
        topic="Strict release",
        outline=Outline(sections=[SectionSpec(id="body", title="Body")]),
        revision=1,
    )
    repository.save_blueprint(blueprint)
    manuscript = Manuscript(
        project_id=project.id,
        title="Strict release",
        blocks=[ParagraphBlock(text="Verified text")],
    )
    repository.save_manuscript(manuscript)
    docx = tmp_path / "result.docx"
    docx.write_bytes(b"deterministic-docx-fixture")
    digest = hashlib.sha256(docx.read_bytes()).hexdigest()
    artifact = Artifact(
        project_id=project.id,
        run_id=run.id,
        kind=ArtifactKind.DOCX,
        path=str(docx),
        sha256=digest,
        size_bytes=docx.stat().st_size,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        metadata={
            "phase": "final",
            "finalizer": "libreoffice",
            "fields_updated": True,
        },
    )
    repository.save_artifact(artifact)
    report = QAReport(
        project_id=project.id,
        run_id=run.id,
        metadata={
            "deterministic": True,
            "gate_version": 2,
            "release_hashes": {
                "input_hash": run.input_hash,
                "manuscript_hash": stable_hash(manuscript.model_dump(mode="json")),
                "docx_hash": digest,
                "pdf_hash": None,
            },
        },
    )
    repository.save_qa_report(report)
    for name in ("consistency_qa", "final_gemini_review"):
        repository.save_stage(
            StageRun(
                run_id=run.id,
                name=name,
                status=StageStatus.SUCCEEDED,
                checkpoint={"accepted": True, "blocker_issues": [], "factual_issues": []},
            )
        )
    project = repository.get_project(project.id)
    assert project is not None
    release = build_submission_release(
        project=project,
        run=run,
        manuscript=manuscript,
        docx_artifact=artifact,
        report=report,
        requirements=requirements,
        blueprint=blueprint,
        profile=_profile(),
    )
    return repository, project, run, artifact, release


@pytest.mark.parametrize("schema", [SectionCritique, GlobalReview])
def test_model_review_requires_explicit_accepted(schema: type[object]) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate({})  # type: ignore[attr-defined]
    rejected = schema.model_validate({"accepted": False})  # type: ignore[attr-defined]
    assert rejected.accepted is False


def test_profile_policy_minimum_sources_is_release_blocking() -> None:
    manuscript = Manuscript(
        project_id="project",
        title="Minimum sources",
        blocks=[ParagraphBlock(text="Substantive text")],
        bibliography=[BibliographyEntry(title="Only source")],
    )
    report = DeterministicQualityGate().run(
        QAGateContext(
            project_id="project",
            run_id="run",
            manuscript=manuscript,
            profile=_profile(minimum_sources=2),
        )
    )
    assert report.status.value == "fail"
    assert any(issue.category == "profile_minimum_sources" for issue in report.issues)


@pytest.mark.parametrize("severity", [QASeverity.WARNING, QASeverity.ERROR])
def test_warning_and_fail_reports_cannot_build_release(tmp_path: Path, severity: QASeverity) -> None:
    repository, project, run, artifact, _ = _ready_candidate(tmp_path)
    manuscript = repository.get_latest_manuscript(project.id)
    requirements = repository.get_latest_requirement_set(project.id)
    blueprint = repository.get_latest_blueprint(project.id)
    assert manuscript is not None and requirements is not None and blueprint is not None
    report = QAReport(
        project_id=project.id,
        run_id=run.id,
        issues=[QAIssue(severity=severity, category="release", message="Unresolved")],
    )
    with pytest.raises(ReleasePolicyError):
        build_submission_release(
            project=project,
            run=run,
            manuscript=manuscript,
            docx_artifact=artifact,
            report=report,
            requirements=requirements,
            blueprint=blueprint,
            profile=_profile(),
        )


def test_stale_docx_hash_cannot_be_finalized(tmp_path: Path) -> None:
    repository, _, _, artifact, release = _ready_candidate(tmp_path)
    Path(artifact.path).write_bytes(b"changed-after-qa")
    with pytest.raises(ValueError, match="integrity"):
        repository.finalize_submission_release(release, finished_at=release.created_at)


def test_release_builder_rejects_draft_docx_and_stale_qa_scope(tmp_path: Path) -> None:
    repository, project, run, artifact, _ = _ready_candidate(tmp_path)
    manuscript = repository.get_latest_manuscript(project.id)
    requirements = repository.get_latest_requirement_set(project.id)
    blueprint = repository.get_latest_blueprint(project.id)
    report = repository.get_latest_qa_report(run.id)
    assert all(item is not None for item in (manuscript, requirements, blueprint, report))
    assert manuscript is not None
    assert requirements is not None
    assert blueprint is not None
    assert report is not None

    with pytest.raises(ReleasePolicyError, match="finalized DOCX"):
        build_submission_release(
            project=project,
            run=run,
            manuscript=manuscript,
            docx_artifact=artifact.model_copy(
                update={"metadata": {**artifact.metadata, "phase": "draft"}}
            ),
            report=report,
            requirements=requirements,
            blueprint=blueprint,
            profile=_profile(),
        )

    stale_hashes = dict(report.metadata["release_hashes"])
    stale_hashes["docx_hash"] = "0" * 64
    with pytest.raises(ReleasePolicyError, match="hashes do not match"):
        build_submission_release(
            project=project,
            run=run,
            manuscript=manuscript,
            docx_artifact=artifact,
            report=report.model_copy(
                update={
                    "metadata": {
                        **report.metadata,
                        "release_hashes": stale_hashes,
                    }
                }
            ),
            requirements=requirements,
            blueprint=blueprint,
            profile=_profile(),
        )


def test_open_in_word_cannot_bypass_release_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository, project, run, _, _ = _ready_candidate(tmp_path)
    opened = False

    def fake_open(_: object) -> None:
        nonlocal opened
        opened = True

    monkeypatch.setattr("os.startfile", fake_open)
    with pytest.raises(DocumentExportBlocked):
        DocumentService(project.id, repository).open_in_word(run.id)
    assert opened is False


def test_ready_is_atomic_and_source_change_supersedes_release(tmp_path: Path) -> None:
    repository, project, run, _artifact, release = _ready_candidate(tmp_path)
    completed = repository.finalize_submission_release(release, finished_at=release.created_at)
    assert completed.status is RunStatus.SUCCEEDED
    ready_project = repository.get_project(project.id)
    assert ready_project is not None
    assert ready_project.submission_status is SubmissionStatus.READY_TO_SUBMIT
    assert DocumentService(project.id, repository).export_block_reason(ArtifactKind.DOCX, run.id) is None

    repository.save_source(
        Source(
            project_id=project.id,
            role=SourceRole.REFERENCE,
            original_name="changed.txt",
            stored_path="inputs/originals/changed.txt",
            sha256="0" * 64,
            size_bytes=0,
            mime_type="text/plain",
        )
    )
    changed = repository.get_project(project.id)
    assert changed is not None
    assert changed.submission_status is SubmissionStatus.DRAFT
    assert changed.current_release_id is None
    assert repository.get_release(release.id).status.value == "SUPERSEDED"  # type: ignore[union-attr]
    assert DocumentService(project.id, repository).export_block_reason(ArtifactKind.DOCX, run.id)


def test_succeeded_cannot_be_persisted_without_release(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "project.db")
    project = Project(brief=ProjectBrief(topic="No false success"))
    repository.save_project(project)
    with pytest.raises(ValueError, match="finalize_submission_release"):
        repository.save_run(GenerationRun(project_id=project.id, status=RunStatus.SUCCEEDED))


@pytest.mark.parametrize("mutation", ["profile", "section", "model"])
def test_release_affecting_mutations_clear_ready(tmp_path: Path, mutation: str) -> None:
    repository, project, _, _, release = _ready_candidate(tmp_path)
    repository.finalize_submission_release(release, finished_at=release.created_at)

    if mutation == "profile":
        changed = repository.get_project(project.id)
        assert changed is not None
        changed.brief.domain_profile = DomainProfile.IT
        repository.save_project(changed)
    elif mutation == "section":
        manuscript = repository.get_latest_manuscript(project.id)
        assert manuscript is not None
        repository.commit_section_override(
            manuscript.model_copy(
                update={
                    "id": "edited-manuscript",
                    "revision": manuscript.revision + 1,
                }
            ),
            "body",
            '{"source":"user","text":"edited"}',
        )
    else:
        repository.save_run(
            GenerationRun(
                project_id=project.id,
                input_hash="input-v2",
                model_policy={"writer": "changed-model"},
            )
        )

    changed = repository.get_project(project.id)
    assert changed is not None
    assert changed.submission_status is SubmissionStatus.DRAFT
    assert changed.current_release_id is None
    superseded = repository.get_release(release.id)
    assert superseded is not None and superseded.status.value == "SUPERSEDED"
