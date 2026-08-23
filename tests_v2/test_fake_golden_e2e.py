"""Six structural golden projects exercised entirely through FakeGemini."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from papercraft.application import (
    AutopilotService,
    ProductionStageFactory,
    ProjectService,
    SourceService,
)
from papercraft.config import AppSettings
from papercraft.domain import (
    ArtifactKind,
    AutopilotOptions,
    DomainProfile,
    ProjectBrief,
    RunStatus,
    SourceRole,
    WorkType,
)
from papercraft.infrastructure.gemini import FakeGeminiGateway, GroundedResult
from papercraft.infrastructure.render import FinalizationResult, PDFResult
from papercraft.infrastructure.research import URLVerificationResult


class _VerifiedURL:
    def verify(self, url: str) -> URLVerificationResult:
        body = b"<html><title>Golden source</title><body>The process is reproducible.</body></html>"
        return URLVerificationResult(
            requested_url=url, final_url=url, status_code=200, content_type="text/html", content_length=len(body),
            content_sha256=hashlib.sha256(body).hexdigest(), verified=True, title="Golden source", body=body
        )


@pytest.mark.parametrize(
    ("golden", "work_type", "profile"),
    [
        ("it_coursework", WorkType.COURSEWORK, DomainProfile.IT),
        ("finance_coursework", WorkType.COURSEWORK, DomainProfile.FINANCE),
        ("scientific_article", WorkType.SCIENTIFIC_ARTICLE, DomainProfile.SCIENCE),
        ("programming_practice_report", WorkType.PRACTICE_REPORT, DomainProfile.PROGRAMMING),
        ("accounting_practice_report", WorkType.PRACTICE_REPORT, DomainProfile.ACCOUNTING),
        ("school_project", WorkType.SCHOOL_PROJECT, DomainProfile.SCHOOL),
    ],
)
def test_fake_golden_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    golden: str,
    work_type: WorkType,
    profile: DomainProfile,
) -> None:
    assert (Path("tests_golden") / golden / "manifest.yaml").is_file()
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(
        ProjectBrief(title=golden, topic=golden, prompt="Produce an evidence-backed work", work_type=work_type, domain_profile=profile),
        AutopilotOptions(consent_to_remote_processing=True, generate_pdf=True, maximum_revision_cycles=2),
    )
    source = tmp_path / "methodology.txt"
    source.write_text("Work requires an introduction and a conclusion.", encoding="utf-8")
    SourceService(workspace).import_files([source], SourceRole.METHODOLOGY)
    monkeypatch.setattr("papercraft.infrastructure.render.DocumentFinalizer", _FakeLocalFinalizer)
    fake = _fake_for(workspace.project.id, workspace, accounting=profile is DomainProfile.ACCOUNTING)
    factory = ProductionStageFactory(fake, url_verifier=_VerifiedURL())
    run = AutopilotService(settings, workspace.project, workspace.repository, workspace.paths, factory.build(), terminal_hook=factory.cleanup_remote_files).start()
    assert run.status is RunStatus.SUCCEEDED, run.error
    artifacts = workspace.repository.list_artifacts(workspace.project.id, run_id=run.id)
    assert any(item.kind is ArtifactKind.DOCX and Path(item.path).is_file() for item in artifacts)
    assert any(item.kind is ArtifactKind.PDF and Path(item.path).is_file() for item in artifacts)
    assert any(item.kind is ArtifactKind.QA_HTML and Path(item.path).is_file() for item in artifacts)
    assert workspace.repository.get_latest_manuscript(workspace.project.id) is not None
    assert workspace.repository.get_latest_qa_report(run.id).status.value in {"pass", "warning"}  # type: ignore[union-attr]
    assert fake.deleted_files
    if profile is DomainProfile.ACCOUNTING:
        facts = workspace.repository.list_facts(workspace.project.id)
        assert facts
        assert {str(item.metadata.get("column")) for item in facts} >= {
            "debit_account",
            "credit_account",
            "amount",
        }


def _fake_for(project_id: str, workspace: object, *, accounting: bool) -> FakeGeminiGateway:
    fake = FakeGeminiGateway()
    fake.enqueue("generate_structured", {"rules": [], "conflicts": []})
    fake.enqueue("generate_structured", {"claims": [{"text": "The process is reproducible", "search_query": "reproducibility source", "importance": "critical"}]})
    fake.enqueue("search_grounded", GroundedResult(text="The process is reproducible.", model="fake", annotations=[{"type": "url_citation", "url": "https://example.org/golden", "title": "Golden source"}]))
    fake.enqueue("generate_structured", {"claim_supported": True, "supported_urls": ["https://example.org/golden"], "confidence": 1, "rationale": "direct support", "evidence_quote": "The process is reproducible.", "locator_hint": "body"})
    fake.enqueue("generate_structured", {"topic": "Golden topic", "goal": "Verify a deterministic run", "tasks": ["Validate evidence"], "sections": [{"key": "introduction", "title": "INTRODUCTION", "order": 0, "target_words": 100, "theses": ["Reproducibility"], "required_claim_texts": ["The process is reproducible"], "source_ids": [], "visuals": [], "expected_conclusion": "The run is reproducible."}]})
    fake.enqueue(
        "generate_structured",
        {
            "synthetic_datasets": [
                {
                    "name": "Journal",
                    "purpose": "balanced accounting journal",
                    "row_count": 1,
                    "seed": 7,
                    "columns": [
                        {"name": "debit_account", "data_type": "string", "distribution": "choice", "parameters": {"choices": ["51"]}},
                        {"name": "credit_account", "data_type": "string", "distribution": "choice", "parameters": {"choices": ["60"]}},
                        {"name": "amount", "data_type": "number", "distribution": "sequence", "parameters": {"start": 100, "step": 0}},
                    ],
                }
            ]
            if accounting
            else []
        },
    )

    def section_response(**_: object) -> dict[str, object]:
        repository = workspace.repository
        claim = repository.list_claims(project_id)[0]
        entry = repository.list_bibliography(project_id)[0]
        section = repository.get_latest_blueprint(project_id).outline.sections[0]
        return {"section_id": section.id, "blocks": [{"type": "paragraph", "text": " ".join(["The process is reproducible through deterministic evidence."] * 12), "claim_ids": [claim.id], "bibliography_entry_ids": [entry.id]}], "conclusion": "The process is reproducible.", "word_count": 96}

    fake.enqueue("generate_structured", section_response)
    fake.enqueue("generate_structured", {"accepted": True})
    fake.enqueue("generate_structured", {"accepted": True})
    fake.enqueue("generate_structured", {"issues": []})
    fake.enqueue("generate_structured", {"accepted": True})
    return fake


class _FakeLocalFinalizer:
    """Local PDF fixture: tests pipeline wiring, not Word/LibreOffice quality."""

    def word_available(self) -> bool:
        return True

    def libreoffice_available(self) -> bool:
        return False

    def finalize(
        self,
        docx_path: str | Path,
        *,
        pdf_path: str | Path | None = None,
        preferred: str = "auto",
        require_pdf: bool = True,
        allow_unfinalized: bool = False,
    ) -> FinalizationResult:
        docx = Path(docx_path)
        if not require_pdf:
            return FinalizationResult(docx, None, "none", False)
        pdf = Path(pdf_path) if pdf_path else docx.with_suffix(".pdf")
        import pymupdf

        pdf.parent.mkdir(parents=True, exist_ok=True)
        document = pymupdf.open()
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 100), "PaperCraft deterministic PDF fixture", fontsize=12)
        document.save(pdf)
        document.close()
        return FinalizationResult(docx, PDFResult(pdf, pdf.stat().st_size, True), "none", False)
