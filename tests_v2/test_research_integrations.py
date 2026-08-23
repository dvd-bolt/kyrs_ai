from __future__ import annotations

import hashlib
import json
from pathlib import Path

from papercraft.infrastructure.research import (
    CrossrefClient,
    DOIResolver,
    OfficialSourcePolicy,
    OpenAlexClient,
    SourceSnapshotStore,
    URLVerificationResult,
)


class _JSONVerifier:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def verify(self, url: str) -> URLVerificationResult:
        self.urls.append(url)
        body = json.dumps(self.payload).encode()
        return URLVerificationResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            content_length=len(body),
            content_sha256=hashlib.sha256(body).hexdigest(),
            verified=True,
            body=body,
        )


def test_source_snapshot_is_byte_exact_and_located(tmp_path: Path) -> None:
    body = b"<html><head><meta name='author' content='Ada'></head><body>Verified evidence.</body></html>"
    verification = URLVerificationResult(
        requested_url="https://example.org/source",
        final_url="https://example.org/source",
        status_code=200,
        content_type="text/html",
        content_length=len(body),
        content_sha256=hashlib.sha256(body).hexdigest(),
        verified=True,
        body=body,
    )
    capture = SourceSnapshotStore(tmp_path / "snapshots").capture(
        project_id="project-1",
        source_id="source-1",
        canonical_url="https://example.org/source",
        verification=verification,
    )
    stored = Path(capture.snapshot.stored_path)
    assert stored.read_bytes() == body
    assert capture.snapshot.locator.snapshot_id == capture.snapshot.id
    assert capture.snapshot.locator.source_id == "source-1"
    assert capture.snapshot.authors == ["Ada"]
    assert "Verified evidence." in capture.extracted_text


def test_crossref_and_openalex_records_are_normalized() -> None:
    crossref_verifier = _JSONVerifier(
        {
            "message": {
                "items": [
                    {
                        "DOI": "10.1000/TEST",
                        "title": ["A verified paper"],
                        "author": [{"given": "Ada", "family": "Lovelace"}],
                        "issued": {"date-parts": [[2026]]},
                        "URL": "https://publisher.example/paper",
                        "publisher": "Example Press",
                    }
                ]
            }
        }
    )
    crossref = CrossrefClient(crossref_verifier).search("verified paper", rows=1)
    assert crossref[0].doi == "10.1000/test"
    assert crossref[0].authors == ("Ada Lovelace",)

    openalex_verifier = _JSONVerifier(
        {
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "display_name": "Open work",
                    "publication_year": 2025,
                    "doi": "https://doi.org/10.1000/TEST",
                    "authorships": [{"author": {"display_name": "Grace Hopper"}}],
                    "primary_location": {
                        "landing_page_url": "https://publisher.example/open",
                        "source": {"display_name": "Journal"},
                    },
                    "abstract_inverted_index": {"Reliable": [0], "evidence": [1]},
                }
            ]
        }
    )
    openalex = OpenAlexClient(openalex_verifier).search("open work", per_page=1)
    assert openalex[0].doi == "10.1000/test"
    assert openalex[0].abstract == "Reliable evidence"


def test_doi_resolver_and_official_policy() -> None:
    verifier = _JSONVerifier({"ok": True})
    result = DOIResolver(verifier).resolve("https://doi.org/10.1000/TEST")
    assert result.verified
    assert verifier.urls == ["https://doi.org/10.1000/test"]
    policy = OfficialSourcePolicy()
    assert policy.is_official("https://ai.google.dev/gemini-api/docs/models")
    assert policy.is_official("https://example.gov/statistics")
    assert not policy.is_official("https://example.com/blog")
