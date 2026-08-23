from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from papercraft.config import AppSettings
from papercraft.domain import Source, SourceRole
from papercraft.infrastructure.gemini import GeminiGateway
from papercraft.infrastructure.ingest import GeminiVisionOCR, PdfParser

pytestmark = [pytest.mark.live]


def _enabled() -> None:
    if os.getenv("PAPERCRAFT_RUN_VISION_TESTS") != "1":
        pytest.skip("set PAPERCRAFT_RUN_VISION_TESTS=1 for live OCR/Vision fixtures")


def _font(size: int, *, handwritten: bool = False) -> ImageFont.FreeTypeFont:
    names = ["segoepr.ttf", "segoesc.ttf"] if handwritten else ["arial.ttf"]
    for name in names:
        path = Path("C:/Windows/Fonts") / name
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.truetype(str(Path("C:/Windows/Fonts/arial.ttf")), size)


def _png(
    lines: list[str],
    *,
    handwritten: bool = False,
    table: bool = False,
    caption: bool = False,
    degrade: bool = False,
) -> bytes:
    image = Image.new("RGB", (1500, 1000), "white")
    draw = ImageDraw.Draw(image)
    font = _font(46, handwritten=handwritten)
    for index, line in enumerate(lines):
        draw.text((90, 80 + index * 90), line, fill="black", font=font)
    if table:
        for x in (80, 500, 920, 1380):
            draw.line((x, 350, x, 700), fill="black", width=4)
        for y in (350, 470, 590, 700):
            draw.line((80, y, 1380, y), fill="black", width=4)
        draw.text((110, 380), "Год", fill="black", font=font)
        draw.text((530, 380), "Доход", fill="black", font=font)
        draw.text((110, 500), "2026", fill="black", font=font)
        draw.text((530, 500), "125 400", fill="black", font=font)
    if caption:
        draw.rectangle((480, 360, 1020, 680), outline="black", width=8)
        draw.ellipse((650, 440, 850, 610), outline="black", width=8)
        draw.text((390, 760), "Рисунок 1 — Схема процесса", fill="black", font=font)
    if degrade:
        image = image.resize((600, 400)).filter(ImageFilter.GaussianBlur(1.8)).resize((1500, 1000))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(scope="module")
def live_ocr() -> GeminiVisionOCR:
    _enabled()
    return GeminiVisionOCR(GeminiGateway(AppSettings.from_environment()))


def test_live_russian_scan(live_ocr: GeminiVisionOCR) -> None:
    result = live_ocr.recognize(
        _png(["Проверка русского скана", "Надёжное извлечение текста"]),
        page_number=1,
    )
    assert "проверка русского скана" in result.text.casefold()


def test_live_table_and_numbers(live_ocr: GeminiVisionOCR) -> None:
    result = live_ocr.recognize(_png(["Таблица 1"], table=True), page_number=2)
    assert "2026" in result.text
    assert "125 400" in result.text or "125400" in result.text
    assert result.tables


def test_live_handwritten_fragment_when_supported(live_ocr: GeminiVisionOCR) -> None:
    result = live_ocr.recognize(
        _png(["Рукописная заметка", "Проверить вывод"], handwritten=True),
        page_number=3,
    )
    assert result.text.strip()
    assert 0 <= result.confidence <= 1


def test_live_bad_quality_and_uncertain_number(live_ocr: GeminiVisionOCR) -> None:
    result = live_ocr.recognize(
        _png(["Плохое качество", "Итого: 12?4 рублей"], degrade=True),
        page_number=4,
    )
    assert result.text.strip()
    assert result.low_confidence_numbers or result.confidence < 0.9


def test_live_figure_caption(live_ocr: GeminiVisionOCR) -> None:
    result = live_ocr.recognize(_png(["Схема"], caption=True), page_number=5)
    assert "рисунок 1" in result.text.casefold()
    assert result.captions


def test_live_mixed_pdf_has_page_level_provenance(
    live_ocr: GeminiVisionOCR,
    tmp_path: Path,
) -> None:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    text_page = document.new_page(width=595, height=842)
    text_page.insert_text((72, 100), "Mixed PDF selectable text", fontsize=16)
    scan_page = document.new_page(width=595, height=842)
    scan_page.insert_image(scan_page.rect, stream=_png(["Сканированная страница PDF"]))
    path = tmp_path / "mixed.pdf"
    document.save(path)
    document.close()
    source = Source(
        project_id="vision-live",
        role=SourceRole.METHODOLOGY,
        original_name=path.name,
        stored_path=str(path),
        sha256="0" * 64,
        mime_type="application/pdf",
        size_bytes=path.stat().st_size,
    )

    result = PdfParser(vision=live_ocr).parse(source)

    by_page = {fragment.locator.page: fragment for fragment in result.fragments}
    assert "Mixed PDF selectable text" in by_page[1].content
    assert "сканированная страница" in by_page[2].content.casefold()
    assert by_page[2].metadata["kind"] == "ocr-page"
