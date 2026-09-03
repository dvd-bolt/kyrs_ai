"""Durable SQLite repository for projects and resumable pipeline state."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, TypeVar, cast
from uuid import uuid4

from pydantic import BaseModel

from papercraft.domain import (
    Artifact,
    BackupRecord,
    BibliographyEntry,
    Calculation,
    Citation,
    Claim,
    Dataset,
    Evidence,
    FactRecord,
    GenerationRun,
    Manuscript,
    MigrationRecord,
    Project,
    ProjectBlueprint,
    QAReport,
    ReleaseStatus,
    RemoteResource,
    RequirementSet,
    RevisionRecord,
    RunEvent,
    RunStatus,
    Source,
    SourceFragment,
    SourceSnapshot,
    StageRun,
    StageStatus,
    SubmissionRelease,
    SubmissionStatus,
)

TModel = TypeVar("TModel", bound=BaseModel)


_SCHEMA_VERSION = 5
_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_project ON sources(project_id, role);
CREATE INDEX IF NOT EXISTS idx_sources_hash ON sources(project_id, sha256);
CREATE TABLE IF NOT EXISTS fragments (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fragments_source ON fragments(source_id);
CREATE TABLE IF NOT EXISTS requirements (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_requirements_project ON requirements(project_id, created_at DESC);
CREATE TABLE IF NOT EXISTS blueprints (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_blueprints_project ON blueprints(project_id, created_at DESC);
CREATE TABLE IF NOT EXISTS manuscripts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_manuscripts_project ON manuscripts(project_id, revision DESC);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS stages (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    stage_order INTEGER NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, name)
);
CREATE INDEX IF NOT EXISTS idx_stages_run ON stages(run_id, stage_order);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
    stage_id TEXT REFERENCES stages(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id, kind);
CREATE TABLE IF NOT EXISTS qa_reports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qa_reports_run ON qa_reports(run_id, created_at DESC);
CREATE TABLE IF NOT EXISTS run_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    stage_id TEXT REFERENCES stages(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id, sequence);
CREATE TABLE IF NOT EXISTS worker_requests (
    request_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    outcome TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_worker_requests_project ON worker_requests(project_id, created_at DESC);
CREATE TABLE IF NOT EXISTS domain_objects (
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_id TEXT,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(kind, id)
);
CREATE INDEX IF NOT EXISTS idx_objects_project ON domain_objects(project_id, kind);
CREATE TABLE IF NOT EXISTS backup_records (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backups_project ON backup_records(project_id, created_at DESC);
CREATE TABLE IF NOT EXISTS migration_records (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    data TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    revision INTEGER NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, kind, revision)
);
CREATE INDEX IF NOT EXISTS idx_revisions_project ON revisions(project_id, kind, revision DESC);
CREATE TABLE IF NOT EXISTS section_revision_payloads (
    revision_id TEXT PRIMARY KEY REFERENCES revisions(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    section_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_section_revision_payloads_project
    ON section_revision_payloads(project_id, section_id, created_at DESC);
CREATE TABLE IF NOT EXISTS plan_revision_payloads (
    revision_id TEXT PRIMARY KEY REFERENCES revisions(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_revision_payloads_project
    ON plan_revision_payloads(project_id, created_at DESC);
CREATE TABLE IF NOT EXISTS submission_releases (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submission_releases_project
    ON submission_releases(project_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_submission_releases_one_ready
    ON submission_releases(project_id) WHERE status = 'READY_TO_SUBMIT';
"""


