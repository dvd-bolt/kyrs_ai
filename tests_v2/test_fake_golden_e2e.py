"""Six structural golden projects exercised entirely through FakeGemini."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from papercraft.application import (
    AutopilotService,
    DocumentExportBlocked,
    DocumentService,
    ProductionStageFactory,
    ProjectService,
    ProjectWorkspace,
    SourceService,
)
from papercraft.config import AppSettings
from papercraft.domain import (
    ArtifactKind,
    AutopilotOptions,
    DomainProfile,
    ProjectBrief,
    QASeverity,
    RequirementPriority,
    RunStatus,
    SourceRole,
    StageStatus,
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
            AutopilotOptions(
                consent_to_remote_processing=True,
                generate_pdf=True,
                maximum_revision_cycles=2,
                allow_synthetic_data=True,
            ),
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
    report = workspace.repository.get_latest_qa_report(run.id)
    assert report is not None
    assert report.status.value in {"pass", "warning"}
    documents = DocumentService(workspace.project.id, workspace.repository)
    assert documents.export_block_reason(ArtifactKind.DOCX, run.id) is None
    assert documents.export_block_reason(ArtifactKind.PDF, run.id) is None
    assert documents.export(ArtifactKind.DOCX, tmp_path / "released.docx", run.id).is_file()
    assert documents.export(ArtifactKind.PDF, tmp_path / "released.pdf", run.id).is_file()
    # A later successful requirement extraction makes the existing release
    # stale even before a subsequent run reaches planning or rendering.
    requirements = workspace.repository.get_latest_requirement_set(workspace.project.id)
    assert requirements is not None
    workspace.repository.save_requirement_set(
        requirements.model_copy(update={"id": "requirements-after-release"})
    )
    assert documents.export_block_reason(ArtifactKind.DOCX, run.id) is not None
    assert documents.export_block_reason(ArtifactKind.PDF, run.id) is not None
    with pytest.raises(DocumentExportBlocked, match="current requirements"):
        documents.export(ArtifactKind.DOCX, tmp_path / "stale-requirements.docx", run.id)
    with pytest.raises(DocumentExportBlocked, match="current requirements"):
        documents.export(ArtifactKind.PDF, tmp_path / "stale-requirements.pdf", run.id)
    # The deterministic profile scaffold is intentionally visible in the
    # release report, even when this compact smoke blueprint omits some of
    # its suggested sections. Profile-only gaps cannot quietly turn an
    # otherwise evidence-backed project into an unexportable document.
    assert report.requirement_coverage is not None
    profile_entries = [
        entry
        for entry in report.requirement_coverage.entries
        if entry.priority is RequirementPriority.PROFILE
    ]
    assert profile_entries
    assert all(entry.criticality == "standard" for entry in profile_entries)
    assert not [
        issue
        for issue in report.issues
        if issue.category == "requirement_coverage" and issue.severity is QASeverity.BLOCKER
    ]
    assert fake.deleted_files
    if profile is DomainProfile.ACCOUNTING:
        facts = workspace.repository.list_facts(workspace.project.id)
        assert facts
        assert {str(item.metadata.get("column")) for item in facts} >= {
            "debit_account",
            "credit_account",
            "amount",
        }


def test_formal_methodology_gap_blocks_final_package_and_persists_qa_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A formal uncovered rule must fail release without discarding its QA proof.

    The document and PDF are intentionally produced before the final package
    gate: coverage includes locations in those artifacts.  The assertion here
    proves they cannot be treated as a released export once a methodology rule
    remains unconfirmed, and that a restarted desktop can still show the
    diagnostic report.
    """

    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(
        ProjectBrief(
            title="formal-coverage-gap",
            topic="formal-coverage-gap",
            prompt="Produce an evidence-backed work",
            work_type=WorkType.COURSEWORK,
            domain_profile=DomainProfile.IT,
        ),
        AutopilotOptions(
            consent_to_remote_processing=True,
            generate_pdf=True,
            maximum_revision_cycles=2,
            allow_synthetic_data=True,
        ),
    )
    source = tmp_path / "methodology.txt"
    source.write_text("A formal appendix is required.", encoding="utf-8")
    SourceService(workspace).import_files([source], SourceRole.METHODOLOGY)
    monkeypatch.setattr("papercraft.infrastructure.render.DocumentFinalizer", _FakeLocalFinalizer)
    fake = _fake_for(
        workspace.project.id,
        workspace,
        accounting=False,
        requirement_rules=[
            {
                "category": "custom",
                "key": "methodology.required_appendix",
                "statement": "The methodology requires an explicit appendix.",
                "value": "appendix",
                "mandatory": True,
                "priority": "methodology",
            }
        ],
    )
    factory = ProductionStageFactory(fake, url_verifier=_VerifiedURL())
    run = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        factory.build(),
        terminal_hook=factory.cleanup_remote_files,
    ).start()

    assert run.status is RunStatus.FAILED
    assert run.error is not None and run.error.startswith("package:")
    package_stage = next(
        stage for stage in workspace.repository.list_stages(run.id) if stage.name == "package"
    )
    assert package_stage.status is StageStatus.FAILED

    # The pre-package render exists for traceability, but the failed package
    # means it is not a release.  QA artifacts are explicitly saved by the
    # package handler before it raises, rather than being lost with StageOutcome.
    artifacts = workspace.repository.list_artifacts(workspace.project.id, run_id=run.id)
    assert {ArtifactKind.DOCX, ArtifactKind.PDF} <= {artifact.kind for artifact in artifacts}
    qa_artifacts = [
        artifact
        for artifact in artifacts
        if artifact.kind in {ArtifactKind.QA_JSON, ArtifactKind.QA_HTML}
    ]
    assert {artifact.kind for artifact in qa_artifacts} == {
        ArtifactKind.QA_JSON,
        ArtifactKind.QA_HTML,
    }
    assert all(Path(artifact.path).is_file() for artifact in qa_artifacts)

    report = workspace.repository.get_latest_qa_report(run.id)
    assert report is not None
    assert report.status.value == "fail"
    assert report.requirement_coverage is not None
    entry = next(
        item
        for item in report.requirement_coverage.entries
        if item.requirement_key == "methodology.required_appendix"
    )
    assert entry.priority is RequirementPriority.METHODOLOGY
    assert entry.criticality == "critical"
    assert entry.status == "partial"
    blockers = [
        issue
        for issue in report.issues
        if issue.category == "requirement_coverage"
        and issue.requirement_rule_id == entry.requirement_rule_id
    ]
    assert len(blockers) == 1
    assert blockers[0].severity is QASeverity.BLOCKER
    assert blockers[0].metadata["coverage_status"] == "partial"
    documents = DocumentService(workspace.project.id, workspace.repository)
    assert documents.export_block_reason(ArtifactKind.DOCX, run.id) is not None
    assert documents.export_block_reason(ArtifactKind.PDF, run.id) is not None
    with pytest.raises(DocumentExportBlocked, match="Export is blocked"):
        documents.export(ArtifactKind.DOCX, tmp_path / "blocked.docx", run.id)
    with pytest.raises(DocumentExportBlocked, match="Export is blocked"):
        documents.export(ArtifactKind.PDF, tmp_path / "blocked.pdf", run.id)
    assert not (tmp_path / "blocked.docx").exists()
    assert not (tmp_path / "blocked.pdf").exists()


