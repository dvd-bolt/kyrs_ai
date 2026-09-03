from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from papercraft.application.stages import StageExecutionError, _snapshot_fetch_url
from papercraft.domain import (
    BibliographyEntry,
    Claim,
    ClaimStatus,
    Evidence,
    Locator,
    Manuscript,
    ParagraphBlock,
    Project,
    ProjectBrief,
    Source,
    SourceRole,
    SourceSnapshot,
)
from papercraft.infrastructure.persistence import SQLiteRepository
from papercraft.infrastructure.render import DocxRenderer
from papercraft.infrastructure.research import (
    ScholarlyDiscovery,
    ScholarlyRecord,
    final_text_claims,
    validate_scholarly_record,
)


def test_doi_candidate_snapshots_the_resolved_work_not_metadata_api() -> None:
    candidate = ScholarlyRecord(
        title="Verified research",
        landing_url="https://publisher.example/work",
        source_api="crossref",
        doi="10.1000/example.1",
    )
    assert _snapshot_fetch_url(candidate) == "https://doi.org/10.1000/example.1"


def test_scholarly_metadata_validates_doi_url_author_year_and_title() -> None:
    invalid = ScholarlyRecord(
        title="x",
        landing_url="not-a-url",
        source_api="openalex",
        authors=("",),
        year=999,
        doi="not-a-doi",
    )
    result = validate_scholarly_record(invalid)
    assert not result.valid
    assert set(result.errors) == {
        "invalid-title",
        "invalid-url",
        "invalid-doi",
        "implausible-year",
        "invalid-author",
    }


def test_scholarly_discovery_keeps_available_api_when_the_other_is_down() -> None:
    class Down:
        def search(self, query: str, *, rows: int) -> list[ScholarlyRecord]:
            raise OSError("service unavailable")

    class Available:
        def search(self, query: str, *, per_page: int) -> list[ScholarlyRecord]:
            return [
                ScholarlyRecord(
                    title="Available publication",
                    landing_url="https://example.org/publication",
                    source_api="openalex",
                    authors=("Ada Lovelace",),
                    year=2025,
                )
            ]

    records = ScholarlyDiscovery(Down(), Available()).search("reproducibility", limit=1)  # type: ignore[arg-type]
    assert [record.title for record in records] == ["Available publication"]


def test_final_text_claims_are_not_silently_treated_as_supported() -> None:
    claims = final_text_claims(
        "project-1",
        "В 2025 году точность модели увеличилась на 12%. Это полезная идея.",
        block_id="paragraph-1",
    )
    assert [claim.text for claim in claims] == ["В 2025 году точность модели увеличилась на 12%."]
    assert claims[0].status is ClaimStatus.PENDING
    assert claims[0].metadata["origin"] == "final_text"


def test_snapshot_repository_rejects_replacement(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "project.db")
    project = Project(brief=ProjectBrief(topic="Lineage"))
    repository.save_project(project)
    source = Source(
        project_id=project.id,
        role=SourceRole.REFERENCE,
        original_name="source.html",
        stored_path="derived/source.html",
        sha256="a" * 64,
        size_bytes=1,
        mime_type="text/html",
    )
    repository.save_source(source)
    snapshot = SourceSnapshot(
        project_id=project.id,
        source_id=source.id,
        canonical_url="https://example.org/source",
        final_url="https://example.org/source",
        stored_path="derived/snapshot.html",
        sha256="b" * 64,
        content_type="text/html",
        size_bytes=1,
        accessed_at=datetime.now(UTC),
    )
    repository.save_source_snapshot(snapshot)
    repository.save_source_snapshot(snapshot)
    with pytest.raises(ValueError, match="immutable"):
        repository.save_source_snapshot(snapshot.model_copy(update={"sha256": "c" * 64}))


def test_canonical_bibliography_replaces_duplicates_before_source_minimums(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "project.db")
    project = Project(brief=ProjectBrief(topic="Bibliography"))
    repository.save_project(project)
    duplicate = BibliographyEntry(id="duplicate", title="Same source")
    retained = BibliographyEntry(id="retained", title="Same source")
    repository.save_bibliography_entry(project.id, duplicate)
    repository.save_bibliography_entry(project.id, retained)
    repository.replace_bibliography_entries(project.id, [retained])
    assert repository.list_bibliography(project.id) == [retained]


def test_scientific_article_header_renders_ru_en_title_abstract_and_keywords(tmp_path: Path) -> None:
    output = tmp_path / "article.docx"
    manuscript = Manuscript(
        project_id="project-1",
        title="Русское название",
        metadata={
            "work_type": "scientific_article",
            "scientific_article": {
                "title_ru": "Русское название",
                "title_en": "English title",
                "abstract_ru": "Русская аннотация.",
                "abstract_en": "English abstract.",
                "keywords_ru": ["исследование", "доказательства"],
                "keywords_en": ["research", "evidence"],
            },
        },
    )
    DocxRenderer().render(manuscript, output)
    from docx import Document

    text = "\n".join(paragraph.text for paragraph in Document(output).paragraphs)
    assert "РУССКОЕ НАЗВАНИЕ" in text
    assert "ENGLISH TITLE" in text
    assert "Русская аннотация." in text
    assert "English abstract." in text
    assert "Ключевые слова: исследование, доказательства" in text
    assert "Keywords: research, evidence" in text


def test_citation_without_verified_evidence_is_rejected(tmp_path: Path) -> None:
    from tests_v2.test_fast_generation import _context

    from papercraft.application.projects import ProjectService
    from papercraft.application.stages import ProductionStageFactory
    from papercraft.config import AppSettings
    from papercraft.infrastructure.gemini import FakeGeminiGateway

    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Citation lineage"))
    source = Source(
        project_id=workspace.project.id,
        role=SourceRole.REFERENCE,
        original_name="source.html",
        stored_path="",
        sha256="a" * 64,
        size_bytes=0,
    )
    workspace.repository.save_source(source)
    entry = BibliographyEntry(title="Unlinked source", source_id=source.id)
    claim = Claim(project_id=workspace.project.id, text="A supported claim", status=ClaimStatus.SUPPORTED)
    evidence = Evidence(
        claim_id=claim.id,
        source_id=source.id,
        locator=Locator(source_id=source.id),
        verified=True,
        supports=True,
    )
    claim.evidence_ids = [evidence.id]
    workspace.repository.save_bibliography_entry(workspace.project.id, entry)
    workspace.repository.save_claim(claim)
    workspace.repository.save_evidence(workspace.project.id, evidence)
    workspace.repository.save_manuscript(
        Manuscript(
            project_id=workspace.project.id,
            title="Citation lineage",
            blocks=[
                ParagraphBlock(
                    text="A supported claim.",
                    metadata={
                        "claim_ids": [claim.id],
                        "bibliography_entry_ids": [entry.id],
                    },
                )
            ],
        )
    )
    with pytest.raises(StageExecutionError, match="no verified claim/evidence lineage"):
        ProductionStageFactory(FakeGeminiGateway()).citation_audit(_context(workspace, settings))
