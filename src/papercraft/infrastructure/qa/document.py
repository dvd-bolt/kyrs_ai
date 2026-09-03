"""Structural DOCX and rendered-page checks for the release gate."""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

from papercraft.domain import QASeverity

_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_FORBIDDEN_PART_MARKERS = (
    "vbaproject.bin",
    "word/activex/",
    "word/embeddings/",
    "word/altchunks/",
)
_ACTIVE_FIELD = re.compile(
    r"\b(?:DDEAUTO|DDE|INCLUDETEXT|INCLUDEPICTURE|HYPERLINK)\b",
    flags=re.IGNORECASE,
)


class DocumentInspectionError(RuntimeError):
    """Raised when a release candidate is not a readable Office artifact."""


@dataclass(frozen=True, slots=True)
class DocxPackageInspection:
    path: Path
    sha256: str
    part_names: frozenset[str]
    styles: frozenset[str]
    field_codes: tuple[str, ...]
    update_fields_on_open: bool
    table_count: int
    image_count: int
    invalid_images: tuple[str, ...]
    malformed_tables: int
    forbidden_parts: tuple[str, ...]
    external_relationships: tuple[str, ...]
    active_fields: tuple[str, ...]
    visible_text: str


@dataclass(frozen=True, slots=True)
class PageLayoutFinding:
    severity: QASeverity
    category: str
    page: int
    message: str
    auto_fixable: bool = False


def inspect_docx_package(path: str | Path) -> DocxPackageInspection:
    """Read a DOCX defensively and return release-relevant OpenXML facts."""

    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file() or candidate.suffix.casefold() != ".docx":
        raise DocumentInspectionError("DOCX release candidate does not exist")
    try:
        with zipfile.ZipFile(candidate) as archive:
            corrupt = archive.testzip()
            if corrupt:
                raise DocumentInspectionError("DOCX package contains a corrupt member")
            infos = archive.infolist()
            names = frozenset(info.filename.replace("\\", "/") for info in infos)
            if len(infos) > 5_000:
                raise DocumentInspectionError("DOCX package contains too many parts")
            for info in infos:
                normalized = info.filename.replace("\\", "/")
                if normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
                    raise DocumentInspectionError("DOCX package contains an unsafe part path")

            required = {"[Content_Types].xml", "word/document.xml", "word/styles.xml"}
            if missing := required - names:
                raise DocumentInspectionError(
                    "DOCX package lacks required members: " + ", ".join(sorted(missing))
                )

            xml_roots: dict[str, ElementTree.Element] = {}
            for name in sorted(names):
                if not (name.endswith(".xml") or name.endswith(".rels")):
                    continue
                try:
                    xml_roots[name] = ElementTree.fromstring(archive.read(name))
                except (KeyError, ElementTree.ParseError) as exc:
                    raise DocumentInspectionError("DOCX package contains invalid XML") from exc

            forbidden = tuple(
                sorted(
                    name
                    for name in names
                    if any(marker in name.casefold() for marker in _FORBIDDEN_PART_MARKERS)
                )
            )
            external: list[str] = []
            for name, root in xml_roots.items():
                if not name.endswith(".rels"):
                    continue
                for relationship in root.findall(f"{{{_REL_NS}}}Relationship"):
                    if relationship.attrib.get("TargetMode", "").casefold() == "external":
                        external.append(relationship.attrib.get("Target", "external target"))

            word_roots = [
                root for name, root in xml_roots.items() if name.startswith("word/")
            ]
            field_codes = tuple(
                " ".join((node.text or "").split())
                for root in word_roots
                for node in root.iter(f"{{{_WORD_NS}}}instrText")
                if (node.text or "").strip()
            )
            active_fields = tuple(code for code in field_codes if _ACTIVE_FIELD.search(code))
            settings_root = xml_roots.get("word/settings.xml")
            update_fields_on_open = bool(
                settings_root is not None
                and settings_root.find(f"{{{_WORD_NS}}}updateFields") is not None
            )
            visible_text = " ".join(
                (node.text or "").strip()
                for root in word_roots
                for node in root.iter(f"{{{_WORD_NS}}}t")
                if (node.text or "").strip()
            )

            styles_root = xml_roots["word/styles.xml"]
            styles: set[str] = set()
            for style in styles_root.iter(f"{{{_WORD_NS}}}style"):
                style_id = style.attrib.get(f"{{{_WORD_NS}}}styleId")
                if style_id:
                    styles.add(style_id)
                style_name = style.find(f"{{{_WORD_NS}}}name")
                if style_name is not None:
                    value = style_name.attrib.get(f"{{{_WORD_NS}}}val")
                    if value:
                        styles.add(value)

            document_root = xml_roots["word/document.xml"]
            tables = list(document_root.iter(f"{{{_WORD_NS}}}tbl"))
            malformed_tables = sum(
                1
                for table in tables
                if not list(table.iter(f"{{{_WORD_NS}}}tr"))
                or any(
                    not list(row.iter(f"{{{_WORD_NS}}}tc"))
                    for row in table.iter(f"{{{_WORD_NS}}}tr")
                )
            )

            media_names = sorted(name for name in names if name.startswith("word/media/"))
            invalid_images: list[str] = []
            if media_names:
                try:
                    from PIL import Image
                except ImportError as exc:  # pragma: no cover - mandatory dependency
                    raise DocumentInspectionError("Pillow is required for DOCX image checks") from exc
                for name in media_names:
                    try:
                        with Image.open(BytesIO(archive.read(name))) as image:
                            image.verify()
                    except Exception:
                        invalid_images.append(name)
    except DocumentInspectionError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocumentInspectionError("DOCX release candidate is not a valid ZIP package") from exc

    return DocxPackageInspection(
        path=candidate,
        sha256=_sha256(candidate),
        part_names=names,
        styles=frozenset(styles),
        field_codes=field_codes,
        update_fields_on_open=update_fields_on_open,
        table_count=len(tables),
        image_count=len(media_names),
        invalid_images=tuple(invalid_images),
        malformed_tables=malformed_tables,
        forbidden_parts=forbidden,
        external_relationships=tuple(sorted(external)),
        active_fields=active_fields,
        visible_text=visible_text,
    )


