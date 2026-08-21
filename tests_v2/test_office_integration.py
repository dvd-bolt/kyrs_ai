from __future__ import annotations

import os
from pathlib import Path

import pytest

from papercraft.domain import HeadingBlock, Manuscript, ParagraphBlock
from papercraft.infrastructure.render import DocumentFinalizer, DocxRenderer, RenderConfig

pytestmark = pytest.mark.office


@pytest.mark.skipif(
    os.getenv("PAPERCRAFT_RUN_OFFICE_TESTS") != "1",
    reason="set PAPERCRAFT_RUN_OFFICE_TESTS=1 to exercise installed Word/LibreOffice",
)
def test_installed_office_updates_docx_and_exports_valid_pdf(tmp_path: Path) -> None:
    manuscript = Manuscript(
        project_id="office-smoke",
        title="PaperCraft Office smoke test",
        blocks=[
            HeadingBlock(text="INTRODUCTION", level=1),
            ParagraphBlock(
                text=(
                    "This document is generated locally to verify Word fields, "
                    "DOCX persistence and PDF export."
                )
            ),
        ],
    )
    docx = tmp_path / "office-smoke.docx"
    pdf = tmp_path / "office-smoke.pdf"
    DocxRenderer(RenderConfig()).render(manuscript, docx)

    finalizer = DocumentFinalizer(timeout_seconds=120)
    if not finalizer.word_available() and not finalizer.libreoffice_available():
        pytest.skip("neither Word automation nor LibreOffice is available")
    result = finalizer.finalize(docx, pdf_path=pdf, preferred="auto", require_pdf=True)

    assert result.engine in {"word", "libreoffice"}
    assert result.pdf is not None
    assert result.pdf.valid_header
    assert result.pdf.size_bytes > 1_000
    assert docx.is_file() and docx.stat().st_size > 1_000
