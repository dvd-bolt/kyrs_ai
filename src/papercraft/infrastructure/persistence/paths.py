"""Filesystem layout for durable PaperCraft projects."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def default_projects_root() -> Path:
    """Return the per-user project root without creating it."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "PaperCraftAI" / "projects"
    return Path.home() / "AppData" / "Local" / "PaperCraftAI" / "projects"


def _validate_project_id(project_id: str) -> str:
    device_name = project_id.split(".", 1)[0].upper()
    if (
        not _SAFE_COMPONENT.fullmatch(project_id)
        or project_id in {".", ".."}
        or device_name in _WINDOWS_RESERVED
    ):
        raise ValueError("project_id must be a safe filesystem component")
    return project_id


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    """All paths owned by one project.

    Paths are resolved from a trusted root and a validated project identifier;
    user-controlled filenames are never interpolated here.
    """

    projects_root: Path
    project_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "projects_root", Path(self.projects_root).expanduser().resolve())
        _validate_project_id(self.project_id)

    @classmethod
    def for_project(
        cls,
        project_id: str,
        projects_root: str | os.PathLike[str] | None = None,
        *,
        create: bool = False,
    ) -> ProjectPaths:
        paths = cls(Path(projects_root) if projects_root is not None else default_projects_root(), project_id)
        if create:
            paths.ensure()
        return paths

    @property
    def root(self) -> Path:
        candidate = (self.projects_root / self.project_id).resolve()
        try:
            candidate.relative_to(self.projects_root)
        except ValueError as error:
            raise ValueError("project path escapes the configured project root") from error
        return candidate

    @property
    def database(self) -> Path:
        return self.root / "project.db"

    @property
    def inputs(self) -> Path:
        return self.root / "inputs"

    @property
    def originals(self) -> Path:
        return self.inputs / "originals"

    @property
    def derived(self) -> Path:
        return self.root / "derived"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    def ensure(self) -> ProjectPaths:
        """Create the project layout and return ``self`` for fluent use."""

        for directory in (
            self.originals,
            self.derived,
            self.runs,
            self.artifacts,
            self.backups,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self
