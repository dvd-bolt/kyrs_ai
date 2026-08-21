from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from papercraft.config import AppSettings
from papercraft.domain import AutopilotOptions, Project, ProjectBrief, Source, SourceRole
from papercraft.infrastructure.ingest import (
    ImportResult,
    ParseResult,
    ParserRegistry,
    SafeSourceImporter,
)
from papercraft.infrastructure.persistence import ProjectPaths, SQLiteRepository


@dataclass(frozen=True, slots=True)
class ProjectWorkspace:
    project: Project
    paths: ProjectPaths
    repository: SQLiteRepository


class ProjectService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.settings.ensure_directories()

    def create(
        self,
        brief: ProjectBrief,
        options: AutopilotOptions | None = None,
    ) -> ProjectWorkspace:
        project = Project(brief=brief, options=options or AutopilotOptions())
        paths = ProjectPaths.for_project(
            project.id,
            self.settings.projects_root,
            create=True,
        )
        repository = SQLiteRepository(paths.database)
        repository.save_project(project)
        return ProjectWorkspace(project=project, paths=paths, repository=repository)

    def open(self, project_id: str) -> ProjectWorkspace:
        paths = ProjectPaths.for_project(project_id, self.settings.projects_root)
        if not paths.database.exists():
            raise FileNotFoundError(f"PaperCraft project not found: {project_id}")
        repository = SQLiteRepository(paths.database)
        project = repository.get_project(project_id)
        if project is None:
            raise ValueError(f"Project database does not contain project {project_id}")
        return ProjectWorkspace(project=project, paths=paths, repository=repository)

    def update(
        self,
        project_id: str,
        *,
        brief: ProjectBrief | None = None,
        options: AutopilotOptions | None = None,
    ) -> ProjectWorkspace:
        workspace = self.open(project_id)
        project = workspace.project.model_copy(deep=True)
        if brief is not None:
            project.brief = brief
        if options is not None:
            project.options = options
        project.updated_at = datetime.now(UTC)
        workspace.repository.save_project(project)
        return ProjectWorkspace(project=project, paths=workspace.paths, repository=workspace.repository)

    def list(self) -> list[Project]:
        projects: list[Project] = []
        if not self.settings.projects_root.exists():
            return projects
        for child in self.settings.projects_root.iterdir():
            if not child.is_dir() or not (child / "project.db").exists():
                continue
            try:
                repository = SQLiteRepository(child / "project.db")
                project = repository.get_project(child.name)
            except Exception:
                continue
            if project is not None:
                projects.append(project)
        return sorted(projects, key=lambda item: item.updated_at, reverse=True)


class SourceService:
    """Import, classify, parse and persist immutable project sources."""

    def __init__(self, workspace: ProjectWorkspace) -> None:
        self.workspace = workspace
        self.importer = SafeSourceImporter(
            workspace.project.id,
            workspace.paths.originals,
        )
        self.parsers = ParserRegistry()

    def import_files(
        self,
        paths: Iterable[str | Path],
        role: SourceRole | str | None = None,
        *,
        parse: bool = True,
    ) -> ImportResult:
        result = self.importer.import_paths(paths, role=role)
        for source in result.sources:
            self.workspace.repository.save_source(source)
            if parse:
                self.reprocess(source.id)
        return result

    def classify(self, source_id: str, role: SourceRole) -> Source:
        source = self.workspace.repository.get_source(source_id)
        if source is None:
            raise KeyError(f"Unknown source: {source_id}")
        updated = source.model_copy(update={"role": role})
        self.workspace.repository.save_source(updated)
        return updated

    def reprocess(self, source_id: str) -> ParseResult:
        source = self.workspace.repository.get_source(source_id)
        if source is None:
            raise KeyError(f"Unknown source: {source_id}")
        parsed = self.parsers.parse(source)
        for fragment in parsed.fragments:
            self.workspace.repository.save_fragment(fragment)
        return parsed
