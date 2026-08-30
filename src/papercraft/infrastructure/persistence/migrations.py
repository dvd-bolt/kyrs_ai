"""Additive SQLite schema migration planning for local PaperCraft projects."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from papercraft.domain import BackupRecord, MigrationPlan, MigrationRecord, MigrationResult

from .repository import _SCHEMA, _SCHEMA_VERSION, SQLiteRepository
from .storage import sha256_file


class MigrationService:
    """Plans and applies only forward, transactional schema migrations.

    Existing project files are backed up before a migration.  The current
    migration is additive, therefore applying the schema script inside an
    immediate transaction has an all-or-nothing rollback boundary.
    """

    def __init__(self, repository: SQLiteRepository, *, backups_dir: Path | None = None) -> None:
        self.repository = repository
        self.backups_dir = backups_dir or repository.database.parent / "backups"

    def plan(self, from_version: int, to_version: int = _SCHEMA_VERSION) -> MigrationPlan:
        if from_version < 1 or to_version < from_version or to_version > _SCHEMA_VERSION:
            raise ValueError("unsupported migration range")
        steps: list[str] = []
        if from_version < 2 <= to_version:
            steps.append("add revision, backup and migration metadata tables")
        if from_version < 3 <= to_version:
            steps.append("add append-only section revision payload history")
        if from_version < 4 <= to_version:
            steps.append("add append-only plan revision payload history")
        return MigrationPlan(from_version=from_version, to_version=to_version, steps=steps)

    def apply(self, plan: MigrationPlan, *, project_id: str | None = None) -> MigrationResult:
        current = self.repository.schema_version
        if current != plan.from_version:
            raise RuntimeError(f"expected schema {plan.from_version}, found {current}")
        if not plan.steps:
            return MigrationResult(plan=plan, applied=True)

        self.backups_dir.mkdir(parents=True, exist_ok=True)
        target = self.backups_dir / f"pre_migration_v{current}_to_v{plan.to_version}.db"
        backup_path = self.repository.backup_to(target)
        backup: BackupRecord | None = None
        if project_id is not None:
            backup = BackupRecord(
                project_id=project_id,
                path=str(backup_path),
                sha256=sha256_file(backup_path),
                size_bytes=backup_path.stat().st_size,
                label=f"before schema migration {current}->{plan.to_version}",
            )
            self.repository.save_backup_record(backup)

        connection = sqlite3.connect(self.repository.database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.executescript(_SCHEMA)
            connection.execute(f"PRAGMA user_version = {plan.to_version}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        record = MigrationRecord(
            project_id=project_id,
            from_version=plan.from_version,
            to_version=plan.to_version,
            backup_id=backup.id if backup else None,
        )
        self.repository.save_migration_record(record)
        return MigrationResult(plan=plan, applied=True, records=[record], backup=backup)

    @staticmethod
    def restore_database(backup: BackupRecord, database: Path) -> None:
        """Restore a verified backup atomically after caller authorization."""

        source = Path(backup.path)
        if not source.is_file() or sha256_file(source) != backup.sha256:
            raise ValueError("backup is missing or its SHA-256 does not match")
        temporary = database.with_name(f".{database.name}.restore.tmp")
        temporary.unlink(missing_ok=True)
        source_connection = sqlite3.connect(source)
        target_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(target_connection)
            target_connection.commit()
        finally:
            target_connection.close()
            source_connection.close()
        os.replace(temporary, database)