def test_formal_renderer_requirements_are_covered_by_the_final_docx_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical layout requirements must not false-block a real render."""

    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(
        ProjectBrief(
            title="renderer-coverage",
            topic="renderer-coverage",
            prompt="Produce an evidence-backed work",
            work_type=WorkType.COURSEWORK,
            domain_profile=DomainProfile.IT,
            title_page={"student": "Test Student", "city": "Moscow"},
        ),
        AutopilotOptions(
            consent_to_remote_processing=True,
            generate_pdf=True,
            maximum_revision_cycles=2,
            allow_synthetic_data=True,
        ),
    )
    source = tmp_path / "methodology.txt"
    source.write_text("Use the mandatory document layout.", encoding="utf-8")
    SourceService(workspace).import_files([source], SourceRole.METHODOLOGY)
    monkeypatch.setattr("papercraft.infrastructure.render.DocumentFinalizer", _FakeLocalFinalizer)
    fake = _fake_for(
        workspace.project.id,
        workspace,
        accounting=False,
        requirement_rules=[
            {
                "category": "title_page",
                "key": "methodology.title_page",
                "statement": "A title page is mandatory.",
                "value": "required",
                "mandatory": True,
                "priority": "methodology",
            },
            {
                "category": "typography",
                "key": "methodology.font_name",
                "statement": "Use Times New Roman.",
                "value": "Times New Roman",
                "mandatory": True,
                "priority": "methodology",
            },
            {
                "category": "page_layout",
                "key": "methodology.margin_left_cm",
                "statement": "Set the left margin to 3 cm.",
                "value": 3.0,
                "mandatory": True,
                "priority": "methodology",
            },
            {
                "category": "structure",
                "key": "methodology.include_toc",
                "statement": "Include a table of contents.",
                "value": True,
                "mandatory": True,
                "priority": "methodology",
            },
        ],
    )
    factory = ProductionStageFactory(fake, url_verifier=_VerifiedURL())
    run = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        factory.build(),
        terminal_hook=factory.cleanup_remote_files,
    ).start()

    assert run.status is RunStatus.SUCCEEDED, run.error
    report = workspace.repository.get_latest_qa_report(run.id)
    assert report is not None and report.requirement_coverage is not None
    formal_entries = [
        entry
        for entry in report.requirement_coverage.entries
        if entry.priority is RequirementPriority.METHODOLOGY
    ]
    assert len(formal_entries) == 4
    assert all(entry.status == "covered" for entry in formal_entries)
    assert all(entry.artifact_id for entry in formal_entries)


def _fake_for(
    project_id: str,
    workspace: ProjectWorkspace,
    *,
    accounting: bool,
    requirement_rules: list[dict[str, object]] | None = None,
) -> FakeGeminiGateway:
    fake = FakeGeminiGateway()
    fake.enqueue("generate_structured", {"rules": requirement_rules or [], "conflicts": []})
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
        return {"section_id": section.id, "blocks": [{"type": "paragraph", "text": " ".join(["The process is reproducible through deterministic evidence."] * 15), "claim_ids": [claim.id], "bibliography_entry_ids": [entry.id]}], "conclusion": "The process is reproducible.", "word_count": 105}

    fake.enqueue("generate_structured", section_response)
    fake.enqueue("generate_structured", {"accepted": True})
    fake.enqueue("generate_structured", {"accepted": True})
    fake.enqueue("generate_structured", {"issues": []})
    fake.enqueue("generate_structured", {"accepted": True})
    return fake


class _FakeLocalFinalizer:
    """Local PDF fixture: tests the LibreOffice beta pipeline wiring."""

    def word_available(self) -> bool:
        return False

    def libreoffice_available(self) -> bool:
        return True

    def finalize(
        self,
        docx_path: str | Path,
        *,
        pdf_path: str | Path | None = None,
        preferred: str = "libreoffice",
        require_pdf: bool = True,
        allow_unfinalized: bool = False,
    ) -> FinalizationResult:
        assert preferred == "libreoffice"
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
