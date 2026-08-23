"""Finalize Word fields and produce PDF through installed desktop software."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
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
                fields_updated = self._convert_with_libreoffice(
                    docx,
                    pdf if require_pdf else None,
                )
                return FinalizationResult(
                    docx,
                    _pdf_result(pdf) if require_pdf else None,
                    "libreoffice",
                    fields_updated,
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
        if find_spec("win32com") is None or find_spec("pythoncom") is None:
            return False
        application = None
        pythoncom = None
        try:
            pythoncom = import_module("pythoncom")
            win32com_client = import_module("win32com.client")
            pythoncom.CoInitialize()
            application = win32com_client.DispatchEx("Word.Application")
            application.Visible = False
            application.DisplayAlerts = 0
            return True
        except Exception:
            return False
        finally:
            if application is not None:
                with suppress(Exception):
                    application.Quit()
            if pythoncom is not None:
                with suppress(Exception):
                    pythoncom.CoUninitialize()

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

    def _convert_with_libreoffice(self, docx: Path, pdf: Path | None) -> bool:
        executable = self._find_libreoffice()
        if executable is None:
            raise FinalizationUnavailableError("LibreOffice executable was not found")
        if pdf is None:
            raise FinalizationUnavailableError(
                "LibreOffice is used only for PDF conversion; no PDF was requested"
            )
        pdf.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="papercraft-lo-",
            ignore_cleanup_errors=True,
        ) as temporary_dir:
            temporary = Path(temporary_dir)
            profile = temporary / "profile"
            profile.mkdir()
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            generated = temporary / f"{docx.stem}.pdf"
            python = executable.parent / ("python.exe" if os.name == "nt" else "python")
            helper = Path(__file__).with_name("libreoffice_update.py")
            if python.is_file() and helper.is_file():
                self._export_with_uno(
                    executable,
                    python,
                    helper,
                    profile,
                    docx,
                    generated,
                    creationflags,
                )
                fields_updated = True
            else:
                fields_updated = False
                self._convert_without_field_update(
                    executable,
                    profile,
                    temporary,
                    docx,
                    creationflags,
                )
            if not generated.is_file():
                raise FinalizationError("LibreOffice did not produce a PDF")
            temporary_pdf = pdf.with_name(f".{pdf.name}.tmp")
            shutil.copyfile(generated, temporary_pdf)
            os.replace(temporary_pdf, pdf)
        _pdf_result(pdf)
        return fields_updated

    def _convert_without_field_update(
        self,
        executable: Path,
        profile: Path,
        temporary: Path,
        docx: Path,
        creationflags: int,
    ) -> None:
        try:
            completed = subprocess.run(
                [
                    str(executable),
                    "--headless",
                    "--nologo",
                    "--nolockcheck",
                    "--nodefault",
                    f"-env:UserInstallation={profile.as_uri()}",
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
        except subprocess.TimeoutExpired as exc:
            raise FinalizationError(
                f"LibreOffice timed out after {self.timeout_seconds:g} seconds"
            ) from exc
        generated = temporary / f"{docx.stem}.pdf"
        if completed.returncode != 0 or not generated.is_file():
            detail = (completed.stderr or completed.stdout or "unknown error").strip()[-800:]
            raise FinalizationError(detail)

    def _export_with_uno(
        self,
        executable: Path,
        python: Path,
        helper: Path,
        profile: Path,
        docx: Path,
        generated: Path,
        creationflags: int,
    ) -> None:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = int(reservation.getsockname()[1])
        listener = subprocess.Popen(
            [
                str(executable),
                "--headless",
                "--nologo",
                "--norestore",
                "--nodefault",
                "--nofirststartwizard",
                f"-env:UserInstallation={profile.as_uri()}",
                f"--accept=socket,host=127.0.0.1,port={port};urp;StarOffice.ServiceManager",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        try:
            completed = subprocess.run(
                [
                    str(python),
                    str(helper),
                    "--port",
                    str(port),
                    "--docx",
                    str(docx),
                    "--pdf",
                    str(generated),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                creationflags=creationflags,
                cwd=python.parent,
                env={**os.environ, "PYTHONPATH": str(python.parent)},
            )
            if completed.returncode != 0 or not generated.is_file():
                detail = (completed.stderr or completed.stdout or "UNO export failed").strip()[-800:]
                raise FinalizationError(detail)
        except subprocess.TimeoutExpired as exc:
            raise FinalizationError(
                f"LibreOffice UNO export timed out after {self.timeout_seconds:g} seconds"
            ) from exc
        finally:
            with suppress(OSError):
                listener.terminate()
            try:
                listener.wait(timeout=10)
            except subprocess.TimeoutExpired:
                with suppress(OSError):
                    listener.kill()
                with suppress(OSError, subprocess.TimeoutExpired):
                    listener.wait(timeout=10)
            # LibreOffice may release its extension registry handles a moment
            # after the UNO desktop reports termination on Windows.
            time.sleep(1.0)

    def _find_libreoffice(self) -> Path | None:
        if self.libreoffice_path and self.libreoffice_path.is_file():
            if os.name == "nt" and self.libreoffice_path.suffix.casefold() == ".exe":
                console = self.libreoffice_path.with_suffix(".com")
                if console.is_file():
                    return console
            return self.libreoffice_path
        for name in (("soffice.com", "soffice", "libreoffice") if os.name == "nt" else ("soffice", "libreoffice")):
            found = shutil.which(name)
            if found:
                return Path(found).resolve()
        if os.name == "nt":
            candidates = [
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
                / "LibreOffice"
                / "program"
                / "soffice.com",
                Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
                / "LibreOffice"
                / "program"
                / "soffice.com",
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
