"""Safe, immutable import of project inputs."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import stat
import tempfile
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO

from papercraft.domain import Source, SourceRole

from ._domain import construct
from .classification import CODE_SUFFIXES, IMAGE_SUFFIXES, SourceClassifier
from .security import SecretScanner, is_excluded_path, is_secret_path, looks_binary
from .types import (
    ImportLimitError,
    ImportRejection,
    ImportResult,
    UnsafeArchiveError,
)

_DOCUMENT_SUFFIXES = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md", ".markdown"}
_ALLOWED_SUFFIXES = _DOCUMENT_SUFFIXES | set(IMAGE_SUFFIXES) | set(CODE_SUFFIXES) | {
    ".json", ".xml", ".ini", ".cfg", ".rst", ".tex", ".ipynb",
}
_OPAQUE_SUPPORTED = {".pdf", ".docx", ".xlsx"} | set(IMAGE_SUFFIXES)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul", *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_WINDOWS_INVALID_CHARACTERS = frozenset('<>:"|?*')


@dataclass(frozen=True, slots=True)
class ImportPolicy:
    max_files: int = 10_000
    max_file_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 512 * 1024 * 1024
    max_archive_ratio: float = 200.0
    max_member_path: int = 240
    reject_files_with_secrets: bool = True
    allow_unknown_text_files: bool = True


def _source_role(value: SourceRole | str | None) -> SourceRole | None:
    if value is None or isinstance(value, SourceRole):
        return value
    try:
        return SourceRole(value.casefold())
    except ValueError:
        try:
            return SourceRole[value.upper()]
        except KeyError:
            raise


def _safe_archive_member(name: str, max_length: int) -> PurePosixPath:
    if not name or "\x00" in name or len(name) > max_length:
        raise UnsafeArchiveError(f"Unsafe ZIP member name: {name!r}")
    normalized = name.replace("\\", "/")
    raw_parts = normalized.split("/")
    member = PurePosixPath(normalized)
    if (
        member.is_absolute()
        or normalized.startswith(("/", "//"))
        or _WINDOWS_DRIVE.match(normalized)
        or any(part in {"", ".", ".."} or _unsafe_windows_part(part) for part in raw_parts)
    ):
        raise UnsafeArchiveError(f"ZIP member escapes the import directory: {name!r}")
    return member


def _unsafe_windows_part(part: str) -> bool:
    base_name = part.split(".", 1)[0].casefold()
    return (
        part.endswith((" ", "."))
        or base_name in _WINDOWS_RESERVED_NAMES
        or any(character in _WINDOWS_INVALID_CHARACTERS or ord(character) < 32 for character in part)
    )


def _zip_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _hash_stream(stream: IO[bytes], destination: Path, limit: int) -> tuple[str, int, bytes]:
    digest = hashlib.sha256()
    total = 0
    sample = bytearray()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > limit:
                raise ImportLimitError(f"File exceeds {limit} bytes")
            digest.update(block)
            if len(sample) < 128 * 1024:
                sample.extend(block[: 128 * 1024 - len(sample)])
            output.write(block)
        output.flush()
        os.fsync(output.fileno())
    return digest.hexdigest(), total, bytes(sample)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _sample_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""


class SafeSourceImporter:
    """Copy approved inputs into a project's immutable originals directory.

    The importer never uses ``ZipFile.extract``.  ZIP members are validated and
    streamed into temporary files before an atomic rename.
    """

    def __init__(
        self,
        project_id: str,
        destination_root: str | Path,
        *,
        policy: ImportPolicy | None = None,
        classifier: SourceClassifier | None = None,
        secret_scanner: SecretScanner | None = None,
    ) -> None:
        self.project_id = project_id
        self.destination_root = Path(destination_root).resolve()
        self.policy = policy or ImportPolicy()
        self.classifier = classifier or SourceClassifier()
        self.secret_scanner = secret_scanner or SecretScanner()
        self._count = 0
        self._total_bytes = 0

    def import_paths(
        self,
        paths: Iterable[str | Path],
        *,
        role: SourceRole | str | None = None,
    ) -> ImportResult:
        result = ImportResult()
        selected_role = _source_role(role)
        for raw_path in paths:
            result.extend(self.import_path(raw_path, role=selected_role))
        return result

    def import_path(
        self,
        path: str | Path,
        *,
        role: SourceRole | str | None = None,
    ) -> ImportResult:
        source_path = Path(path)
        result = ImportResult()
        selected_role = _source_role(role)
        if not source_path.exists():
            result.rejected.append(ImportRejection(source_path, "not-found"))
            return result
        if source_path.is_symlink():
            result.rejected.append(ImportRejection(source_path, "symlink"))
            return result
        if source_path.is_dir():
            return self._import_directory(source_path, selected_role)
        if source_path.suffix.casefold() == ".zip":
            return self._import_zip(source_path, selected_role)
        imported = self._import_file(source_path, Path(source_path.name), selected_role)
        if isinstance(imported, Source):
            result.sources.append(imported)
        else:
            result.rejected.append(imported)
        return result

    def _import_directory(self, directory: Path, role: SourceRole | None) -> ImportResult:
        result = ImportResult()
        base = directory.resolve()
        for current, directory_names, file_names in os.walk(base, followlinks=False):
            current_path = Path(current)
            directory_names[:] = sorted(
                name
                for name in directory_names
                if not is_excluded_path(Path(name))
                and not (current_path / name).is_symlink()
            )
            for name in sorted(file_names):
                path = current_path / name
                relative = Path(directory.name) / path.relative_to(base)
                if path.is_symlink():
                    result.rejected.append(ImportRejection(path, "symlink"))
                    continue
                imported = self._import_file(path, relative, role)
                if isinstance(imported, Source):
                    result.sources.append(imported)
                else:
                    result.rejected.append(imported)
        return result

    def _import_zip(self, archive: Path, role: SourceRole | None) -> ImportResult:
        result = ImportResult()
        try:
            package = zipfile.ZipFile(archive)
        except (zipfile.BadZipFile, OSError) as error:
            result.rejected.append(ImportRejection(archive, f"invalid-zip:{error}"))
            return result

        with package:
            infos = package.infolist()
            if len(infos) > self.policy.max_files - self._count:
                raise ImportLimitError("ZIP contains too many members")
            for info in infos:
                if info.is_dir():
                    continue
                try:
                    member = _safe_archive_member(info.filename, self.policy.max_member_path)
                except UnsafeArchiveError as error:
                    result.rejected.append(ImportRejection(archive, str(error)))
                    continue
                display_path = Path(f"{archive.name}!/{member.as_posix()}")
                if info.flag_bits & 0x1:
                    result.rejected.append(ImportRejection(display_path, "encrypted-zip-member"))
                    continue
                if _zip_is_symlink(info):
                    result.rejected.append(ImportRejection(display_path, "zip-symlink"))
                    continue
                member_path = Path(*member.parts)
                if is_excluded_path(member_path) or is_secret_path(member_path):
                    result.rejected.append(ImportRejection(display_path, "excluded-path"))
                    continue
                if member_path.suffix.casefold() == ".zip":
                    result.rejected.append(ImportRejection(display_path, "nested-archive"))
                    continue
                if info.file_size > self.policy.max_file_bytes:
                    result.rejected.append(ImportRejection(display_path, "file-too-large"))
                    continue
                ratio = info.file_size / max(1, info.compress_size)
                if ratio > self.policy.max_archive_ratio:
                    result.rejected.append(ImportRejection(display_path, "suspicious-compression-ratio"))
                    continue
                destination_relative = Path(archive.stem) / member_path
                try:
                    with package.open(info, "r") as stream:
                        imported = self._import_stream(
                            stream,
                            original_name=member.as_posix(),
                            relative=destination_relative,
                            role=role,
                            declared_size=info.file_size,
                        )
                except (ImportLimitError, OSError, RuntimeError) as error:
                    result.rejected.append(ImportRejection(display_path, f"read-error:{error}"))
                    continue
                if isinstance(imported, Source):
                    result.sources.append(imported)
                else:
                    result.rejected.append(imported)
        return result

    def _import_file(
        self,
        path: Path,
        relative: Path,
        role: SourceRole | None,
    ) -> Source | ImportRejection:
        if is_excluded_path(relative) or is_secret_path(relative):
            return ImportRejection(path, "excluded-path")
        try:
            size = path.stat().st_size
        except OSError as error:
            return ImportRejection(path, f"stat-error:{error}")
        if size > self.policy.max_file_bytes:
            return ImportRejection(path, "file-too-large")
        try:
            with path.open("rb") as stream:
                return self._import_stream(
                    stream,
                    original_name=str(relative),
                    relative=relative,
                    role=role,
                    declared_size=size,
                )
        except (OSError, ImportLimitError) as error:
            return ImportRejection(path, f"read-error:{error}")

    def _import_stream(
        self,
        stream: IO[bytes],
        *,
        original_name: str,
        relative: Path,
        role: SourceRole | None,
        declared_size: int,
    ) -> Source | ImportRejection:
        if self._count >= self.policy.max_files:
            raise ImportLimitError("Import contains too many files")
        if self._total_bytes + declared_size > self.policy.max_total_bytes:
            raise ImportLimitError("Import exceeds total byte limit")
        suffix = relative.suffix.casefold()
        if suffix not in _ALLOWED_SUFFIXES and not self.policy.allow_unknown_text_files:
            return ImportRejection(Path(original_name), "unsupported-extension")

        self.destination_root.mkdir(parents=True, exist_ok=True)
        safe_relative = self._safe_destination(relative)
        safe_relative.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{safe_relative.name}.", suffix=".tmp", dir=safe_relative.parent, delete=False
        ) as temporary_handle:
            temporary_path = Path(temporary_handle.name)
        try:
            digest, size, sample = _hash_stream(stream, temporary_path, self.policy.max_file_bytes)
            if size != declared_size:
                raise ImportLimitError("File size changed while importing")
            if suffix not in _OPAQUE_SUPPORTED and looks_binary(sample):
                return ImportRejection(Path(original_name), "binary-file")
            if self.policy.reject_files_with_secrets and suffix not in _OPAQUE_SUPPORTED:
                findings = self.secret_scanner.scan_bytes(sample)
                if findings:
                    kinds = ",".join(sorted({finding.kind for finding in findings}))
                    return ImportRejection(Path(original_name), f"secret-detected:{kinds}")

            destination, already_present = self._deduplicated_destination(safe_relative, digest)
            if not already_present:
                os.replace(temporary_path, destination)
            classification = self.classifier.classify(
                relative, "" if suffix in _OPAQUE_SUPPORTED else _sample_text(sample)
            )
            selected_role = role or classification.role
            source_id = str(uuid.uuid4())
            media_type = mimetypes.guess_type(relative.name)[0] or "application/octet-stream"
            source = construct(
                Source,
                id=source_id,
                project_id=self.project_id,
                role=selected_role,
                original_name=original_name,
                stored_path=str(destination),
                sha256=digest,
                mime_type=media_type,
                media_type=media_type,
                size_bytes=size,
                classification_confidence=classification.confidence if role is None else 1.0,
                created_at=datetime.now(UTC),
                metadata={"relative_path": original_name, "immutable": True},
            )
            self._count += 1
            self._total_bytes += size
            return source
        finally:
            temporary_path.unlink(missing_ok=True)

    def _safe_destination(self, relative: Path) -> Path:
        raw_parts = list(relative.parts)
        if (
            not raw_parts
            or relative.is_absolute()
            or any(part in {"", ".", ".."} or _unsafe_windows_part(part) for part in raw_parts)
        ):
            raise UnsafeArchiveError(f"Unsafe destination: {relative}")
        candidate = (self.destination_root / Path(*raw_parts)).resolve()
        try:
            candidate.relative_to(self.destination_root)
        except ValueError as error:
            raise UnsafeArchiveError(f"Destination escapes import root: {relative}") from error
        return candidate

    @staticmethod
    def _deduplicated_destination(destination: Path, digest: str) -> tuple[Path, bool]:
        if not destination.exists():
            return destination, False
        try:
            existing_digest = _hash_file(destination)
        except OSError:
            existing_digest = ""
        if existing_digest == digest:
            return destination, True
        base = destination.with_name(f"{destination.stem}-{digest[:8]}{destination.suffix}")
        candidate = base
        sequence = 2
        while candidate.exists():
            try:
                if _hash_file(candidate) == digest:
                    return candidate, True
            except OSError:
                pass
            candidate = base.with_name(f"{base.stem}-{sequence}{base.suffix}")
            sequence += 1
        return candidate, False


def import_sources(
    project_id: str,
    destination_root: str | Path,
    paths: Iterable[str | Path],
    *,
    role: SourceRole | str | None = None,
    policy: ImportPolicy | None = None,
) -> ImportResult:
    """Convenience entry point for application services."""

    return SafeSourceImporter(project_id, destination_root, policy=policy).import_paths(
        paths, role=role
    )


ALLOWED_SUFFIXES = frozenset(_ALLOWED_SUFFIXES)
