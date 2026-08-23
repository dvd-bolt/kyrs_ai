from __future__ import annotations

import os

import pytest

from papercraft.infrastructure.research import CrossrefClient, DOIResolver, OpenAlexClient

pytestmark = [pytest.mark.live]


def _enabled() -> None:
    if os.getenv("PAPERCRAFT_RUN_RESEARCH_TESTS") != "1":
        pytest.skip("set PAPERCRAFT_RUN_RESEARCH_TESTS=1 for live research API tests")


def test_live_crossref_and_openalex() -> None:
    _enabled()
    crossref = CrossrefClient().search("software engineering reproducibility", rows=2)
    openalex = OpenAlexClient().search("software engineering reproducibility", per_page=2)
    assert crossref and any(item.title for item in crossref)
    assert openalex and any(item.title for item in openalex)
    assert any(item.doi for item in [*crossref, *openalex])


def test_live_doi_resolution() -> None:
    _enabled()
    result = DOIResolver().resolve("10.1038/nphys1170")
    assert result.verified
    assert result.final_url.startswith("https://")
    assert result.content_sha256 != "0" * 64