class SQLiteRepository:
    """JSON-domain repository backed by a normalized SQLite index.

    A connection is opened per operation, so the repository is safe to use
    from the UI and a background worker.  WAL enables concurrent readers while
    the worker commits a checkpoint.
    """

    def __init__(self, database: str | os.PathLike[str], *, timeout: float = 30.0) -> None:
        self.database = Path(database).expanduser().resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=self.timeout)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        """Commit/rollback and, importantly on Windows, close a connection."""

        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._session() as connection:
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise RuntimeError(f"SQLite refused WAL mode for {self.database}")
            connection.execute("PRAGMA synchronous = NORMAL")
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > _SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {current} is newer than supported schema {_SCHEMA_VERSION}"
                )
            backup_path: Path | None = None
            if 0 < current < _SCHEMA_VERSION:
                backups_dir = self.database.parent / "backups"
                backups_dir.mkdir(parents=True, exist_ok=True)
                backup_path = backups_dir / (
                    f"pre_migration_v{current}_to_v{_SCHEMA_VERSION}_{uuid4().hex[:8]}.db"
                )
                backup_connection = sqlite3.connect(backup_path)
                try:
                    connection.backup(backup_connection)
                    backup_connection.commit()
                    integrity = backup_connection.execute("PRAGMA integrity_check").fetchone()
                    if integrity is None or str(integrity[0]) != "ok":
                        raise RuntimeError("automatic pre-migration backup failed integrity check")
                finally:
                    backup_connection.close()
            try:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + _SCHEMA
                    + f"\nPRAGMA user_version = {_SCHEMA_VERSION};\nCOMMIT;"
                )
            except Exception:
                connection.rollback()
                raise
            if backup_path is not None:
                project_row = connection.execute(
                    "SELECT id FROM projects ORDER BY created_at,id LIMIT 1"
                ).fetchone()
                project_id = None if project_row is None else str(project_row["id"])
                backup: BackupRecord | None = None
                if project_id is not None:
                    backup = BackupRecord(
                        project_id=project_id,
                        path=str(backup_path),
                        sha256=self._file_sha256(backup_path),
                        size_bytes=backup_path.stat().st_size,
                        label=f"before schema migration {current}->{_SCHEMA_VERSION}",
                    )
                    connection.execute(
                        """INSERT INTO backup_records(id,project_id,data,created_at)
                           VALUES(?,?,?,?)""",
                        (
                            backup.id,
                            backup.project_id,
                            self._json(backup),
                            backup.created_at.isoformat(),
                        ),
                    )
                migration = MigrationRecord(
                    project_id=project_id,
                    from_version=current,
                    to_version=_SCHEMA_VERSION,
                    backup_id=backup.id if backup is not None else None,
                )
                connection.execute(
                    """INSERT INTO migration_records(id,project_id,data,applied_at)
                       VALUES(?,?,?,?)""",
                    (
                        migration.id,
                        migration.project_id,
                        self._json(migration),
                        migration.applied_at.isoformat(),
                    ),
                )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield an explicit immediate transaction for grouped operations."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @property
    def journal_mode(self) -> str:
        with self._session() as connection:
            return str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    @staticmethod
    def _json(model: BaseModel) -> str:
        return str(model.model_dump_json())

    @staticmethod
    def _load(row: sqlite3.Row | None, model_type: type[TModel]) -> TModel | None:
        return None if row is None else model_type.model_validate_json(row["data"])

    @staticmethod
    def _stable_hash(value: object) -> str:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _bump_project_content(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        *,
        reason: str,
    ) -> Project | None:
        row = connection.execute("SELECT data FROM projects WHERE id=?", (project_id,)).fetchone()
        project = self._load(row, Project)
        if project is None:
            return None
        project.content_revision += 1
        project.updated_at = datetime.now(project.updated_at.tzinfo)
        if project.current_release_id is not None:
            release_row = connection.execute(
                "SELECT data FROM submission_releases WHERE id=? AND project_id=?",
                (project.current_release_id, project.id),
            ).fetchone()
            release = self._load(release_row, SubmissionRelease)
            if release is not None and release.status is ReleaseStatus.READY_TO_SUBMIT:
                release = release.model_copy(
                    update={
                        "status": ReleaseStatus.SUPERSEDED,
                        "superseded_at": datetime.now(release.created_at.tzinfo),
                        "superseded_reason": reason,
                    }
                )
                connection.execute(
                    "UPDATE submission_releases SET status=?,data=? WHERE id=?",
                    (release.status.value, self._json(release), release.id),
                )
            project.current_release_id = None
            project.submission_status = SubmissionStatus.DRAFT
        connection.execute(
            "UPDATE projects SET data=?,updated_at=? WHERE id=?",
            (self._json(project), project.updated_at.isoformat(), project.id),
        )
        return project

    def save_project(self, project: Project) -> None:
        with self.transaction() as connection:
            row = connection.execute("SELECT data FROM projects WHERE id=?", (project.id,)).fetchone()
            current = self._load(row, Project)
            if current is not None:
                if current.brief != project.brief or current.options != project.options:
                    current = self._bump_project_content(
                        connection,
                        project.id,
                        reason="project_or_profile_changed",
                    ) or current
                project.content_revision = current.content_revision
                project.current_release_id = current.current_release_id
                project.submission_status = current.submission_status
            connection.execute(
                """INSERT INTO projects(id, data, created_at, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at""",
                (
                    project.id,
                    self._json(project),
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                ),
            )

    def get_project(self, project_id: str) -> Project | None:
        with self._session() as connection:
            row = connection.execute("SELECT data FROM projects WHERE id=?", (project_id,)).fetchone()
        return self._load(row, Project)

    def list_projects(self, *, limit: int = 100, offset: int = 0) -> list[Project]:
        if limit < 1 or offset < 0:
            raise ValueError("limit must be positive and offset cannot be negative")
        with self._session() as connection:
            rows = connection.execute(
                "SELECT data FROM projects ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [Project.model_validate_json(row["data"]) for row in rows]

    def save_source(self, source: Source) -> None:
        with self.transaction() as connection:
            row = connection.execute("SELECT data FROM sources WHERE id=?", (source.id,)).fetchone()
            existing = self._load(row, Source)
            if existing != source:
                self._bump_project_content(
                    connection,
                    source.project_id,
                    reason="source_changed",
                )
            connection.execute(
                """INSERT INTO sources(id,project_id,role,sha256,data,created_at) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET role=excluded.role,sha256=excluded.sha256,data=excluded.data""",
                (
                    source.id,
                    source.project_id,
                    source.role.value,
                    source.sha256,
                    self._json(source),
                    source.created_at.isoformat(),
                ),
            )

    def get_source(self, source_id: str) -> Source | None:
        with self._session() as connection:
            row = connection.execute("SELECT data FROM sources WHERE id=?", (source_id,)).fetchone()
        return self._load(row, Source)

    def list_sources(self, project_id: str, *, role: str | None = None) -> list[Source]:
        query = "SELECT data FROM sources WHERE project_id=?"
        parameters: list[object] = [project_id]
        if role is not None:
            query += " AND role=?"
            parameters.append(role)
        query += " ORDER BY created_at,id"
        with self._session() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [Source.model_validate_json(row["data"]) for row in rows]

    def save_fragment(self, fragment: SourceFragment) -> None:
        with self._session() as connection:
            connection.execute(
                """INSERT INTO fragments(id,source_id,data) VALUES(?,?,?)
                   ON CONFLICT(id) DO UPDATE SET source_id=excluded.source_id,data=excluded.data""",
                (fragment.id, fragment.source_id, self._json(fragment)),
            )

    def list_fragments(self, source_id: str) -> list[SourceFragment]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT data FROM fragments WHERE source_id=? ORDER BY id", (source_id,)
            ).fetchall()
        return [SourceFragment.model_validate_json(row["data"]) for row in rows]

    def clear_source_fragments(self, source_id: str) -> None:
        with self._session() as connection:
            connection.execute("DELETE FROM fragments WHERE source_id=?", (source_id,))

    def save_requirement_set(self, requirements: RequirementSet) -> None:
        self._save_versioned(
            "requirements", requirements.id, requirements.project_id, requirements.created_at.isoformat(), requirements
        )

    def get_latest_requirement_set(self, project_id: str) -> RequirementSet | None:
        return self._latest("requirements", project_id, RequirementSet)

    def save_blueprint(self, blueprint: ProjectBlueprint) -> None:
        self._save_versioned(
            "blueprints", blueprint.id, blueprint.project_id, blueprint.created_at.isoformat(), blueprint
        )

    def get_latest_blueprint(self, project_id: str) -> ProjectBlueprint | None:
        return self._latest("blueprints", project_id, ProjectBlueprint)

    def _save_versioned(
        self, table: str, object_id: str, project_id: str, created_at: str, model: BaseModel
    ) -> None:
        if table not in {"requirements", "blueprints"}:
            raise ValueError("unsupported versioned table")
        with self.transaction() as connection:
            row = connection.execute(f"SELECT data FROM {table} WHERE id=?", (object_id,)).fetchone()
            existing = None if row is None else model.__class__.model_validate_json(row["data"])
            if existing != model:
                self._bump_project_content(
                    connection,
                    project_id,
                    reason=f"{table}_changed",
                )
            connection.execute(
                f"""INSERT INTO {table}(id,project_id,data,created_at) VALUES(?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET data=excluded.data""",
                (object_id, project_id, self._json(model), created_at),
            )

    def _latest(self, table: str, project_id: str, model_type: type[TModel]) -> TModel | None:
        if table not in {"requirements", "blueprints"}:
            raise ValueError("unsupported versioned table")
        with self._session() as connection:
            row = connection.execute(
                f"SELECT data FROM {table} WHERE project_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return self._load(row, model_type)

    def save_manuscript(self, manuscript: Manuscript) -> None:
        with self.transaction() as connection:
            row = connection.execute("SELECT data FROM manuscripts WHERE id=?", (manuscript.id,)).fetchone()
            existing = self._load(row, Manuscript)
            if existing != manuscript:
                self._bump_project_content(
                    connection,
                    manuscript.project_id,
                    reason="manuscript_changed",
                )
            self._save_manuscript(connection, manuscript)

    def _save_manuscript(self, connection: sqlite3.Connection, manuscript: Manuscript) -> None:
        connection.execute(
            """INSERT INTO manuscripts(id,project_id,revision,data,updated_at) VALUES(?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,data=excluded.data,
               updated_at=excluded.updated_at""",
            (
                manuscript.id,
                manuscript.project_id,
                manuscript.revision,
                self._json(manuscript),
                manuscript.updated_at.isoformat(),
            ),
        )

    def get_latest_manuscript(self, project_id: str) -> Manuscript | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT data FROM manuscripts WHERE project_id=? ORDER BY revision DESC LIMIT 1", (project_id,)
            ).fetchone()
        return self._load(row, Manuscript)

    def commit_section_override(
        self,
        manuscript: Manuscript,
        section_id: str,
        payload: str,
        *,
        baseline_payload: str | None = None,
    ) -> RevisionRecord:
        """Atomically save a user-facing manuscript revision and its section history.

        The full manuscript remains a normal immutable snapshot in
        ``manuscripts``.  The compact section payload is stored independently
        so an editor can compare or restore generated/user versions without
        treating the manuscript JSON as mutable editor state.
        """

        normalized_section_id = section_id.strip()
        if not normalized_section_id:
            raise ValueError("section_id must not be blank")
        if not payload:
            raise ValueError("section revision payload must not be blank")
        with self.transaction() as connection:
            self._bump_project_content(
                connection,
                manuscript.project_id,
                reason="section_changed",
            )
            if baseline_payload is not None:
                existing = connection.execute(
                    """SELECT 1 FROM section_revision_payloads
                       WHERE project_id=? AND section_id=? LIMIT 1""",
                    (manuscript.project_id, normalized_section_id),
                ).fetchone()
                if existing is None:
                    self._insert_section_revision(
                        connection,
                        manuscript.project_id,
                        normalized_section_id,
                        baseline_payload,
                    )
            record = self._insert_section_revision(
                connection,
                manuscript.project_id,
                normalized_section_id,
                payload,
            )
            connection.execute(
                """INSERT INTO manuscripts(id,project_id,revision,data,updated_at) VALUES(?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,data=excluded.data,
                   updated_at=excluded.updated_at""",
                (
                    manuscript.id,
                    manuscript.project_id,
                    manuscript.revision,
                    self._json(manuscript),
                    manuscript.updated_at.isoformat(),
                ),
            )
        return record

    @staticmethod
    def _insert_section_revision(
        connection: sqlite3.Connection,
        project_id: str,
        section_id: str,
        payload: str,
    ) -> RevisionRecord:
        row = connection.execute(
            "SELECT COALESCE(MAX(revision), 0) AS revision FROM revisions WHERE project_id=? AND kind='manuscript'",
            (project_id,),
        ).fetchone()
        next_revision = int(row["revision"]) + 1
        record = RevisionRecord(
            project_id=project_id,
            kind="manuscript",
            revision=next_revision,
            object_id=section_id,
            sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )
        connection.execute(
            """INSERT INTO revisions(id,project_id,kind,revision,data,created_at) VALUES(?,?,?,?,?,?)""",
            (
                record.id,
                record.project_id,
                record.kind,
                record.revision,
                record.model_dump_json(),
                record.created_at.isoformat(),
            ),
        )
        connection.execute(
            """INSERT INTO section_revision_payloads(revision_id,project_id,section_id,payload,created_at)
               VALUES(?,?,?,?,?)""",
            (record.id, project_id, section_id, payload, record.created_at.isoformat()),
        )
        return record

    def list_section_revisions(self, project_id: str, section_id: str) -> list[RevisionRecord]:
        with self._session() as connection:
            rows = connection.execute(
                """SELECT revisions.data
                   FROM revisions
                   INNER JOIN section_revision_payloads
                     ON section_revision_payloads.revision_id = revisions.id
                   WHERE section_revision_payloads.project_id=?
                     AND section_revision_payloads.section_id=?
                     AND revisions.kind='manuscript'
                   ORDER BY revisions.revision DESC""",
                (project_id, section_id),
            ).fetchall()
        return [RevisionRecord.model_validate_json(row["data"]) for row in rows]

    def get_section_revision_payload(self, project_id: str, revision_id: str) -> str | None:
        with self._session() as connection:
            row = connection.execute(
                """SELECT payload FROM section_revision_payloads
                   WHERE project_id=? AND revision_id=?""",
                (project_id, revision_id),
            ).fetchone()
        return None if row is None else str(row["payload"])

    def commit_plan_override(
        self,
        blueprint: ProjectBlueprint,
        payload: str,
        *,
        baseline_payload: str | None = None,
        baseline_object_id: str | None = None,
    ) -> RevisionRecord:
        """Atomically save a user plan snapshot and its separately stored history."""

        if not payload:
            raise ValueError("plan revision payload must not be blank")
        with self.transaction() as connection:
            self._bump_project_content(
                connection,
                blueprint.project_id,
                reason="blueprint_changed",
            )
            if baseline_payload is not None:
                existing = connection.execute(
                    "SELECT 1 FROM plan_revision_payloads WHERE project_id=? LIMIT 1",
                    (blueprint.project_id,),
                ).fetchone()
                if existing is None:
                    self._insert_plan_revision(
                        connection,
                        blueprint.project_id,
                        baseline_object_id or blueprint.id,
                        baseline_payload,
                    )
            record = self._insert_plan_revision(connection, blueprint.project_id, blueprint.id, payload)
            connection.execute(
                """INSERT INTO blueprints(id,project_id,data,created_at) VALUES(?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET data=excluded.data""",
                (
                    blueprint.id,
                    blueprint.project_id,
                    self._json(blueprint),
                    blueprint.created_at.isoformat(),
                ),
            )
        return record

    @staticmethod
    def _insert_plan_revision(
        connection: sqlite3.Connection,
        project_id: str,
        blueprint_id: str,
        payload: str,
    ) -> RevisionRecord:
        row = connection.execute(
            "SELECT COALESCE(MAX(revision), 0) AS revision FROM revisions WHERE project_id=? AND kind='blueprint'",
            (project_id,),
        ).fetchone()
        next_revision = int(row["revision"]) + 1
        record = RevisionRecord(
            project_id=project_id,
            kind="blueprint",
            revision=next_revision,
            object_id=blueprint_id,
            sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )
        connection.execute(
            """INSERT INTO revisions(id,project_id,kind,revision,data,created_at) VALUES(?,?,?,?,?,?)""",
            (
                record.id,
                record.project_id,
                record.kind,
                record.revision,
                record.model_dump_json(),
                record.created_at.isoformat(),
            ),
        )
        connection.execute(
            """INSERT INTO plan_revision_payloads(revision_id,project_id,payload,created_at)
               VALUES(?,?,?,?)""",
            (record.id, project_id, payload, record.created_at.isoformat()),
        )
        return record

    def list_plan_revisions(self, project_id: str) -> list[RevisionRecord]:
        with self._session() as connection:
            rows = connection.execute(
                """SELECT revisions.data
                   FROM revisions
                   INNER JOIN plan_revision_payloads
                     ON plan_revision_payloads.revision_id = revisions.id
                   WHERE plan_revision_payloads.project_id=? AND revisions.kind='blueprint'
                   ORDER BY revisions.revision DESC""",
                (project_id,),
            ).fetchall()
        return [RevisionRecord.model_validate_json(row["data"]) for row in rows]

    def get_plan_revision_payload(self, project_id: str, revision_id: str) -> str | None:
        with self._session() as connection:
            row = connection.execute(
                """SELECT payload FROM plan_revision_payloads
                   WHERE project_id=? AND revision_id=?""",
                (project_id, revision_id),
            ).fetchone()
        return None if row is None else str(row["payload"])

    def save_run(self, run: GenerationRun) -> None:
        if run.status is RunStatus.SUCCEEDED:
            raise ValueError("RunStatus.SUCCEEDED can be persisted only by finalize_submission_release")
        with self.transaction() as connection:
            existing = connection.execute("SELECT 1 FROM runs WHERE id=?", (run.id,)).fetchone()
            if existing is None:
                self._bump_project_content(
                    connection,
                    run.project_id,
                    reason="model_policy_or_generation_changed",
                )
            connection.execute(
                """INSERT INTO runs(id,project_id,status,data,updated_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,data=excluded.data,
                   updated_at=CURRENT_TIMESTAMP""",
                (run.id, run.project_id, run.status.value, self._json(run)),
            )

    def save_run_preserving_control(
        self,
        run: GenerationRun,
        *,
        replace_metadata_keys: Collection[str] = (),
    ) -> GenerationRun:
        """Save worker state without undoing a cross-process pause/cancel.

        This is deliberately for in-flight worker bookkeeping only. Explicit
        user actions (resume/retry/pause/cancel) use their own transition
        methods and may intentionally change the durable status.
        """

        if run.status is RunStatus.SUCCEEDED:
            raise ValueError("RunStatus.SUCCEEDED can be persisted only by finalize_submission_release")
        with self.transaction() as connection:
            row = connection.execute("SELECT data FROM runs WHERE id=?", (run.id,)).fetchone()
            current = self._load(row, GenerationRun)
            if (
                current is not None
                and current.status in {RunStatus.PAUSED, RunStatus.CANCELLED}
                and run.status != current.status
            ):
                return current
            if current is not None:
                # Cost is monotonic. Retain a usage write that committed after
                # the caller obtained its in-memory GenerationRun instance.
                run.cost = max(run.cost, current.cost)
                merged_metadata = {**current.metadata, **run.metadata}
                # A shallow merge normally protects independent worker
                # bookkeeping from an older in-memory run.  Some keys are
                # intentionally replaced, however: remote-file cleanup and
                # terminal cleanup must be able to persist an empty value (or
                # a deletion) instead of resurrecting the old database value.
                for key in replace_metadata_keys:
                    if key in run.metadata:
                        merged_metadata[key] = run.metadata[key]
                    else:
                        merged_metadata.pop(key, None)
                run.metadata = merged_metadata
            connection.execute(
                """INSERT INTO runs(id,project_id,status,data,updated_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,data=excluded.data,
                   updated_at=CURRENT_TIMESTAMP""",
                (run.id, run.project_id, run.status.value, self._json(run)),
            )
        return run

    def add_run_usage(
        self,
        run_id: str,
        estimated_cost: Decimal,
        *,
        maximum_cost: Decimal | None = None,
    ) -> GenerationRun:
        """Atomically add billable usage without overwriting UI state.

        The desktop can pause/cancel a run from another process while section
        workers are recording usage.  A standalone ``get_run`` followed by
        ``save_run`` would write an old RUNNING JSON document over that user
        action.  Load and update both run and current stage in one immediate
        SQLite transaction instead.
        """

        if estimated_cost < 0:
            raise ValueError("estimated_cost must not be negative")
        with self.transaction() as connection:
            row = connection.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
            run = self._load(row, GenerationRun)
            if run is None:
                raise KeyError(run_id)
            run.cost += estimated_cost
            if maximum_cost is not None and run.cost >= maximum_cost:
                run.metadata["cost_limit_exceeded"] = True
            connection.execute(
                """UPDATE runs SET status=?,data=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (run.status.value, self._json(run), run.id),
            )
            if run.current_stage:
                stage_row = connection.execute(
                    "SELECT data FROM stages WHERE run_id=? AND name=?",
                    (run.id, run.current_stage),
                ).fetchone()
                stage = self._load(stage_row, StageRun)
                if stage is not None:
                    stage.cost += estimated_cost
                    connection.execute(
                        """UPDATE stages SET status=?,data=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (stage.status.value, self._json(stage), stage.id),
                    )
        return run

    def transition_run_status(
        self,
        run_id: str,
        *,
        status: RunStatus,
        allowed_from: set[RunStatus],
        finished_at: datetime | None = None,
    ) -> GenerationRun:
        """Compare-and-set a run status while retaining concurrent cost data."""

        if status is RunStatus.SUCCEEDED:
            raise ValueError("RunStatus.SUCCEEDED requires finalize_submission_release")
        with self.transaction() as connection:
            row = connection.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
            run = self._load(row, GenerationRun)
            if run is None:
                raise KeyError(run_id)
            if run.status not in allowed_from:
                raise RuntimeError(f"run status {run.status.value} is not eligible for transition")
            run.status = status
            if finished_at is not None:
                run.finished_at = finished_at
            connection.execute(
                """UPDATE runs SET status=?,data=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (run.status.value, self._json(run), run.id),
            )
        return run

    def prepare_retry(
        self,
        run_id: str,
        *,
        stage_names: Collection[str],
        input_hash: str,
        reason: str,
        allowed_from: set[RunStatus],
        maximum_cost: Decimal | None,
    ) -> GenerationRun:
        """Atomically invalidate a suffix and claim the retry transition.

        A desktop cancel can race a retry request from another process.  The
        status CAS and every affected ``StageRun`` reset therefore share one
        ``BEGIN IMMEDIATE`` boundary: a crash or a cancel leaves either the
        original run untouched or a complete retry-ready suffix, never a
        partially queued DAG.
        """

        if not stage_names:
            raise ValueError("stage_names must not be empty")
        with self.transaction() as connection:
            row = connection.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
            run = self._load(row, GenerationRun)
            if run is None:
                raise KeyError(run_id)
            if run.status not in allowed_from:
                raise RuntimeError(f"run status {run.status.value} is not eligible for retry")
            if maximum_cost is not None and run.cost >= maximum_cost:
                raise RuntimeError("run cost has reached the configured maximum")
            self._bump_project_content(
                connection,
                run.project_id,
                reason="generation_retried",
            )

            rows = connection.execute(
                "SELECT data FROM stages WHERE run_id=? ORDER BY stage_order,id", (run_id,)
            ).fetchall()
            for stage_row in rows:
                stage = StageRun.model_validate_json(stage_row["data"])
                if stage.name not in stage_names:
                    continue
                stage.status = StageStatus.QUEUED
                stage.started_at = None
                stage.finished_at = None
                stage.error = None
                stage.output_artifact_ids = []
                stage.output_hash = ""
                stage.failure_code = None
                stage.failure_details = {}
                stage.progress_current = 0
                stage.progress_total = 0
                stage.heartbeat_at = None
                stage.checkpoint = {"invalidated": True, "reason": reason}
                stage.input_hash = input_hash
                connection.execute(
                    """UPDATE stages SET status=?,data=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (stage.status.value, self._json(stage), stage.id),
                )

            run.status = RunStatus.RETRYING
            run.input_hash = input_hash
            run.current_stage = None
            run.finished_at = None
            run.error = None
            run.metadata.pop("terminal_hook_done", None)
            # A retry may proceed only when a changed budget leaves room for
            # another call.  Never clear a reached cap under the same budget.
            if maximum_cost is None or run.cost < maximum_cost:
                run.metadata.pop("cost_limit_exceeded", None)
            else:
                run.metadata["cost_limit_exceeded"] = True
            connection.execute(
                """UPDATE runs SET status=?,data=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (run.status.value, self._json(run), run.id),
            )
        return run

    def get_run(self, run_id: str) -> GenerationRun | None:
        with self._session() as connection:
            row = connection.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._load(row, GenerationRun)

    def list_runs(self, project_id: str) -> list[GenerationRun]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT data FROM runs WHERE project_id=? ORDER BY updated_at DESC", (project_id,)
            ).fetchall()
        return [GenerationRun.model_validate_json(row["data"]) for row in rows]

    def save_stage(self, stage: StageRun) -> None:
        with self._session() as connection:
            connection.execute(
                """INSERT INTO stages(id,run_id,name,stage_order,status,data,updated_at)
                   VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,stage_order=excluded.stage_order,
                   status=excluded.status,data=excluded.data,updated_at=CURRENT_TIMESTAMP""",
                (stage.id, stage.run_id, stage.name, stage.order, stage.status.value, self._json(stage)),
            )

    def get_stage(self, stage_id: str) -> StageRun | None:
        with self._session() as connection:
            row = connection.execute("SELECT data FROM stages WHERE id=?", (stage_id,)).fetchone()
        return self._load(row, StageRun)

    def list_stages(self, run_id: str) -> list[StageRun]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT data FROM stages WHERE run_id=? ORDER BY stage_order,id", (run_id,)
            ).fetchall()
        return [StageRun.model_validate_json(row["data"]) for row in rows]

    def save_artifact(self, artifact: Artifact) -> None:
        with self._session() as connection:
            connection.execute(
                """INSERT INTO artifacts(id,project_id,run_id,stage_id,kind,data,created_at)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,
                   run_id=excluded.run_id,stage_id=excluded.stage_id,kind=excluded.kind""",
                (
                    artifact.id,
                    artifact.project_id,
                    artifact.run_id,
                    artifact.stage_id,
                    artifact.kind.value,
                    self._json(artifact),
                    artifact.created_at.isoformat(),
                ),
            )

    def list_artifacts(self, project_id: str, *, run_id: str | None = None) -> list[Artifact]:
        query = "SELECT data FROM artifacts WHERE project_id=?"
        parameters: list[object] = [project_id]
        if run_id is not None:
            query += " AND run_id=?"
            parameters.append(run_id)
        query += " ORDER BY created_at,id"
        with self._session() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [Artifact.model_validate_json(row["data"]) for row in rows]

    def save_qa_report(self, report: QAReport) -> None:
        with self._session() as connection:
            connection.execute(
                """INSERT INTO qa_reports(id,project_id,run_id,data,created_at) VALUES(?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET data=excluded.data""",
                (report.id, report.project_id, report.run_id, self._json(report), report.created_at.isoformat()),
            )

    def get_latest_qa_report(self, run_id: str) -> QAReport | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT data FROM qa_reports WHERE run_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (run_id,)
            ).fetchone()
        return self._load(row, QAReport)

    def get_release(self, release_id: str) -> SubmissionRelease | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT data FROM submission_releases WHERE id=?",
                (release_id,),
            ).fetchone()
        return self._load(row, SubmissionRelease)

    def get_current_release(self, project_id: str) -> SubmissionRelease | None:
        with self._session() as connection:
            row = connection.execute(
                """SELECT data FROM submission_releases
                   WHERE project_id=? AND status='READY_TO_SUBMIT'
                   ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
        return self._load(row, SubmissionRelease)

    def supersede_current_release(self, project_id: str, *, reason: str) -> Project:
        if not reason.strip():
            raise ValueError("supersession reason must not be blank")
        with self.transaction() as connection:
            project = self._bump_project_content(
                connection,
                project_id,
                reason=reason,
            )
            if project is None:
                raise KeyError(project_id)
        return project

    def finalize_submission_release(
        self,
        release: SubmissionRelease,
        *,
        finished_at: datetime,
    ) -> GenerationRun:
        """Atomically validate the release scope, create READY and succeed its run."""

        with self.transaction() as connection:
            project = self._load(
                connection.execute(
                    "SELECT data FROM projects WHERE id=?", (release.project_id,)
                ).fetchone(),
                Project,
            )
            run = self._load(
                connection.execute(
                    "SELECT data FROM runs WHERE id=? AND project_id=?",
                    (release.run_id, release.project_id),
                ).fetchone(),
                GenerationRun,
            )
            manuscript = self._load(
                connection.execute(
                    """SELECT data FROM manuscripts WHERE project_id=?
                       ORDER BY revision DESC LIMIT 1""",
                    (release.project_id,),
                ).fetchone(),
                Manuscript,
            )
            requirements = self._load(
                connection.execute(
                    """SELECT data FROM requirements WHERE project_id=?
                       ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                    (release.project_id,),
                ).fetchone(),
                RequirementSet,
            )
            blueprint = self._load(
                connection.execute(
                    """SELECT data FROM blueprints WHERE project_id=?
                       ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                    (release.project_id,),
                ).fetchone(),
                ProjectBlueprint,
            )
            artifact = self._load(
                connection.execute(
                    "SELECT data FROM artifacts WHERE id=?", (release.docx_artifact_id,)
                ).fetchone(),
                Artifact,
            )
            report = self._load(
                connection.execute(
                    "SELECT data FROM qa_reports WHERE id=?", (release.qa_report_id,)
                ).fetchone(),
                QAReport,
            )
            if any(
                item is None
                for item in (project, run, manuscript, requirements, blueprint, artifact, report)
            ):
                raise ValueError("release scope is incomplete")
            assert project is not None
            assert run is not None
            assert manuscript is not None
            assert requirements is not None
            assert blueprint is not None
            assert artifact is not None
            assert report is not None
            if run.status not in {RunStatus.RUNNING, RunStatus.RETRYING}:
                raise ValueError("release run is not active")
            if project.content_revision != release.project_content_revision:
                raise ValueError("project content revision changed before release")
            if project.current_release_id is not None:
                raise ValueError("project already has a current release")
            if run.input_hash != release.input_hash:
                raise ValueError("release input hash is stale")
            if self._stable_hash(run.model_policy) != release.model_policy_hash:
                raise ValueError("release model policy hash is stale")
            if manuscript.id != release.manuscript_id or manuscript.revision != release.manuscript_revision:
                raise ValueError("release manuscript is stale")
            if self._stable_hash(manuscript.model_dump(mode="json")) != release.manuscript_hash:
                raise ValueError("release manuscript hash is stale")
            if requirements.revision != release.requirements_revision:
                raise ValueError("release requirements revision is stale")
            if blueprint.revision != release.blueprint_revision:
                raise ValueError("release blueprint revision is stale")
            if artifact.project_id != project.id or artifact.run_id != run.id:
                raise ValueError("release DOCX belongs to another scope")
            if artifact.kind.value != "docx" or artifact.sha256 != release.docx_hash:
                raise ValueError("release DOCX record is invalid")
            if (
                artifact.metadata.get("phase") != "final"
                or artifact.metadata.get("finalizer") != "libreoffice"
                or artifact.metadata.get("fields_updated") is not True
            ):
                raise ValueError("release DOCX was not finalized by LibreOffice")
            document_path = Path(artifact.path)
            if (
                not document_path.is_file()
                or document_path.stat().st_size != artifact.size_bytes
                or self._file_sha256(document_path) != release.docx_hash
            ):
                raise ValueError("release DOCX failed integrity verification")
            if report.project_id != project.id or report.run_id != run.id:
                raise ValueError("release QA belongs to another scope")
            if report.status.value != "pass" or any(
                not issue.resolved and issue.severity.value != "info"
                for issue in report.issues
            ):
                raise ValueError("release QA is not an exact PASS")
            if report.metadata.get("deterministic") is not True:
                raise ValueError("release QA is not a deterministic gate result")
            release_hashes = report.metadata.get("release_hashes")
            if not isinstance(release_hashes, dict) or any(
                release_hashes.get(key) != value
                for key, value in {
                    "input_hash": run.input_hash,
                    "manuscript_hash": release.manuscript_hash,
                    "docx_hash": release.docx_hash,
                }.items()
            ):
                raise ValueError("release QA hashes are stale")
            if (
                any(rule.mandatory for rule in requirements.rules)
                and report.requirement_coverage is None
            ):
                raise ValueError("mandatory requirements lack release coverage")
            if self._stable_hash(report.model_dump(mode="json")) != release.qa_scope_hash:
                raise ValueError("release QA scope hash is stale")
            for stage_name in ("consistency_qa", "final_gemini_review"):
                stage = self._load(
                    connection.execute(
                        "SELECT data FROM stages WHERE run_id=? AND name=?",
                        (run.id, stage_name),
                    ).fetchone(),
                    StageRun,
                )
                if stage is None or stage.status is not StageStatus.SUCCEEDED:
                    raise ValueError(f"{stage_name} did not succeed")
                if stage.checkpoint.get("accepted") is not True:
                    raise ValueError(f"{stage_name} was not explicitly accepted")
                if any(stage.checkpoint.get(key) for key in ("blocker_issues", "factual_issues")):
                    raise ValueError(f"{stage_name} contains release-blocking model issues")

            connection.execute(
                """INSERT INTO submission_releases(id,project_id,run_id,status,data,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (
                    release.id,
                    release.project_id,
                    release.run_id,
                    release.status.value,
                    self._json(release),
                    release.created_at.isoformat(),
                ),
            )
            project.current_release_id = release.id
            project.submission_status = SubmissionStatus.READY_TO_SUBMIT
            project.updated_at = finished_at
            connection.execute(
                "UPDATE projects SET data=?,updated_at=? WHERE id=?",
                (self._json(project), project.updated_at.isoformat(), project.id),
            )
            run.status = RunStatus.SUCCEEDED
            run.current_stage = None
            run.finished_at = finished_at
            run.error = None
            connection.execute(
                "UPDATE runs SET status=?,data=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (run.status.value, self._json(run), run.id),
            )
        return run

    def append_event(self, event: RunEvent) -> int:
        with self._session() as connection:
            cursor = connection.execute(
                """INSERT INTO run_events(id,run_id,stage_id,event_type,data,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (
                    event.id,
                    event.run_id,
                    event.stage_id,
                    event.event_type,
                    self._json(event),
                    event.created_at.isoformat(),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return an event sequence")
            return int(cursor.lastrowid)

    def list_events(self, run_id: str, *, after_sequence: int = 0) -> list[tuple[int, RunEvent]]:
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        with self._session() as connection:
            rows = connection.execute(
                """SELECT sequence,data FROM run_events WHERE run_id=? AND sequence>?
                   ORDER BY sequence""",
                (run_id, after_sequence),
            ).fetchall()
        return [(int(row["sequence"]), RunEvent.model_validate_json(row["data"])) for row in rows]

    def get_worker_request(self, request_id: str) -> tuple[str, str, str | None] | None:
        """Return ``(project_id, fingerprint, outcome_json)`` for protocol replay."""

        with self._session() as connection:
            row = connection.execute(
                "SELECT project_id,fingerprint,outcome FROM worker_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["project_id"]), str(row["fingerprint"]), (
            str(row["outcome"]) if row["outcome"] is not None else None
        )

    def record_worker_request(self, request_id: str, project_id: str, fingerprint: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO worker_requests(request_id,project_id,fingerprint,created_at)
                   VALUES(?,?,?,CURRENT_TIMESTAMP)""",
                (request_id, project_id, fingerprint),
            )

    def complete_worker_request(self, request_id: str, outcome_json: str) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE worker_requests SET outcome=?,completed_at=CURRENT_TIMESTAMP
                   WHERE request_id=? AND outcome IS NULL""",
                (outcome_json, request_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("worker request is missing or already completed")

    def _save_object(self, kind: str, project_id: str, object_id: str, parent_id: str | None, model: BaseModel) -> None:
        with self._session() as connection:
            connection.execute(
                """INSERT INTO domain_objects(kind,id,project_id,parent_id,data,updated_at)
                   VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(kind,id) DO UPDATE SET parent_id=excluded.parent_id,data=excluded.data,
                   updated_at=CURRENT_TIMESTAMP""",
                (kind, object_id, project_id, parent_id, self._json(model)),
            )

    def _list_objects(self, kind: str, project_id: str, model_type: type[TModel]) -> list[TModel]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT data FROM domain_objects WHERE kind=? AND project_id=? ORDER BY updated_at,id",
                (kind, project_id),
            ).fetchall()
        return [model_type.model_validate_json(row["data"]) for row in rows]

    def save_claim(self, claim: Claim) -> None:
        self._save_object("claim", claim.project_id, claim.id, claim.section_id, claim)

    def list_claims(self, project_id: str) -> list[Claim]:
        return self._list_objects("claim", project_id, Claim)

    def save_evidence(self, project_id: str, evidence: Evidence) -> None:
        self._save_object("evidence", project_id, evidence.id, evidence.claim_id, evidence)

    def list_evidence(self, project_id: str) -> list[Evidence]:
        return self._list_objects("evidence", project_id, Evidence)

    def save_source_snapshot(self, snapshot: SourceSnapshot) -> None:
        """Persist an immutable capture; an ID can never be repointed or rewritten."""

        with self.transaction() as connection:
            row = connection.execute(
                "SELECT data FROM domain_objects WHERE kind='source_snapshot' AND id=?",
                (snapshot.id,),
            ).fetchone()
            if row is not None:
                existing = SourceSnapshot.model_validate_json(row["data"])
                if existing != snapshot:
                    raise ValueError("SourceSnapshot is immutable and cannot be replaced")
                return
            connection.execute(
                """INSERT INTO domain_objects(kind,id,project_id,parent_id,data,updated_at)
                   VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)""",
                (
                    "source_snapshot",
                    snapshot.id,
                    snapshot.project_id,
                    snapshot.source_id,
                    self._json(snapshot),
                ),
            )

    def list_source_snapshots(self, project_id: str) -> list[SourceSnapshot]:
        return self._list_objects("source_snapshot", project_id, SourceSnapshot)

    def save_bibliography_entry(self, project_id: str, entry: BibliographyEntry) -> None:
        self._save_object("bibliography", project_id, entry.id, entry.source_id, entry)

    def replace_bibliography_entries(
        self, project_id: str, entries: list[BibliographyEntry]
    ) -> None:
        """Publish a canonical bibliography so duplicates cannot inflate source minimums."""

        unique_ids = [entry.id for entry in entries]
        if len(unique_ids) != len(set(unique_ids)):
            raise ValueError("bibliography entry IDs must be unique")
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM domain_objects WHERE kind='bibliography' AND project_id=?",
                (project_id,),
            )
            for entry in entries:
                connection.execute(
                    """INSERT INTO domain_objects(kind,id,project_id,parent_id,data,updated_at)
                       VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)""",
                    (
                        "bibliography",
                        entry.id,
                        project_id,
                        entry.source_id,
                        self._json(entry),
                    ),
                )

    def list_bibliography(self, project_id: str) -> list[BibliographyEntry]:
        return self._list_objects("bibliography", project_id, BibliographyEntry)

    def save_citation(self, project_id: str, citation: Citation) -> None:
        """Persist a citation marker independently from manuscript revisions."""

        self._save_object("citation", project_id, citation.id, citation.claim_id, citation)

    def replace_citations_and_save_manuscript(
        self, manuscript: Manuscript, citations: list[Citation]
    ) -> None:
        """Atomically publish a complete derived citation graph and manuscript.

        Citation audit is an all-or-nothing rebuild: a validation failure or a
        process error must leave the prior citations and manuscript untouched.
        """

        project_id = manuscript.project_id
        with self.transaction() as connection:
            self._bump_project_content(
                connection,
                project_id,
                reason="manuscript_changed",
            )
            connection.execute(
                "DELETE FROM domain_objects WHERE kind='citation' AND project_id=?",
                (project_id,),
            )
            for citation in citations:
                connection.execute(
                    """INSERT INTO domain_objects(kind,id,project_id,parent_id,data,updated_at)
                       VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
                       ON CONFLICT(kind,id) DO UPDATE SET parent_id=excluded.parent_id,data=excluded.data,
                       updated_at=CURRENT_TIMESTAMP""",
                    (
                        "citation",
                        citation.id,
                        project_id,
                        citation.claim_id,
                        self._json(citation),
                    ),
                )
            self._save_manuscript(connection, manuscript)

    def list_citations(self, project_id: str) -> list[Citation]:
        return self._list_objects("citation", project_id, Citation)

    def delete_citations(self, project_id: str) -> None:
        """Clear derived citation markers before an idempotent audit rebuild."""

        with self._session() as connection:
            connection.execute(
                "DELETE FROM domain_objects WHERE kind='citation' AND project_id=?",
                (project_id,),
            )

    def clear_research_data(self, project_id: str, *, include_claims: bool = False) -> None:
        """Atomically remove pipeline-derived research before a stage rebuild.

        User inputs are preserved.  Only sources explicitly marked as generated
        are removed; their fragments cascade with the source row.
        """

        kinds = ["citation", "evidence", "bibliography", "source_snapshot"]
        if include_claims:
            kinds.append("claim")
        placeholders = ",".join("?" for _ in kinds)
        with self.transaction() as connection:
            connection.execute(
                f"DELETE FROM domain_objects WHERE project_id=? AND kind IN ({placeholders})",
                (project_id, *kinds),
            )
            rows = connection.execute(
                "SELECT id,data FROM sources WHERE project_id=? AND role='reference'",
                (project_id,),
            ).fetchall()
            generated_ids = [
                str(row["id"])
                for row in rows
                if Source.model_validate_json(row["data"]).metadata.get("generated") is True
            ]
            connection.executemany(
                "DELETE FROM sources WHERE id=?",
                ((source_id,) for source_id in generated_ids),
            )

    def clear_calculation_data(self, project_id: str) -> None:
        """Clear reproducible facts/datasets/calculations before rebuilding them."""

        with self._session() as connection:
            connection.execute(
                "DELETE FROM domain_objects WHERE project_id=? AND kind IN ('fact','dataset','calculation')",
                (project_id,),
            )

    def save_fact(self, fact: FactRecord) -> None:
        self._save_object("fact", fact.project_id, fact.id, fact.source_id, fact)

    def list_facts(self, project_id: str) -> list[FactRecord]:
        return self._list_objects("fact", project_id, FactRecord)

    def save_dataset(self, dataset: Dataset) -> None:
        self._save_object("dataset", dataset.project_id, dataset.id, None, dataset)

    def list_datasets(self, project_id: str) -> list[Dataset]:
        return self._list_objects("dataset", project_id, Dataset)

    def save_calculation(self, calculation: Calculation) -> None:
        self._save_object("calculation", calculation.project_id, calculation.id, None, calculation)

    def list_calculations(self, project_id: str) -> list[Calculation]:
        return self._list_objects("calculation", project_id, Calculation)

    def save_remote_resource(self, resource: RemoteResource) -> None:
        self._save_object("remote_resource", resource.project_id, resource.id, resource.run_id, resource)

    def list_remote_resources(self, run_id: str) -> list[RemoteResource]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT data FROM domain_objects WHERE kind='remote_resource' AND parent_id=? ORDER BY updated_at,id",
                (run_id,),
            ).fetchall()
        return [RemoteResource.model_validate_json(row["data"]) for row in rows]

    def backup_to(self, destination: str | os.PathLike[str]) -> Path:
        """Create a consistent SQLite backup and atomically publish it."""

        target = Path(destination).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        temporary.unlink(missing_ok=True)
        try:
            source = self._connect()
            backup = sqlite3.connect(temporary)
            try:
                source.backup(backup)
                backup.commit()
            finally:
                backup.close()
                source.close()
            os.replace(temporary, target)
            return target
        finally:
            temporary.unlink(missing_ok=True)

    @property
    def schema_version(self) -> int:
        with self._session() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def integrity_check(self) -> tuple[bool, list[str]]:
        with self._session() as connection:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
        messages = [str(row[0]) for row in rows]
        return messages == ["ok"], messages

    def save_backup_record(self, record: BackupRecord) -> None:
        with self._session() as connection:
            connection.execute(
                """INSERT INTO backup_records(id,project_id,data,created_at) VALUES(?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET data=excluded.data""",
                (record.id, record.project_id, self._json(record), record.created_at.isoformat()),
            )

    def list_backup_records(self, project_id: str) -> list[BackupRecord]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT data FROM backup_records WHERE project_id=? ORDER BY created_at DESC,id DESC",
                (project_id,),
            ).fetchall()
        return [BackupRecord.model_validate_json(row["data"]) for row in rows]

    def delete_backup_record(self, record_id: str) -> None:
        with self._session() as connection:
            connection.execute("DELETE FROM backup_records WHERE id=?", (record_id,))

    def save_migration_record(self, record: MigrationRecord) -> None:
        with self._session() as connection:
            connection.execute(
                """INSERT INTO migration_records(id,project_id,data,applied_at) VALUES(?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET data=excluded.data""",
                (record.id, record.project_id, self._json(record), record.applied_at.isoformat()),
            )

    def save_revision(self, record: RevisionRecord) -> None:
        with self._session() as connection:
            connection.execute(
                """INSERT INTO revisions(id,project_id,kind,revision,data,created_at) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(project_id,kind,revision) DO UPDATE SET id=excluded.id,data=excluded.data,
                   created_at=excluded.created_at""",
                (record.id, record.project_id, record.kind, record.revision, self._json(record), record.created_at.isoformat()),
            )

    def list_revisions(self, project_id: str, kind: str | None = None) -> list[RevisionRecord]:
        query = "SELECT data FROM revisions WHERE project_id=?"
        values: list[str] = [project_id]
        if kind is not None:
            query += " AND kind=?"
            values.append(kind)
        query += " ORDER BY kind,revision DESC"
        with self._session() as connection:
            rows = connection.execute(query, values).fetchall()
        return [RevisionRecord.model_validate_json(row["data"]) for row in rows]

    def record_revision(self, project_id: str, kind: str, object_id: str, payload: str) -> RevisionRecord:
        if kind not in {"requirements", "blueprint", "manuscript", "datasets", "qa"}:
            raise ValueError(f"unsupported revision kind: {kind}")
        existing = self.list_revisions(project_id, kind)
        record = RevisionRecord(
            project_id=project_id,
            kind=cast(Literal["requirements", "blueprint", "manuscript", "datasets", "qa"], kind),
            revision=(existing[0].revision + 1) if existing else 1,
            object_id=object_id,
            sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )
        self.save_revision(record)
        return record


# More explicit name for dependency-injection declarations.
SQLiteProjectRepository = SQLiteRepository
