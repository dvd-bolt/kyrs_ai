"""Value objects used by the ingestion boundary.

These are intentionally infrastructure types.  Persisted source and fragment
objects live in :mod:`papercraft.domain`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from papercraft.domain import Source, SourceFragment


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """A source-role prediction together with an auditable explanation."""

    role: Any
    confidence: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ImportRejection:
    """A file that was deliberately not copied into a project."""

    path: Path
    reason: str


@dataclass(slots=True)
class ImportResult:
    """Result of one safe import operation."""

    sources: list[Source] = field(default_factory=list)
    rejected: list[ImportRejection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def extend(self, other: ImportResult) -> None:
        self.sources.extend(other.sources)
        self.rejected.extend(other.rejected)
        self.warnings.extend(other.warnings)


@dataclass(slots=True)
class ParseResult:
    """Fragments plus non-fatal extraction diagnostics."""

    source_id: str
    fragments: list[SourceFragment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_text(self) -> bool:
        return any(fragment.content.strip() for fragment in self.fragments)


class IngestionError(RuntimeError):
    """Base error for invalid or unsafe ingestion input."""


class ImportLimitError(IngestionError):
    """An archive or directory exceeded a configured safety limit."""


class UnsafeArchiveError(IngestionError):
    """An archive contains an unsafe member."""


class UnsupportedSourceError(IngestionError):
    """No parser supports the supplied source."""


class OptionalDependencyError(IngestionError):
    """A parser cannot run because an optional dependency is unavailable."""

    def __init__(self, dependency: str, source_kind: str) -> None:
        self.dependency = dependency
        self.source_kind = source_kind
        super().__init__(
            f"Parsing {source_kind} requires optional dependency {dependency!r}"
        )
