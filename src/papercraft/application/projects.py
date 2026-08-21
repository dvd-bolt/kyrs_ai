from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from papercraft.config import AppSettings
from papercraft.domain import (
    AutopilotOptions,
    BackupRecord,
    Project,
    ProjectBrief,
    ProjectHealth,
    Source,
    SourceRole,
)
from papercraft.infrastructure.ingest import (
    ImportResult,
    ParseResult,
    ParserRegistry,
    SafeSourceImporter,
)
from papercraft.infrastructure.persistence import (
    MigrationService,
    ProjectPaths,
    SQLiteRepository,
    sha256_file,
)


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

    def check_health(self, project_id: str) -> ProjectHealth:
        workspace = self.open(project_id)
        integrity_ok, messages = workspace.repository.integrity_check()
        missing = [
            artifact.id
            for artifact in workspace.repository.list_artifacts(project_id)
            if not (workspace.paths.root / artifact.path).is_file()
        ]
        input_hash_valid = all(
            (workspace.paths.originals / Path(source.stored_path).name).is_file()
            and sha256_file(workspace.paths.originals / Path(source.stored_path).name) == source.sha256
            for source in workspace.repository.list_sources(project_id)
        )
        warnings = [] if integrity_ok else messages
        if missing:
            warnings.append(f"missing artifacts: {len(missing)}")
        if not input_hash_valid:
            warnings.append("one or more imported source files are missing or changed")
        return ProjectHealth(
            project_id=project_id,
            schema_version=workspace.repository.schema_version,
            integrity_ok=integrity_ok,
            input_hash_valid=input_hash_valid,
            missing_artifact_ids=missing,
            warnings=warnings,
        )

    def backup(self, project_id: str, *, label: str = "", automatic: bool = False) -> BackupRecord:
        workspace = self.open(project_id)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = workspace.paths.backups / f"{stamp}_{'auto' if automatic else 'manual'}.db"
        backup = workspace.repository.backup_to(target)
        record = BackupRecord(
            project_id=project_id,
            path=str(backup),
            sha256=sha256_file(backup),
            size_bytes=backup.stat().st_size,
            automatic=automatic,
            label=label,
        )
        workspace.repository.save_backup_record(record)
        if automatic:
            automatic_records = [item for item in workspace.repository.list_backup_records(project_id) if item.automatic]
            for expired in automatic_records[10:]:
                Path(expired.path).unlink(missing_ok=True)
                workspace.repository.delete_backup_record(expired.id)
        return record

    def restore(self, backup_id: str) -> Project:
        for project in self.list():
            workspace = self.open(project.id)
            for record in workspace.repository.list_backup_records(project.id):
                if record.id == backup_id:
                    MigrationService.restore_database(record, workspace.paths.database)
                    restored = SQLiteRepository(workspace.paths.database).get_project(project.id)
                    if restored is None:
                        raise ValueError("restored database does not contain its project")
                    return restored
        raise KeyError(f"Unknown backup: {backup_id}")


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
