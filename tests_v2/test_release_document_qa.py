from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest
from docx import Document

from papercraft.application import (
    PipelineStage,
    ProductionStageFactory,
    ProjectService,
    StageContext,
)
from papercraft.application.release import stable_hash
from papercraft.application.worker_control import CancellationToken
from papercraft.config import AppSettings
from papercraft.domain import (
    ArtifactKind,
    GenerationRun,
    HeadingBlock,
    Manuscript,
    ParagraphBlock,
    ProjectBrief,
    QAStatus,
    RunStatus,
    StageRun,
)
from papercraft.infrastructure.gemini import FakeGeminiGateway
from papercraft.infrastructure.persistence import AtomicArtifactStore
from papercraft.infrastructure.qa import (
    DeterministicQualityGate,
    QAGateContext,
    inspect_docx_package,
    inspect_pdf_layout,
)
from papercraft.infrastructure.render import (
    DocxRenderer,
    FinalizationResult,
    PDFResult,
    RenderConfig,
)
from papercraft.profiles.models import ProfilePolicy, ProfileSectionTemplate, WorkProfile


def _profile() -> WorkProfile:
    return WorkProfile(
        id="release-document-qa",
        display_name="Release document QA",
        work_type="coursework",
        description="Module 9 fixture",
        sections=[
            ProfileSectionTemplate(key="body", title="Body", target_words=100, purpose="QA")
        ],
        policy=ProfilePolicy(voice="academic", minimum_sources=0),
    )


def _manuscript() -> Manuscript:
    return Manuscript(
        project_id="project-9",
        title="Release document",
        blocks=[
            HeadingBlock(text="INTRODUCTION", level=1),
            ParagraphBlock(text="The finalized document contains verified release content."),
        ],
    )


def test_release_qa_binds_exact_input_manuscript_and_docx_hashes(tmp_path: Path) -> None:
    manuscript = _manuscript()
    docx = tmp_path / "final.docx"
    rendered = DocxRenderer(RenderConfig(include_toc=False)).render(manuscript, docx)
    manuscript_hash = stable_hash(manuscript.model_dump(mode="json"))

    report = DeterministicQualityGate().run(
        QAGateContext(
            project_id=manuscript.project_id,
            run_id="run-9",
            manuscript=manuscript,
            profile=_profile(),
            docx_path=docx,
            input_hash="input-hash-9",
            expected_manuscript_hash=manuscript_hash,
            expected_docx_hash=rendered.sha256,
            docx_finalized=True,
        )
    )

    assert report.status is QAStatus.PASS, [issue.message for issue in report.issues]
    assert report.metadata["release_hashes"] == {
        "input_hash": "input-hash-9",
        "manuscript_hash": manuscript_hash,
        "docx_hash": rendered.sha256,
        "pdf_hash": None,
    }

    stale = DeterministicQualityGate().run(
        QAGateContext(
            project_id=manuscript.project_id,
            run_id="run-9",
            manuscript=manuscript,
            profile=_profile(),
            docx_path=docx,
            expected_manuscript_hash="0" * 64,
            expected_docx_hash="1" * 64,
        )
    )
    assert stale.status is QAStatus.FAIL
    assert {issue.category for issue in stale.issues} >= {"docx_hash", "manuscript_hash"}