def inspect_pdf_layout(path: str | Path) -> list[PageLayoutFinding]:
    """Detect deterministic page defects in LibreOffice's internal PDF."""

    candidate = Path(path).expanduser().resolve()
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - mandatory visuals dependency
        raise DocumentInspectionError("PyMuPDF is required for page layout QA") from exc

    try:
        document: Any = pymupdf.open(candidate)  # type: ignore[no-untyped-call]
    except Exception as exc:
        raise DocumentInspectionError("Internal PDF could not be opened") from exc
    findings: list[PageLayoutFinding] = []
    try:
        for page_index, page in enumerate(document, start=1):
            width = float(page.rect.width)
            height = float(page.rect.height)
            dictionary = page.get_text("dict")
            text_lines: list[tuple[tuple[float, float, float, float], str, float]] = []
            has_visible_graphics = bool(page.get_images(full=True) or page.get_drawings())
            for block in dictionary.get("blocks", []):
                if not isinstance(block, dict):
                    continue
                for line in block.get("lines", []):
                    spans = [span for span in line.get("spans", []) if str(span.get("text", "")).strip()]
                    if not spans:
                        continue
                    text = "".join(str(span.get("text", "")) for span in spans).strip()
                    bounds = tuple(float(value) for value in line.get("bbox", (0, 0, 0, 0)))
                    size = max(float(span.get("size", 0) or 0) for span in spans)
                    if len(bounds) != 4:
                        continue
                    text_lines.append((bounds, text, size))
                    x0, y0, x1, y1 = bounds
                    if x0 < -1 or y0 < -1 or x1 > width + 1 or y1 > height + 1:
                        findings.append(
                            PageLayoutFinding(
                                QASeverity.CRITICAL,
                                "overflow",
                                page_index,
                                f"Page {page_index} contains content outside the page bounds",
                                True,
                            )
                        )
                    if _is_caption(text) and y1 > height - 8:
                        findings.append(
                            PageLayoutFinding(
                                QASeverity.CRITICAL,
                                "cropped_caption",
                                page_index,
                                f"Page {page_index} has a caption clipped by the bottom edge",
                                True,
                            )
                        )

            if not text_lines and not has_visible_graphics:
                findings.append(
                    PageLayoutFinding(
                        QASeverity.BLOCKER,
                        "blank_page",
                        page_index,
                        f"Page {page_index} is blank",
                    )
                )
                continue
            if text_lines:
                bounds, text, size = max(text_lines, key=lambda item: item[0][3])
                if _looks_like_heading(text, size) and bounds[3] > height * 0.72:
                    findings.append(
                        PageLayoutFinding(
                            QASeverity.CRITICAL,
                            "orphan_heading",
                            page_index,
                            f"Page {page_index} ends with an orphan heading",
                            True,
                        )
                    )
    finally:
        document.close()
    return _deduplicate(findings)


def _looks_like_heading(text: str, size: float) -> bool:
    normalized = " ".join(text.split())
    if not normalized or len(normalized) > 140 or _is_caption(normalized):
        return False
    letters = [character for character in normalized if character.isalpha()]
    uppercase = bool(letters) and all(character.isupper() for character in letters)
    return size >= 13 or uppercase


def _is_caption(text: str) -> bool:
    return bool(re.match(r"^(?:Таблица|Рисунок|Table|Figure)\s+\d+\b", text, re.IGNORECASE))


def _deduplicate(findings: list[PageLayoutFinding]) -> list[PageLayoutFinding]:
    unique: dict[tuple[str, int, str], PageLayoutFinding] = {}
    for finding in findings:
        unique[(finding.category, finding.page, finding.message)] = finding
    return list(unique.values())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "DocumentInspectionError",
    "DocxPackageInspection",
    "PageLayoutFinding",
    "inspect_docx_package",
    "inspect_pdf_layout",
]
