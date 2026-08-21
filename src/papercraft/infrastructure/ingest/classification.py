"""Deterministic first-pass source classification."""

from __future__ import annotations

import re
from pathlib import Path

from papercraft.domain import SourceRole

from .types import ClassificationResult

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
_CODE_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt",
    ".cs", ".cpp", ".cc", ".c", ".h", ".hpp", ".go", ".rs", ".rb",
    ".php", ".swift", ".scala", ".sql", ".vue", ".svelte", ".html",
    ".css", ".scss", ".sh", ".ps1", ".toml", ".yaml", ".yml",
}

_ROLE_PATTERNS: tuple[tuple[SourceRole, tuple[str, ...]], ...] = (
    (SourceRole.METHODOLOGY, ("method", "guideline", "requirements", "методич", "требован")),
    (SourceRole.TEMPLATE, ("template", "title.page", "шаблон", "титул")),
    (SourceRole.EXAMPLE, ("example", "sample", "образец", "пример", r"готов\w*\s+работ")),
)


def _role(name: str) -> SourceRole:
    """Resolve enum values across conventional upper/lower member names."""

    try:
        return SourceRole[name.upper()]
    except KeyError:
        return SourceRole(name.lower())


class SourceClassifier:
    """Classify from extension, file name and an optional text sample."""

    def classify(self, path: str | Path, sample_text: str = "") -> ClassificationResult:
        source_path = Path(path)
        searchable = f"{source_path.stem} {sample_text[:4000]}".casefold()

        for role, patterns in _ROLE_PATTERNS:
            for pattern in patterns:
                if re.search(pattern, searchable, flags=re.IGNORECASE):
                    return ClassificationResult(role, 0.94, (f"matched:{pattern}",))

        if source_path.is_dir():
            return ClassificationResult(_role("codebase"), 0.82, ("directory",))
        if source_path.suffix.casefold() in _IMAGE_SUFFIXES:
            return ClassificationResult(_role("image"), 0.99, ("image-extension",))
        if source_path.suffix.casefold() in _CODE_SUFFIXES:
            return ClassificationResult(_role("codebase"), 0.98, ("code-extension",))
        if source_path.suffix.casefold() in {".zip"} and re.search(
            r"(?:src|code|project|код|проект)", searchable
        ):
            return ClassificationResult(_role("codebase"), 0.78, ("code-archive-name",))
        return ClassificationResult(_role("source_data"), 0.62, ("default",))


def classify_source(path: str | Path, sample_text: str = "") -> ClassificationResult:
    return SourceClassifier().classify(path, sample_text)


IMAGE_SUFFIXES = frozenset(_IMAGE_SUFFIXES)
CODE_SUFFIXES = frozenset(_CODE_SUFFIXES)
