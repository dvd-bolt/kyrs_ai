"""Document queries and targeted rebuild/export use cases."""

from __future__ import annotations

import hmac
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from papercraft.domain import (
    Artifact,
    ArtifactKind,
    GenerationRun,
    ReleaseStatus,
    RunStatus,
)
from papercraft.infrastructure.persistence import sha256_file

from .autopilot import AutopilotService, PipelineStage
from .ports import RepositoryPort
from .release import stable_hash


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
        return self._validated_path(self._latest_artifact(kind, run_id))

    def export_block_reason(self, kind: ArtifactKind, run_id: str | None = None) -> str | None:
        """Return the release-QA reason that currently prevents an export."""

        try:
            artifact = self._latest_artifact(kind, run_id)
            self._assert_export_allowed(artifact)
        except DocumentExportBlocked as error:
            return str(error)
        return None

    def _latest_artifact(self, kind: ArtifactKind, run_id: str | None = None) -> Artifact:
        candidates = [artifact for artifact in self.artifacts(run_id) if artifact.kind == kind]
        if not candidates:
            raise FileNotFoundError(f"No {kind.value} artifact is available")
        return max(candidates, key=lambda item: item.created_at)

    def _assert_export_allowed(self, artifact: Artifact) -> None:
        """Require a matching passing release-QA report for final documents.

        DOCX/PDF artifacts intentionally exist before Package so that QA can
        inspect them.  They are not user-exportable until Package records a
        passing report for this exact manuscript, blueprint and artifact.
        """

        if artifact.kind not in {ArtifactKind.DOCX, ArtifactKind.PDF}:
            return
        if artifact.kind is ArtifactKind.PDF:
            raise DocumentExportBlocked("Only the released DOCX is user-exportable.")
        if not artifact.run_id:
            raise DocumentExportBlocked("Export is blocked until release QA is completed.")
        project = self.repository.get_project(self.project_id)
        release = self.repository.get_current_release(self.project_id)
        if (
            project is None
            or release is None
            or release.status is not ReleaseStatus.READY_TO_SUBMIT
            or project.current_release_id != release.id
            or project.content_revision != release.project_content_revision
        ):
            raise DocumentExportBlocked("Export is blocked until the current revision is READY_TO_SUBMIT.")
        if release.docx_artifact_id != artifact.id or release.run_id != artifact.run_id:
            raise DocumentExportBlocked("Export is blocked until this exact DOCX passes release QA.")
        run = self.repository.get_run(artifact.run_id)
        if (
            run is None
            or run.status is not RunStatus.SUCCEEDED
            or run.input_hash != release.input_hash
            or stable_hash(run.model_policy) != release.model_policy_hash
        ):
            raise DocumentExportBlocked("Export is blocked until the release run succeeds.")
        manuscript = self.repository.get_latest_manuscript(self.project_id)
        if (
            manuscript is None
            or manuscript.id != release.manuscript_id
            or manuscript.revision != release.manuscript_revision
            or stable_hash(manuscript.model_dump(mode="json")) != release.manuscript_hash
        ):
            raise DocumentExportBlocked("Export is blocked until the edited manuscript passes release QA.")
        if artifact.sha256 != release.docx_hash:
            raise DocumentExportBlocked("Export is blocked because the released DOCX scope is stale.")
        blueprint = self.repository.get_latest_blueprint(self.project_id)
        if blueprint is None or blueprint.revision != release.blueprint_revision:
            raise DocumentExportBlocked("Export is blocked until the edited plan passes release QA.")
        requirements = self.repository.get_latest_requirement_set(self.project_id)
        if requirements is None or requirements.revision != release.requirements_revision:
            raise DocumentExportBlocked(
                "Export is blocked until the current requirements pass release QA."
            )

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
        # A dependent section receives predecessor conclusions in its writer
        # context, so rebuilding an upstream section must also rebuild the
        # downstream closure rather than leaving it internally stale.
        target_ids = {section_id}
        changed = True
        while changed:
            changed = False
            for section in blueprint.outline.sections:
                if section.id not in target_ids and set(section.depends_on) & target_ids:
                    target_ids.add(section.id)
                    changed = True
        run.metadata["rebuild_section_ids"] = [
            section.id
            for section in sorted(blueprint.outline.sections, key=lambda item: item.order)
            if section.id in target_ids
        ]
        # A rebuild commonly reuses the original run ID. This token separates
        # new target drafts from its old artifacts, yet remains stable if the
        # rebuild is paused and resumed.
        run.metadata["rebuild_section_token"] = uuid4().hex
        self.repository.save_run_preserving_control(run)
        return autopilot.retry_from(run_id, PipelineStage.GENERATE_SECTIONS)

    def export(self, kind: ArtifactKind, destination: str | Path, run_id: str | None = None) -> Path:
        artifact = self._latest_artifact(kind, run_id)
        self._assert_export_allowed(artifact)
        source = self._validated_path(artifact)
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
        artifact = self._latest_artifact(ArtifactKind.DOCX, run_id)
        self._assert_export_allowed(artifact)
        path = self._validated_path(artifact)
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


class DocumentExportBlocked(RuntimeError):
    """Raised when a final document has not passed release QA."""


__all__ = ["DocumentExportBlocked", "DocumentService"]
