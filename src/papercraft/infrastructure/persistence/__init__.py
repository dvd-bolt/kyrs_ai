"""Persistence adapters for the local PaperCraft desktop application."""

from .legacy import LegacyCourseProjectImporter, LegacyImportResult
from .paths import ProjectPaths, default_projects_root
from .repository import SQLiteProjectRepository, SQLiteRepository
from .storage import AtomicArtifactStore, ImmutableFileStorage, StoredFile, sha256_file

__all__ = [
    "AtomicArtifactStore",
    "ImmutableFileStorage",
    "LegacyCourseProjectImporter",
    "LegacyImportResult",
    "ProjectPaths",
    "SQLiteProjectRepository",
    "SQLiteRepository",
    "StoredFile",
    "default_projects_root",
    "sha256_file",
]
