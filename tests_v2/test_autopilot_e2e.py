import hashlib
from pathlib import Path

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
    ProjectBrief,
    RunStatus,
    SourceRole,
)
from papercraft.infrastructure.gemini import FakeGeminiGateway, GroundedResult
from papercraft.infrastructure.research import URLVerificationResult


class AlwaysVerifiedURL:
    def verify(self, url: str) -> URLVerificationResult:
        body = b"<html><title>Authoritative source</title><body>Automation improves reproducibility when the process is deterministic.</body></html>"
        return URLVerificationResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            content_length=len(body),
            content_sha256=hashlib.sha256(body).hexdigest(),
            verified=True,
            title="Authoritative source",
            body=body,
        )


def test_full_autopilot_produces_docx_and_qa(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(
        ProjectBrief(
            title="Test work",
            topic="Reliable automated systems",
            prompt="Prepare a concise evidence-backed academic work",
        ),
        AutopilotOptions(
            consent_to_remote_processing=True,
            generate_pdf=False,
            maximum_revision_cycles=2,
        ),
    )
    methodology = tmp_path / "methodology.txt"
    methodology.write_text("The work must contain an introduction and conclusion.", encoding="utf-8")
    SourceService(workspace).import_files([methodology], SourceRole.METHODOLOGY)

    fake = FakeGeminiGateway()
    fake.enqueue("generate_structured", {"rules": [], "conflicts": []})
    fake.enqueue(
        "generate_structured",
        {
            "claims": [
                {
                    "text": "Automation improves reproducibility",
                    "search_query": "automation reproducibility official source",
                    "importance": "critical",
                }
            ]
        },
    )
    fake.enqueue(
        "search_grounded",
        GroundedResult(
            text="Automation improves reproducibility when the process is deterministic.",
            model="fake-architect",
            annotations=[
                {
                    "type": "url_citation",
                    "url": "https://example.org/research",
                    "title": "Authoritative source",
                }
            ],
        ),
    )
    fake.enqueue(
        "generate_structured",
        {
            "claim_supported": True,
            "supported_urls": ["https://example.org/research"],
            "confidence": 0.95,
            "rationale": "The cited sentence directly supports the claim.",
            "evidence_quote": "Automation improves reproducibility when the process is deterministic.",
            "locator_hint": "body",
        },
    )
    fake.enqueue(
        "generate_structured",
        {
            "topic": "Reliable automated systems",
            "goal": "Evaluate reproducible automation",
            "tasks": ["Review evidence"],
            "sections": [
                {
                    "key": "introduction",
                    "title": "INTRODUCTION",
                    "order": 0,
                    "target_words": 100,
                    "theses": ["Automation can be reproducible"],
                    "required_claim_texts": ["Automation improves reproducibility"],
                    "source_ids": [],
                    "visuals": [],
                    "expected_conclusion": "Automation improves repeatability.",
                }
            ],
        },
    )
    fake.enqueue("generate_structured", {"synthetic_datasets": []})

    def section_response(**_kwargs):
        claim = workspace.repository.list_claims(workspace.project.id)[0]
        entry = workspace.repository.list_bibliography(workspace.project.id)[0]
        section = workspace.repository.get_latest_blueprint(workspace.project.id).outline.sections[0]
        return {
            "section_id": section.id,
            "blocks": [
                {
                    "type": "paragraph",
                    "text": " ".join(["Automation improves reproducibility through deterministic stages."] * 12),
                    "claim_ids": [claim.id],
                    "bibliography_entry_ids": [entry.id],
                }
            ],
            "conclusion": "The evidence supports reproducible automation.",
            "word_count": 96,
        }

    fake.enqueue("generate_structured", section_response)
    fake.enqueue("generate_structured", {"accepted": True})
    fake.enqueue("generate_structured", {"accepted": True})
    fake.enqueue("generate_structured", {"accepted": True})

    factory = ProductionStageFactory(fake, url_verifier=AlwaysVerifiedURL())
    service = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        factory.build(),
        terminal_hook=factory.cleanup_remote_files,
    )
    run = service.start()
    assert run.status == RunStatus.SUCCEEDED, run.error
    artifacts = workspace.repository.list_artifacts(workspace.project.id, run_id=run.id)
    assert any(item.kind == ArtifactKind.DOCX and Path(item.path).is_file() for item in artifacts)
    assert any(item.kind == ArtifactKind.QA_HTML and Path(item.path).is_file() for item in artifacts)
    assert fake.deleted_files
    resources = workspace.repository.list_remote_resources(run.id)
    assert resources
    assert all(item.deleted_at is not None for item in resources)
