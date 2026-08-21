"""Immutable input storage and atomic JSON artifact writes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sha256_file(path: str | os.PathLike[str], *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_filename(name: str) -> str:
    cleaned = _INVALID_FILENAME.sub("_", Path(name).name).strip(" .")
    if not cleaned:
        cleaned = "source"
    stem, suffix = Path(cleaned).stem, Path(cleaned).suffix
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    max_stem = max(1, 120 - len(suffix))
    return f"{stem[:max_stem]}{suffix[:20]}"


def _resolve_child(root: Path, relative_path: str | os.PathLike[str]) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ValueError("artifact path must be relative")
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError("artifact path escapes its storage root") from error
    return target


@dataclass(frozen=True, slots=True)
class StoredFile:
    path: Path
    sha256: str
    size_bytes: int
    original_name: str


class ImmutableFileStorage:
    """Content-addressed copies of user inputs.

    Existing objects are verified and never overwritten.  This allows Source
    records to keep a stable checksum for the full lifetime of a project.
    """

    def __init__(self, root: str | os.PathLike[str], *, maximum_bytes: int | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.maximum_bytes = maximum_bytes

    def store(self, source_path: str | os.PathLike[str]) -> StoredFile:
        source = Path(source_path)
        if not source.is_file():
            raise FileNotFoundError(f"input is not a readable file: {source}")
        size = source.stat().st_size
        if self.maximum_bytes is not None and size > self.maximum_bytes:
            raise ValueError(f"input exceeds the {self.maximum_bytes}-byte limit")
        checksum = sha256_file(source)
        if source.stat().st_size != size:
            raise OSError("input changed while it was being hashed")
        target = self.root / f"{checksum}_{_safe_filename(source.name)}"
        if target.exists():
            if target.stat().st_size != size or sha256_file(target) != checksum:
                raise OSError(f"immutable object is corrupt: {target}")
            return StoredFile(target, checksum, size, source.name)

        descriptor, temporary_name = tempfile.mkstemp(prefix=".incoming-", dir=self.root)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if temporary.stat().st_size != size or sha256_file(temporary) != checksum:
                raise OSError("input changed while it was being copied")
            # Another worker may have completed the same content in parallel.
            if target.exists():
                if sha256_file(target) != checksum:
                    raise OSError(f"immutable object is corrupt: {target}")
            else:
                os.replace(temporary, target)
            return StoredFile(target, checksum, size, source.name)
        finally:
            temporary.unlink(missing_ok=True)


class AtomicArtifactStore:
    """Atomically write JSON values beneath a fixed artifact root."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def write_json(
        self,
        relative_path: str | os.PathLike[str],
        value: BaseModel | Any,
        *,
        overwrite: bool = True,
    ) -> Path:
        target = _resolve_child(self.root, relative_path)
        if target.suffix.lower() != ".json":
            raise ValueError("JSON artifact filenames must end in .json")
        if target.exists() and not overwrite:
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            return target
        finally:
            temporary.unlink(missing_ok=True)

    def read_json(self, relative_path: str | os.PathLike[str]) -> Any:
        target = _resolve_child(self.root, relative_path)
        with target.open("r", encoding="utf-8") as stream:
            return json.load(stream)
