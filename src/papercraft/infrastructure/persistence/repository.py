"""Durable SQLite repository for projects and resumable pipeline state."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
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
    RemoteResource,
    RequirementSet,
    RevisionRecord,
    RunEvent,
    Source,
    SourceFragment,
    StageRun,
)

TModel = TypeVar("TModel", bound=BaseModel)


_SCHEMA_VERSION = 2
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
            # All schema additions are additive.  A dedicated MigrationService
            # creates a backup before upgrading an existing project; this
            # bootstrap keeps a freshly-created project at the current version.
            connection.executescript(_SCHEMA)
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

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
        return model.model_dump_json()

    @staticmethod
    def _load(row: sqlite3.Row | None, model_type: type[TModel]) -> TModel | None:
        return None if row is None else model_type.model_validate_json(row["data"])

    def save_project(self, project: Project) -> None:
        with self._session() as connection:
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
        with self._session() as connection:
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
        with self._session() as connection:
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
        with self._session() as connection:
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

    def save_run(self, run: GenerationRun) -> None:
        with self._session() as connection:
            connection.execute(
                """INSERT INTO runs(id,project_id,status,data,updated_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,data=excluded.data,
                   updated_at=CURRENT_TIMESTAMP""",
                (run.id, run.project_id, run.status.value, self._json(run)),
            )

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

    def save_bibliography_entry(self, project_id: str, entry: BibliographyEntry) -> None:
        self._save_object("bibliography", project_id, entry.id, entry.source_id, entry)

    def list_bibliography(self, project_id: str) -> list[BibliographyEntry]:
        return self._list_objects("bibliography", project_id, BibliographyEntry)

    def save_citation(self, project_id: str, citation: Citation) -> None:
        """Persist a citation marker independently from manuscript revisions."""

        self._save_object("citation", project_id, citation.id, citation.claim_id, citation)

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

        kinds = ["citation", "evidence", "bibliography"]
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
