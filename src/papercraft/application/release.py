"""Release-policy construction kept separate from artifact generation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from papercraft.domain import (
    Artifact,
    ArtifactKind,
    GenerationRun,
    Manuscript,
    Project,
    ProjectBlueprint,
    QAReport,
    QASeverity,
    QAStatus,
    RequirementSet,
    SubmissionRelease,
)
from papercraft.profiles import WorkProfile


class ReleasePolicyError(RuntimeError):
    """Raised when an exact release candidate does not satisfy policy 1."""


def stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_submission_release(
    *,
    project: Project,
    run: GenerationRun,
    manuscript: Manuscript,
    docx_artifact: Artifact,
    report: QAReport,
    requirements: RequirementSet,
    blueprint: ProjectBlueprint,
    profile: WorkProfile,
) -> SubmissionRelease:
    if report.status is not QAStatus.PASS:
        raise ReleasePolicyError("Release QA must be PASS")
    if report.metadata.get("deterministic") is not True:
        raise ReleasePolicyError("Release QA must come from the deterministic gate")
    if any(
        not issue.resolved
        and issue.severity
        in {
            QASeverity.WARNING,
            QASeverity.ERROR,
            QASeverity.CRITICAL,
            QASeverity.BLOCKER,
        }
        for issue in report.issues
    ):
        raise ReleasePolicyError("Release QA contains unresolved warning-or-higher issues")
    if docx_artifact.kind is not ArtifactKind.DOCX:
        raise ReleasePolicyError("Release artifact is not a DOCX")
    if docx_artifact.metadata.get("phase") != "final":
        raise ReleasePolicyError("Release artifact is not the finalized DOCX")
    if (
        docx_artifact.metadata.get("finalizer") != "libreoffice"
        or docx_artifact.metadata.get("fields_updated") is not True
    ):
        raise ReleasePolicyError("Release DOCX was not finalized by LibreOffice")
    if docx_artifact.project_id != project.id or docx_artifact.run_id != run.id:
        raise ReleasePolicyError("Release DOCX does not belong to the current run")
    if manuscript.project_id != project.id or report.project_id != project.id:
        raise ReleasePolicyError("Release inputs do not belong to the current project")
    if report.run_id != run.id:
        raise ReleasePolicyError("Release QA does not belong to the current run")
    if any(rule.mandatory for rule in requirements.rules) and report.requirement_coverage is None:
        raise ReleasePolicyError("Mandatory requirements lack release coverage")
    docx_path = Path(docx_artifact.path)
    if not docx_path.is_file():
        raise ReleasePolicyError("Release DOCX is missing")
    actual_docx_hash = hashlib.sha256(docx_path.read_bytes()).hexdigest()
    if actual_docx_hash != docx_artifact.sha256:
        raise ReleasePolicyError("Release DOCX hash does not match its artifact")
    manuscript_hash = stable_hash(manuscript.model_dump(mode="json"))
    release_hashes = report.metadata.get("release_hashes")
    if not isinstance(release_hashes, dict):
        raise ReleasePolicyError("Release QA lacks exact scope hashes")
    expected_hashes = {
        "input_hash": run.input_hash,
        "manuscript_hash": manuscript_hash,
        "docx_hash": docx_artifact.sha256,
    }
    if any(release_hashes.get(key) != value for key, value in expected_hashes.items()):
        raise ReleasePolicyError("Release QA hashes do not match the current candidate")

    return SubmissionRelease(
        project_id=project.id,
        run_id=run.id,
        manuscript_id=manuscript.id,
        manuscript_revision=manuscript.revision,
        docx_artifact_id=docx_artifact.id,
        qa_report_id=report.id,
        project_content_revision=project.content_revision,
        input_hash=run.input_hash,
        requirements_revision=requirements.revision,
        blueprint_revision=blueprint.revision,
        profile_id=profile.id,
        profile_version=profile.version,
        model_policy_hash=stable_hash(run.model_policy),
        manuscript_hash=manuscript_hash,
        docx_hash=docx_artifact.sha256,
        qa_scope_hash=stable_hash(report.model_dump(mode="json")),
    )


__all__ = ["ReleasePolicyError", "build_submission_release", "stable_hash"]
