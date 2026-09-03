"""Finalize DOCX fields and export PDFs through local Office software.

LibreOffice is the beta finalizer. Microsoft Word compatibility helpers are
kept only for non-beta migration callers; the application pipeline always
requests LibreOffice explicitly. Process diagnostics deliberately avoid
returning raw LibreOffice output because it can contain local paths and
document data.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
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
    """Use LibreOffice by default; Word paths are explicit compatibility-only APIs."""

    # LibreOffice can retain extension-registry handles briefly after the UNO
    # listener exits on Windows. Keep the retry budget bounded but long enough
    # to remove the per-run profile in ordinary desktop environments.
    _PROFILE_CLEANUP_ATTEMPTS = 12
    _PROFILE_CLEANUP_DELAY_SECONDS = 0.5
    _PROCESS_STOP_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        *,
        libreoffice_path: str | os.PathLike[str] | None = None,
        timeout_seconds: float = 180,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a finite positive value")
        self.libreoffice_path = Path(libreoffice_path).resolve() if libreoffice_path else None
        self.timeout_seconds = timeout_seconds

    def finalize(
        self,
        docx_path: str | os.PathLike[str],
        *,
        pdf_path: str | os.PathLike[str] | None = None,
        preferred: Literal["auto", "word", "libreoffice"] = "libreoffice",
        require_pdf: bool = True,
        allow_unfinalized: bool = False,
    ) -> FinalizationResult:
        docx = Path(docx_path).expanduser().resolve()
        if not docx.is_file() or docx.suffix.lower() != ".docx":
            raise FinalizationError("A readable .docx input is required for Office finalization")
        pdf = (
            Path(pdf_path).expanduser().resolve()
            if pdf_path is not None
            else docx.with_suffix(".pdf")
        )

        if preferred == "libreoffice":
            try:
                return self._finalize_with_libreoffice_result(docx, pdf, require_pdf, ())
            except FinalizationError as error:
                warnings = [self._safe_failure_warning("LibreOffice", error)]
                return self._unfinalized_or_raise(docx, warnings, require_pdf, allow_unfinalized)

        if preferred == "word":
            return self._finalize_word_then_libreoffice(
                docx,
                pdf,
                require_pdf=require_pdf,
                allow_unfinalized=allow_unfinalized,
            )

        return self._finalize_libreoffice_then_word(
            docx,
            pdf,
            require_pdf=require_pdf,
            allow_unfinalized=allow_unfinalized,
        )

    def finalize_copy(
        self,
        draft_docx_path: str | os.PathLike[str],
        final_docx_path: str | os.PathLike[str],
        *,
        pdf_path: str | os.PathLike[str],
    ) -> FinalizationResult:
        """Finalize a staged copy while retaining the immutable draft artifact."""

        draft = Path(draft_docx_path).expanduser().resolve()
        final = Path(final_docx_path).expanduser().resolve()
        pdf = Path(pdf_path).expanduser().resolve()
        if draft == final:
            raise FinalizationError("Draft and final DOCX paths must be different")
        _docx_result(draft)
        draft_digest = _sha256(draft)
        final.parent.mkdir(parents=True, exist_ok=True)
        pdf.parent.mkdir(parents=True, exist_ok=True)
        descriptor, staged_docx_name = tempfile.mkstemp(
            prefix=f".{final.stem}-", suffix=".docx", dir=final.parent
        )
        os.close(descriptor)
        staged_docx = Path(staged_docx_name)
        descriptor, staged_pdf_name = tempfile.mkstemp(
            prefix=f".{pdf.stem}-", suffix=".pdf", dir=pdf.parent
        )
        os.close(descriptor)
        staged_pdf = Path(staged_pdf_name)
        staged_pdf.unlink(missing_ok=True)
        try:
            shutil.copyfile(draft, staged_docx)
            result = self.finalize(
                staged_docx,
                pdf_path=staged_pdf,
                preferred="libreoffice",
                require_pdf=True,
            )
            if result.engine != "libreoffice" or not result.fields_updated:
                raise FinalizationError(
                    "Bundled LibreOffice did not update the DOCX fields"
                )
            _docx_result(staged_docx)
            _pdf_result(staged_pdf)
            if _sha256(draft) != draft_digest:
                raise FinalizationError("Draft DOCX changed during finalization")
            os.replace(staged_docx, final)
            os.replace(staged_pdf, pdf)
            return FinalizationResult(
                docx_path=final,
                pdf=_pdf_result(pdf),
                engine="libreoffice",
                fields_updated=True,
                warnings=result.warnings,
            )
        except FinalizationError:
            raise
        except OSError as exc:
            raise FinalizationError("Could not publish finalized Office artifacts") from exc
        finally:
            staged_docx.unlink(missing_ok=True)
            staged_pdf.unlink(missing_ok=True)

    def _finalize_libreoffice_then_word(
        self,
        docx: Path,
        pdf: Path,
        *,
        require_pdf: bool,
        allow_unfinalized: bool,
    ) -> FinalizationResult:
        try:
            return self._finalize_with_libreoffice_result(docx, pdf, require_pdf, ())
        except FinalizationError as libreoffice_error:
            libreoffice_warning = self._safe_failure_warning("LibreOffice", libreoffice_error)

        try:
            return self._finalize_with_word_result(
                docx,
                pdf,
                require_pdf,
                (
                    libreoffice_warning,
                    "Microsoft Word was used as a compatibility fallback; "
                    "this export is outside the LibreOffice beta gate.",
                ),
            )
        except FinalizationError as word_error:
            # Keep this order stable for existing callers while retaining the fact
            # that LibreOffice was attempted first.
            warnings = [
                self._safe_failure_warning("Microsoft Word", word_error),
                libreoffice_warning,
            ]
            return self._unfinalized_or_raise(docx, warnings, require_pdf, allow_unfinalized)

    def _finalize_word_then_libreoffice(
        self,
        docx: Path,
        pdf: Path,
        *,
        require_pdf: bool,
        allow_unfinalized: bool,
    ) -> FinalizationResult:
        try:
            return self._finalize_with_word_result(docx, pdf, require_pdf, ())
        except FinalizationError as word_error:
            warnings = [self._safe_failure_warning("Microsoft Word", word_error)]

        try:
            return self._finalize_with_libreoffice_result(docx, pdf, require_pdf, tuple(warnings))
        except FinalizationError as libreoffice_error:
            warnings.append(self._safe_failure_warning("LibreOffice", libreoffice_error))
            return self._unfinalized_or_raise(docx, warnings, require_pdf, allow_unfinalized)

    @staticmethod
    def _unfinalized_or_raise(
        docx: Path,
        warnings: list[str],
        require_pdf: bool,
        allow_unfinalized: bool,
    ) -> FinalizationResult:
        if allow_unfinalized and not require_pdf:
            return FinalizationResult(docx, None, "none", False, tuple(warnings))
        raise FinalizationUnavailableError(
            "; ".join(warnings) or "No Office finalizer is available"
        )

    def _finalize_with_libreoffice_result(
        self,
        docx: Path,
        pdf: Path,
        require_pdf: bool,
        warnings: tuple[str, ...],
    ) -> FinalizationResult:
        fields_updated = self._convert_with_libreoffice(docx, pdf if require_pdf else None)
        return FinalizationResult(
            docx,
            _pdf_result(pdf) if require_pdf else None,
            "libreoffice",
            fields_updated,
            warnings,
        )

    def _finalize_with_word_result(
        self,
        docx: Path,
        pdf: Path,
        require_pdf: bool,
        warnings: tuple[str, ...],
    ) -> FinalizationResult:
        self._finalize_with_word(docx, pdf if require_pdf else None)
        return FinalizationResult(
            docx,
            _pdf_result(pdf) if require_pdf else None,
            "word",
            True,
            warnings,
        )

    @staticmethod
    def _safe_failure_warning(engine: str, error: FinalizationError) -> str:
        """Return an actionable category without exposing process output or paths."""

        detail = str(error).strip()
        message = detail.casefold()
        if isinstance(error, FinalizationUnavailableError) or "not found" in message:
            category = "unavailable"
        elif "timed out" in message:
            category = "timed out"
        elif "valid pdf" in message or "pdf was not produced" in message:
            category = "did not produce a valid PDF"
        else:
            category = "failed"
        if category == "failed" and re.fullmatch(r"[A-Za-z0-9 .,:;()_-]{1,160}", detail):
            return f"{engine} finalization failed: {detail}"
        return f"{engine} finalization {category}."

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
                try:
                    pdf.parent.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise FinalizationError("Could not create the PDF output directory") from exc
                # wdExportFormatPDF = 17
                document.ExportAsFixedFormat(str(pdf), 17, OpenAfterExport=False)
        except FinalizationError:
            raise
        except Exception as exc:
            raise FinalizationError("Microsoft Word finalization failed") from exc
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
                "LibreOffice field finalization requires PDF export in this beta"
            )
        try:
            pdf.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FinalizationError("Could not create the PDF output directory") from exc

        try:
            temporary = Path(tempfile.mkdtemp(prefix="papercraft-lo-"))
        except OSError as exc:
            raise FinalizationError("Could not create an isolated LibreOffice profile") from exc
        try:
            try:
                profile = temporary / "profile"
                profile.mkdir()
                environment = self._libreoffice_environment(profile)
            except OSError as exc:
                raise FinalizationError("Could not prepare the isolated LibreOffice profile") from exc
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            generated = temporary / f"{docx.stem}.pdf"
            updated_docx = temporary / f"{docx.stem}.updated.docx"
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
                    updated_docx,
                    creationflags,
                    environment,
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
                    environment,
                )
            _pdf_result(generated)
            self._copy_pdf_atomically(generated, pdf)
            if fields_updated:
                _docx_result(updated_docx)
                self._copy_docx_atomically(updated_docx, docx)
        finally:
            self._cleanup_working_directory(temporary)
        _pdf_result(pdf)
        return fields_updated

    @staticmethod
    def _libreoffice_environment(profile: Path) -> dict[str, str]:
        """Keep profile, caches, and temporary files inside one disposable directory."""

        home = profile / "home"
        config = profile / "config"
        cache = profile / "cache"
        data = profile / "data"
        temporary = profile / "tmp"
        for directory in (home, config, cache, data, temporary):
            directory.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ)
        environment.update(
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "XDG_CONFIG_HOME": str(config),
                "XDG_CACHE_HOME": str(cache),
                "XDG_DATA_HOME": str(data),
                "TEMP": str(temporary),
                "TMP": str(temporary),
            }
        )
        return environment

    @staticmethod
    def _copy_pdf_atomically(source: Path, destination: Path) -> None:
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.stem}-",
                suffix=".tmp",
                dir=destination.parent,
            )
        except OSError as exc:
            raise FinalizationError("Could not stage the LibreOffice PDF output") from exc
        os.close(descriptor)
        staged = Path(temporary_name)
        try:
            shutil.copyfile(source, staged)
            _pdf_result(staged)
            os.replace(staged, destination)
        except FinalizationError:
            raise
        except OSError as exc:
            raise FinalizationError("Could not place the LibreOffice PDF output") from exc
        finally:
            with suppress(OSError):
                staged.unlink(missing_ok=True)

    @staticmethod
    def _copy_docx_atomically(source: Path, destination: Path) -> None:
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.stem}-",
                suffix=".tmp",
                dir=destination.parent,
            )
        except OSError as exc:
            raise FinalizationError("Could not stage the LibreOffice DOCX output") from exc
        os.close(descriptor)
        staged = Path(temporary_name)
        try:
            shutil.copyfile(source, staged)
            _docx_result(staged)
            os.replace(staged, destination)
        except FinalizationError:
            raise
        except OSError as exc:
            raise FinalizationError("Could not place the LibreOffice DOCX output") from exc
        finally:
            with suppress(OSError):
                staged.unlink(missing_ok=True)

    @classmethod
    def _cleanup_working_directory(cls, path: Path) -> None:
        for attempt in range(cls._PROFILE_CLEANUP_ATTEMPTS):
            try:
                shutil.rmtree(path)
                return
            except FileNotFoundError:
                return
            except OSError:
                if attempt + 1 < cls._PROFILE_CLEANUP_ATTEMPTS:
                    time.sleep(cls._PROFILE_CLEANUP_DELAY_SECONDS)

    def _convert_without_field_update(
        self,
        executable: Path,
        profile: Path,
        temporary: Path,
        docx: Path,
        creationflags: int,
        environment: dict[str, str],
    ) -> None:
        try:
            completed = subprocess.run(
                [
                    str(executable),
                    "--headless",
                    "--nologo",
                    "--norestore",
                    "--nodefault",
                    "--nofirststartwizard",
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
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise FinalizationError(
                f"LibreOffice timed out after {self.timeout_seconds:g} seconds"
            ) from exc
        except OSError as exc:
            raise FinalizationError("LibreOffice could not start the PDF conversion") from exc
        generated = temporary / f"{docx.stem}.pdf"
        if completed.returncode != 0:
            raise FinalizationError(
                f"LibreOffice PDF conversion failed (exit code {completed.returncode})"
            )
        if not generated.is_file():
            raise FinalizationError("LibreOffice completed without producing a PDF")

    def _export_with_uno(
        self,
        executable: Path,
        python: Path,
        helper: Path,
        profile: Path,
        docx: Path,
        generated: Path,
        updated_docx: Path,
        creationflags: int,
        environment: dict[str, str],
    ) -> None:
        try:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = int(reservation.getsockname()[1])
        except OSError as exc:
            raise FinalizationError("LibreOffice could not reserve a local update port") from exc
        try:
            listener = subprocess.Popen(
                [
                    str(executable),
                    "--headless",
                    "--nologo",
                    "--norestore",
                    "--nodefault",
                    "--nofirststartwizard",
                    f"-env:UserInstallation={profile.as_uri()}",
                    "--accept="
                    f"socket,host=127.0.0.1,port={port};urp;StarOffice.ServiceManager",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                env=environment,
            )
        except OSError as exc:
            raise FinalizationError("LibreOffice could not start the field-update service") from exc

        helper_environment = dict(environment)
        helper_environment["PYTHONPATH"] = str(python.parent)
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
                    "--updated-docx",
                    str(updated_docx),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                creationflags=creationflags,
                cwd=python.parent,
                env=helper_environment,
            )
            if completed.returncode != 0:
                raise FinalizationError(
                    "LibreOffice field update and PDF export failed "
                    f"(exit code {completed.returncode})"
                )
            if not generated.is_file():
                raise FinalizationError("LibreOffice field update completed without producing a PDF")
        except subprocess.TimeoutExpired as exc:
            raise FinalizationError(
                f"LibreOffice UNO export timed out after {self.timeout_seconds:g} seconds"
            ) from exc
        except OSError as exc:
            raise FinalizationError("LibreOffice could not start the field-update helper") from exc
        finally:
            self._stop_process(listener)

    @classmethod
    def _stop_process(cls, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        with suppress(OSError):
            process.terminate()
        try:
            process.wait(timeout=cls._PROCESS_STOP_TIMEOUT_SECONDS)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        with suppress(OSError):
            process.kill()
        with suppress(OSError, subprocess.TimeoutExpired):
            process.wait(timeout=cls._PROCESS_STOP_TIMEOUT_SECONDS)

    def _find_libreoffice(self) -> Path | None:
        if self.libreoffice_path is not None:
            if not self.libreoffice_path.is_file():
                return None
            if os.name == "nt" and self.libreoffice_path.suffix.casefold() == ".exe":
                console = self.libreoffice_path.with_suffix(".com")
                if console.is_file():
                    return console
            return self.libreoffice_path
        names = ("soffice.com", "soffice", "libreoffice") if os.name == "nt" else (
            "soffice",
            "libreoffice",
        )
        for name in names:
            found = shutil.which(name)
            if found:
                return Path(found).resolve()
        if os.name == "nt":
            configured = os.getenv("PAPERCRAFT_LIBREOFFICE")
            bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
            candidates: list[Path] = []
            if configured:
                candidates.append(Path(configured))
            candidates.extend(
                [
                bundle_root / "libreoffice" / "program" / "soffice.com",
                Path(sys.executable).resolve().parent
                / "runtime"
                / "libreoffice"
                / "program"
                / "soffice.com",
                Path(__file__).resolve().parents[4]
                / "runtime"
                / "libreoffice"
                / "program"
                / "soffice.com",
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
                / "LibreOffice"
                / "program"
                / "soffice.com",
                Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
                / "LibreOffice"
                / "program"
                / "soffice.com",
                ]
            )
            for candidate in candidates:
                if candidate.is_file():
                    if candidate.suffix.casefold() == ".exe" and candidate.with_suffix(
                        ".com"
                    ).is_file():
                        return candidate.with_suffix(".com").resolve()
                    return candidate.resolve()
            return None
        return None


def _pdf_result(path: Path) -> PDFResult:
    if not path.is_file() or path.stat().st_size < 8:
        raise FinalizationError("PDF was not produced")
    try:
        with path.open("rb") as stream:
            header = stream.read(5)
    except OSError as exc:
        raise FinalizationError("PDF output could not be read") from exc
    if header != b"%PDF-":
        raise FinalizationError("PDF output does not have a valid header")
    return PDFResult(path=path, size_bytes=path.stat().st_size, valid_header=True)


def _docx_result(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 8:
        raise FinalizationError("Updated DOCX was not produced")
    try:
        with zipfile.ZipFile(path) as archive:
            if "word/document.xml" not in archive.namelist():
                raise FinalizationError("Updated DOCX does not contain a document body")
    except FinalizationError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise FinalizationError("Updated DOCX is not a valid Office document") from exc


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
