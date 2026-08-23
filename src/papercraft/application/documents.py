"""Document queries and targeted rebuild/export use cases."""

from __future__ import annotations

import hmac
import os
import shutil
import subprocess
from pathlib import Path

from papercraft.domain import Artifact, ArtifactKind, GenerationRun
from papercraft.infrastructure.persistence import sha256_file

from .autopilot import AutopilotService, PipelineStage
from .ports import RepositoryPort


class DocumentService:
    def __init__(self, project_id: str, repository: RepositoryPort) -> None:
        self.project_id = project_id
        self.repository = repository

    def artifacts(self, run_id: str | None = None) -> list[Artifact]:
        return self.repository.list_artifacts(self.project_id, run_id=run_id)

    def preview(self, run_id: str) -> list[Path]:
        pages = [
            self._validated_path(artifact)
            for artifact in self.artifacts(run_id)
            if artifact.kind == ArtifactKind.PAGE_PREVIEW
        ]
        return sorted(pages)

    def latest(self, kind: ArtifactKind, run_id: str | None = None) -> Path:
        candidates = [artifact for artifact in self.artifacts(run_id) if artifact.kind == kind]
        if not candidates:
            raise FileNotFoundError(f"No {kind.value} artifact is available")
        artifact = max(candidates, key=lambda item: item.created_at)
        return self._validated_path(artifact)

    @staticmethod
    def _validated_path(artifact: Artifact) -> Path:
        expected_suffixes = {
            ArtifactKind.DOCX: {".docx"},
            ArtifactKind.PDF: {".pdf"},
            ArtifactKind.PAGE_PREVIEW: {".png", ".jpg", ".jpeg"},
            ArtifactKind.QA_JSON: {".json"},
            ArtifactKind.QA_HTML: {".html", ".htm"},
        }
        path = Path(artifact.path)
        allowed = expected_suffixes.get(artifact.kind)
        if allowed is not None and path.suffix.casefold() not in allowed:
            raise ValueError(f"Artifact extension does not match {artifact.kind.value}")
        if not path.is_file():
            raise FileNotFoundError(path)
        try:
            actual_size = path.stat().st_size
            actual_hash = sha256_file(path)
        except OSError as error:
            raise FileNotFoundError(path) from error
        if actual_size != artifact.size_bytes or not hmac.compare_digest(actual_hash, artifact.sha256):
            raise OSError(f"Artifact failed integrity verification: {artifact.id}")
        return path

    def rebuild_section(
        self,
        autopilot: AutopilotService,
        run_id: str,
        section_id: str,
    ) -> GenerationRun:
        blueprint = self.repository.get_latest_blueprint(self.project_id)
        if blueprint is None or section_id not in {item.id for item in blueprint.outline.sections}:
            raise KeyError(f"Unknown section: {section_id}")
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        run.metadata["rebuild_section_ids"] = [section_id]
        self.repository.save_run(run)
        return autopilot.retry_from(run_id, PipelineStage.GENERATE_SECTIONS)

    def export(self, kind: ArtifactKind, destination: str | Path, run_id: str | None = None) -> Path:
        source = self.latest(kind, run_id)
        target = Path(destination).expanduser().resolve()
        if target.is_dir():
            target = target / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".partial")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def open_in_word(self, run_id: str | None = None) -> None:
        path = self.latest(ArtifactKind.DOCX, run_id)
        if os.name == "nt":
            os.startfile(path)
            return
        completed = subprocess.run(
            ["xdg-open", str(path)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Unable to open {path}")


__all__ = ["DocumentService"]
