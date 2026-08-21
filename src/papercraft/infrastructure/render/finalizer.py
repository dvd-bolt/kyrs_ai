"""Finalize Word fields and produce PDF through installed desktop software."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import Literal


class FinalizationError(RuntimeError):
    pass


class FinalizationUnavailableError(FinalizationError):
    pass


@dataclass(frozen=True, slots=True)
class PDFResult:
    path: Path
    size_bytes: int
    valid_header: bool


@dataclass(frozen=True, slots=True)
class FinalizationResult:
    docx_path: Path
    pdf: PDFResult | None
    engine: Literal["word", "libreoffice", "none"]
    fields_updated: bool
    warnings: tuple[str, ...] = ()


class DocumentFinalizer:
    """Use Word COM when possible and LibreOffice as a headless fallback."""

    def __init__(
        self,
        *,
        libreoffice_path: str | os.PathLike[str] | None = None,
        timeout_seconds: float = 180,
    ) -> None:
        self.libreoffice_path = Path(libreoffice_path).resolve() if libreoffice_path else None
        self.timeout_seconds = timeout_seconds

    def finalize(
        self,
        docx_path: str | os.PathLike[str],
        *,
        pdf_path: str | os.PathLike[str] | None = None,
        preferred: Literal["auto", "word", "libreoffice"] = "auto",
        require_pdf: bool = True,
        allow_unfinalized: bool = False,
    ) -> FinalizationResult:
        docx = Path(docx_path).expanduser().resolve()
        if not docx.is_file() or docx.suffix.lower() != ".docx":
            raise FinalizationError(f"DOCX does not exist: {docx}")
        pdf = (
            Path(pdf_path).expanduser().resolve()
            if pdf_path
            else docx.with_suffix(".pdf")
        )
        warnings: list[str] = []

        if preferred in {"auto", "word"}:
            try:
                self._finalize_with_word(docx, pdf if require_pdf else None)
                return FinalizationResult(
                    docx,
                    _pdf_result(pdf) if require_pdf else None,
                    "word",
                    True,
                    tuple(warnings),
                )
            except FinalizationError as exc:
                warnings.append(f"Microsoft Word finalization failed: {exc}")

        if preferred in {"auto", "word", "libreoffice"}:
            try:
                self._convert_with_libreoffice(docx, pdf if require_pdf else None)
                return FinalizationResult(
                    docx,
                    _pdf_result(pdf) if require_pdf else None,
                    "libreoffice",
                    False,
                    tuple(warnings),
                )
            except FinalizationError as exc:
                warnings.append(f"LibreOffice finalization failed: {exc}")

        if allow_unfinalized and not require_pdf:
            return FinalizationResult(docx, None, "none", False, tuple(warnings))
        raise FinalizationUnavailableError("; ".join(warnings) or "No Office finalizer is available")

    @staticmethod
    def word_available() -> bool:
        if os.name != "nt":
            return False
        return find_spec("win32com") is not None and find_spec("pythoncom") is not None

    def libreoffice_available(self) -> bool:
        return self._find_libreoffice() is not None

    def _finalize_with_word(self, docx: Path, pdf: Path | None) -> None:
        if os.name != "nt":
            raise FinalizationUnavailableError("Word COM is available only on Windows")
        try:
            pythoncom = import_module("pythoncom")
            win32com_client = import_module("win32com.client")
        except ImportError as exc:
            raise FinalizationUnavailableError("pywin32 is not installed") from exc

        application = None
        document = None
        pythoncom.CoInitialize()
        try:
            application = win32com_client.DispatchEx("Word.Application")
            application.Visible = False
            application.DisplayAlerts = 0
            document = application.Documents.Open(str(docx), ReadOnly=False, AddToRecentFiles=False)
            document.Fields.Update()
            for index in range(1, document.TablesOfContents.Count + 1):
                document.TablesOfContents(index).Update()
            for index in range(1, document.TablesOfFigures.Count + 1):
                document.TablesOfFigures(index).Update()
            for story_range in document.StoryRanges:
                current = story_range
                while current is not None:
                    current.Fields.Update()
                    current = current.NextStoryRange
            document.Save()
            if pdf is not None:
                pdf.parent.mkdir(parents=True, exist_ok=True)
                # wdExportFormatPDF = 17
                document.ExportAsFixedFormat(str(pdf), 17, OpenAfterExport=False)
        except Exception as exc:
            raise FinalizationError(str(exc)) from exc
        finally:
            if document is not None:
                with suppress(Exception):
                    document.Close(SaveChanges=True)
            if application is not None:
                with suppress(Exception):
                    application.Quit()
            pythoncom.CoUninitialize()
        if pdf is not None:
            _pdf_result(pdf)

    def _convert_with_libreoffice(self, docx: Path, pdf: Path | None) -> None:
        executable = self._find_libreoffice()
        if executable is None:
            raise FinalizationUnavailableError("LibreOffice executable was not found")
        if pdf is None:
            raise FinalizationUnavailableError(
                "LibreOffice is used only for PDF conversion; no PDF was requested"
            )
        pdf.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="papercraft-lo-") as temporary_dir:
            temporary = Path(temporary_dir)
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            completed = subprocess.run(
                [
                    str(executable),
                    "--headless",
                    "--nologo",
                    "--nolockcheck",
                    "--nodefault",
                    "--convert-to",
                    "pdf:writer_pdf_Export",
                    "--outdir",
                    str(temporary),
                    str(docx),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                creationflags=creationflags,
            )
            generated = temporary / f"{docx.stem}.pdf"
            if completed.returncode != 0 or not generated.is_file():
                detail = (completed.stderr or completed.stdout or "unknown error").strip()[-800:]
                raise FinalizationError(detail)
            temporary_pdf = pdf.with_name(f".{pdf.name}.tmp")
            shutil.copyfile(generated, temporary_pdf)
            os.replace(temporary_pdf, pdf)
        _pdf_result(pdf)

    def _find_libreoffice(self) -> Path | None:
        if self.libreoffice_path and self.libreoffice_path.is_file():
            return self.libreoffice_path
        for name in ("soffice", "libreoffice"):
            found = shutil.which(name)
            if found:
                return Path(found).resolve()
        if os.name == "nt":
            candidates = [
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
                / "LibreOffice"
                / "program"
                / "soffice.exe",
                Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
                / "LibreOffice"
                / "program"
                / "soffice.exe",
            ]
            return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
        return None


def _pdf_result(path: Path) -> PDFResult:
    if not path.is_file() or path.stat().st_size < 8:
        raise FinalizationError(f"PDF was not produced: {path}")
    with path.open("rb") as stream:
        header = stream.read(5)
    if header != b"%PDF-":
        raise FinalizationError(f"output does not have a valid PDF header: {path}")
    return PDFResult(path=path, size_bytes=path.stat().st_size, valid_header=True)
