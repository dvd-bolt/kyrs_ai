from __future__ import annotations

import json
import os
import zipfile
from datetime import date
from pathlib import Path

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image, ImageDraw, ImageFont

from papercraft.domain import (
    AppendixBlock,
    BibliographyEntry,
    FigureBlock,
    FormulaBlock,
    FormulaSpec,
    HeadingBlock,
    Manuscript,
    ParagraphBlock,
    TableBlock,
    TableSpec,
)
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


def _append_ref_field(path: Path, bookmark: str) -> None:
    document = Document(path)
    paragraph = document.add_paragraph("Контрольная перекрёстная ссылка: ")
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    code = OxmlElement("w:instrText")
    code.set(qn("xml:space"), "preserve")
    code.text = f" REF {bookmark} \\h "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    fallback = OxmlElement("w:t")
    fallback.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, code, separate, fallback, end):
        run._r.append(element)
    document.save(path)


@pytest.mark.skipif(
    os.getenv("PAPERCRAFT_RUN_OFFICE_TESTS") != "1",
    reason="set PAPERCRAFT_RUN_OFFICE_TESTS=1 to exercise installed Word/LibreOffice",
)
def test_real_office_feature_matrix(tmp_path: Path) -> None:
    output_root = Path(os.getenv("PAPERCRAFT_OFFICE_OUTPUT_DIR", str(tmp_path))).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    image = output_root / "office-matrix-figure.png"
    canvas = Image.new("RGB", (1200, 650), "white")
    drawing = ImageDraw.Draw(canvas)
    drawing.rectangle((80, 80, 1120, 570), outline="black", width=8)
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = ImageFont.truetype(str(font_path), 62) if font_path.is_file() else None
    drawing.text((230, 280), "PaperCraft Office Matrix", fill="black", font=font)
    canvas.save(image)
    manuscript = Manuscript(
        project_id="office-feature-matrix",
        title="Проверка Office matrix",
        metadata={
            "title_page": {
                "university": "Тестовый университет",
                "department": "Кафедра информационных систем",
                "work_type": "ОТЧЁТ",
                "student": "Студент А. А.",
                "supervisor": "Руководитель Б. Б.",
                "city": "Москва",
                "year": 2026,
            }
        },
        blocks=[
            HeadingBlock(text="ВВЕДЕНИЕ", level=1),
            ParagraphBlock(
                text="Документ проверяет поля, секции, изображения, формулы и приложения. " * 20
            ),
            TableBlock(
                spec=TableSpec(
                    caption="Широкая таблица Office matrix",
                    headers=[f"Колонка {index}" for index in range(1, 9)],
                    rows=[[f"R{row}C{column}" for column in range(1, 9)] for row in range(1, 18)],
                )
            ),
            FormulaBlock(spec=FormulaSpec(expression="x = (-b ± √(b²-4ac))/(2a)", label="1")),
            FigureBlock(caption="Контрольное изображение", artifact_id="matrix-figure"),
            AppendixBlock(
                title="ПРИЛОЖЕНИЕ А",
                blocks=[ParagraphBlock(text="Материалы приложения и контроль нумерации страниц.")],
            ),
        ],
        bibliography=[
            BibliographyEntry(
                title="Официальная документация",
                authors=["A. Author"],
                year=2026,
                publisher="Publisher",
                url="https://example.org/official",
                accessed_on=date(2026, 8, 21),
            )
        ],
    )
    docx = output_root / "office-feature-matrix.docx"
    DocxRenderer(RenderConfig()).render(
        manuscript,
        docx,
        artifact_paths={"matrix-figure": image},
    )
    _append_ref_field(docx, "pc_Table_1")

    with zipfile.ZipFile(docx) as archive:
        document_xml = archive.read("word/document.xml")
        field_parts = [
            archive.read(name)
            for name in archive.namelist()
            if name.startswith(("word/header", "word/footer")) and name.endswith(".xml")
        ]
    assert b"TOC " in document_xml
    assert b"SEQ Table" in document_xml and b"SEQ Figure" in document_xml
    assert b"REF pc_Table_1" in document_xml
    assert b"bookmarkStart" in document_xml
    assert b"oMath" in document_xml
    assert any(b"PAGE" in item for item in field_parts)
    loaded = Document(docx)
    assert len(loaded.sections) >= 3
    landscape_sections = [section for section in loaded.sections if section.orientation == 1]
    assert landscape_sections
    assert all(section.page_width > section.page_height for section in landscape_sections)
    assert loaded.sections[-1].page_height > loaded.sections[-1].page_width
    assert loaded.inline_shapes
    assert "ПРИЛОЖЕНИЕ А" in "\n".join(item.text for item in loaded.paragraphs)

    finalizer = DocumentFinalizer(timeout_seconds=120)
    matrix: dict[str, object] = {
        "docx": str(docx),
        "features": {
            "toc": True,
            "page": True,
            "seq": True,
            "ref": True,
            "bookmarks": True,
            "headers_footers": True,
            "title_page": True,
            "landscape_table": True,
            "formula": True,
            "image": True,
            "bibliography": True,
            "appendix": True,
        },
    }
    if finalizer.word_available():
        word_pdf = output_root / "office-feature-matrix-word.pdf"
        word_result = finalizer.finalize(docx, pdf_path=word_pdf, preferred="word")
        assert word_result.engine == "word"
        matrix["microsoft_word"] = {"status": "passed", "pdf": str(word_pdf)}
    else:
        matrix["microsoft_word"] = {"status": "unavailable", "reason": "Word COM launch failed"}
    assert finalizer.libreoffice_available()
    libreoffice_pdf = output_root / "office-feature-matrix-libreoffice.pdf"
    libreoffice_result = finalizer.finalize(
        docx,
        pdf_path=libreoffice_pdf,
        preferred="libreoffice",
    )
    assert libreoffice_result.engine == "libreoffice"
    assert libreoffice_result.pdf is not None and libreoffice_result.pdf.valid_header
    import pymupdf

    rendered_pdf = pymupdf.open(libreoffice_pdf)
    try:
        for page_number in (4, 5):
            page = rendered_pdf[page_number - 1]
            footer_text = " ".join(
                block[4]
                for block in page.get_text("blocks")
                if block[1] > page.rect.height * 0.85
            )
            assert str(page_number) in footer_text
    finally:
        rendered_pdf.close()
    matrix["libreoffice"] = {
        "status": "passed",
        "pdf": str(libreoffice_pdf),
        "size_bytes": libreoffice_result.pdf.size_bytes,
    }
    (output_root / "office-matrix.json").write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