def test_openxml_inspection_rejects_active_content_and_external_links(tmp_path: Path) -> None:
    docx = tmp_path / "unsafe.docx"
    DocxRenderer(RenderConfig(include_toc=False)).render(_manuscript(), docx)
    with zipfile.ZipFile(docx, "a") as archive:
        archive.writestr("word/vbaProject.bin", b"macro")
        archive.writestr(
            "word/_rels/unsafe.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.org/tracker" TargetMode="External"/>
</Relationships>""",
        )

    inspection = inspect_docx_package(docx)

    assert inspection.forbidden_parts == ("word/vbaProject.bin",)
    assert inspection.external_relationships == ("https://example.org/tracker",)
    assert inspection.sha256 == hashlib.sha256(docx.read_bytes()).hexdigest()
    report = DeterministicQualityGate().run(
        QAGateContext(
            project_id="project-9",
            run_id="run-unsafe",
            manuscript=_manuscript(),
            profile=_profile(),
            docx_path=docx,
        )
    )
    assert report.status is QAStatus.FAIL
    assert {issue.category for issue in report.issues} >= {
        "docx_active_content",
        "docx_external_link",
    }


def test_corrupt_docx_is_release_blocking(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.docx"
    corrupt.write_bytes(b"not-an-openxml-package")

    report = DeterministicQualityGate().run(
        QAGateContext(
            project_id="project-9",
            run_id="run-corrupt",
            manuscript=_manuscript(),
            profile=_profile(),
            docx_path=corrupt,
        )
    )

    assert report.status is QAStatus.FAIL
    assert any(issue.category == "docx" for issue in report.issues)


def test_page_layout_inspection_finds_blank_orphan_and_cropped_caption(tmp_path: Path) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    pdf = tmp_path / "layout.pdf"
    document = pymupdf.open()
    document.new_page(width=595, height=842)
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 790), "ORPHAN HEADING", fontsize=14)
    caption_page = document.new_page(width=595, height=842)
    caption_page.insert_text((72, 838), "Figure 1 – clipped", fontsize=12)
    document.save(pdf)
    document.close()

    findings = inspect_pdf_layout(pdf)
    categories = {(finding.category, finding.page) for finding in findings}

    assert ("blank_page", 1) in categories
    assert ("orphan_heading", 2) in categories
    assert ("cropped_caption", 3) in categories


def test_pipeline_keeps_draft_and_emits_final_docx_with_internal_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(ProjectBrief(title="Module 9", topic="Module 9"))
    manuscript = _manuscript().model_copy(update={"project_id": workspace.project.id})
    workspace.repository.save_manuscript(manuscript)
    run = GenerationRun(
        project_id=workspace.project.id,
        status=RunStatus.RUNNING,
        input_hash="module-9-input",
    )
    workspace.repository.save_run(run)
    factory = ProductionStageFactory(FakeGeminiGateway())

    render_stage = StageRun(
        run_id=run.id,
        name=PipelineStage.RENDER_DOCX.value,
        order=11,
        status="running",
    )
    workspace.repository.save_stage(render_stage)
    render_context = StageContext(
        settings=settings,
        project=workspace.project,
        run=run,
        stage=render_stage,
        paths=workspace.paths,
        repository=workspace.repository,
        artifact_store=AtomicArtifactStore(workspace.paths.artifacts),
        cancellation=CancellationToken(workspace.repository, run.id, render_stage.id),
    )
    draft_outcome = factory.render_docx(render_context)
    draft = draft_outcome.artifacts[0]
    workspace.repository.save_artifact(draft)
    draft_bytes = Path(draft.path).read_bytes()

    class FakeLibreOffice:
        def finalize_copy(
            self,
            draft_docx_path: str | Path,
            final_docx_path: str | Path,
            *,
            pdf_path: str | Path,
        ) -> FinalizationResult:
            final = Path(final_docx_path)
            final.parent.mkdir(parents=True, exist_ok=True)
            final.write_bytes(Path(draft_docx_path).read_bytes())
            document = Document(final)
            for paragraph in document.paragraphs:
                if "Обновите оглавление" in paragraph.text:
                    paragraph.text = "Оглавление обновлено"
            document.save(final)
            pdf = Path(pdf_path)
            pdf.write_bytes(b"%PDF-1.7\nfixture")
            return FinalizationResult(
                final,
                PDFResult(pdf, pdf.stat().st_size, True),
                "libreoffice",
                True,
            )

    monkeypatch.setattr("papercraft.infrastructure.render.DocumentFinalizer", FakeLibreOffice)
    final_stage = StageRun(
        run_id=run.id,
        name=PipelineStage.WORD_FINALIZE.value,
        order=12,
        status="running",
    )
    workspace.repository.save_stage(final_stage)
    final_context = StageContext(
        settings=settings,
        project=workspace.project,
        run=run,
        stage=final_stage,
        paths=workspace.paths,
        repository=workspace.repository,
        artifact_store=AtomicArtifactStore(workspace.paths.artifacts),
        cancellation=CancellationToken(workspace.repository, run.id, final_stage.id),
    )
    final_outcome = factory.word_finalize(final_context)

    assert Path(draft.path).read_bytes() == draft_bytes
    assert [artifact.kind for artifact in final_outcome.artifacts] == [
        ArtifactKind.DOCX,
        ArtifactKind.PDF,
    ]
    final_docx, internal_pdf = final_outcome.artifacts
    assert final_docx.metadata["phase"] == "final"
    assert final_docx.metadata["draft_artifact_id"] == draft.id
    assert internal_pdf.metadata["user_exportable"] is False
    assert Path(final_docx.path).is_file() and Path(internal_pdf.path).is_file()
