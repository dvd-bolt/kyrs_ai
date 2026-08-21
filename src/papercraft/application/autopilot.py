from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import JsonValue

from papercraft.config import AppSettings
from papercraft.domain import (
    Artifact,
    GenerationRun,
    Project,
    QAIssue,
    RunEvent,
    RunStatus,
    StageRun,
    StageStatus,
)
from papercraft.infrastructure.persistence import AtomicArtifactStore, ProjectPaths

from .ports import RepositoryPort


class PipelineStage(StrEnum):
    PREFLIGHT = "preflight"
    INGEST = "ingest"
    EXTRACT_REQUIREMENTS = "extract_requirements"
    BUILD_EVIDENCE_INDEX = "build_evidence_index"
    VERIFIED_RESEARCH = "verified_research"
    PLAN = "plan"
    BUILD_FACTS_AND_DATASETS = "build_facts_and_datasets"
    GENERATE_SECTIONS = "generate_sections"
    GENERATE_VISUALS = "generate_visuals"
    CITATION_AUDIT = "citation_audit"
    CONSISTENCY_QA = "consistency_qa"
    RENDER_DOCX = "render_docx"
    WORD_FINALIZE = "word_finalize"
    EXPORT_PDF = "export_pdf"
    PDF_VISUAL_QA = "pdf_visual_qa"
    FINAL_GEMINI_REVIEW = "final_gemini_review"
    PACKAGE = "package"


PIPELINE_ORDER = tuple(PipelineStage)


@dataclass(slots=True)
class StageOutcome:
    artifacts: list[Artifact] = field(default_factory=list)
    checkpoint: dict[str, JsonValue] = field(default_factory=dict)
    skipped: bool = False
    message: str = ""


@dataclass(frozen=True, slots=True)
class StageContext:
    settings: AppSettings
    project: Project
    run: GenerationRun
    stage: StageRun
    paths: ProjectPaths
    repository: RepositoryPort
    artifact_store: AtomicArtifactStore


class StageHandler(Protocol):
    def __call__(self, context: StageContext) -> StageOutcome: ...


class MissingStageHandler(RuntimeError):
    pass


class AutopilotService:
    """Durable, idempotent orchestration for the PaperCraft pipeline.

    The service is synchronous by design; the desktop starts it in the worker
    process.  Every boundary is persisted, so a killed worker resumes without
    repeating successful stages.
    """

    def __init__(
        self,
        settings: AppSettings,
        project: Project,
        repository: RepositoryPort,
        paths: ProjectPaths,
        handlers: Mapping[PipelineStage, StageHandler],
        terminal_hook: Callable[[GenerationRun], None] | None = None,
    ) -> None:
        self.settings = settings
        self.project = project
        self.repository = repository
        self.paths = paths
        self.handlers = dict(handlers)
        self.terminal_hook = terminal_hook
        self.artifact_store = AtomicArtifactStore(paths.artifacts)

    def _input_hash(self) -> str:
        # Verified web sources are produced by the pipeline itself and must not
        # invalidate the user's input hash during resume/retry.
        sources = [
            source
            for source in self.repository.list_sources(self.project.id)
            if source.role.value != "reference" and not source.metadata.get("generated")
        ]
        value = {
            "project": self.project.model_dump(mode="json"),
            "source_hashes": sorted(source.sha256 for source in sources),
            "pipeline": "2",
        }
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _event(self, run: GenerationRun, stage: StageRun | None, event_type: str, message: str) -> None:
        self.repository.append_event(
            RunEvent(
                run_id=run.id,
                stage_id=stage.id if stage else None,
                event_type=event_type,
                message=message,
            )
        )

    def create_run(self) -> GenerationRun:
        active = [
            run
            for run in self.repository.list_runs(self.project.id)
            if run.status in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.RETRYING, RunStatus.PAUSED, RunStatus.WAITING_INPUT}
        ]
        if active:
            raise RuntimeError("This project already has an unfinished autopilot run")
        run = GenerationRun(
            project_id=self.project.id,
            input_hash=self._input_hash(),
            pipeline_version="2",
            model_policy=self.settings.model_policy.model_dump(mode="json"),
        )
        self.repository.save_run(run)
        for order, name in enumerate(PIPELINE_ORDER):
            self.repository.save_stage(
                StageRun(run_id=run.id, name=name.value, order=order, input_hash=run.input_hash)
            )
        self._event(run, None, "run_created", "Autopilot run created")
        return run

    def start(self) -> GenerationRun:
        run = self.create_run()
        return self.execute(run.id)

    def execute(self, run_id: str) -> GenerationRun:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown run: {run_id}")
        if run.status in {RunStatus.SUCCEEDED, RunStatus.CANCELLED}:
            return run
        if run.input_hash != self._input_hash():
            raise RuntimeError("Project inputs changed; use retry_from to invalidate dependent stages")

        now = datetime.now(UTC)
        run.status = RunStatus.RUNNING
        run.started_at = run.started_at or now
        run.error = None
        self.repository.save_run(run)
        self._event(run, None, "run_started", "Autopilot execution started")

        for stage in self.repository.list_stages(run.id):
            run = self.repository.get_run(run.id) or run
            if run.status == RunStatus.CANCELLED:
                self._event(run, stage, "run_cancelled", "Autopilot cancelled")
                self._terminal(run)
                return run
            if run.status == RunStatus.PAUSED:
                return run
            if stage.status in {StageStatus.SUCCEEDED, StageStatus.SKIPPED}:
                continue
            stage_name = PipelineStage(stage.name)
            handler = self.handlers.get(stage_name)
            if handler is None:
                return self._fail(run, stage, MissingStageHandler(f"No handler for {stage.name}"))

            run.current_stage = stage.name
            stage.status = StageStatus.RUNNING
            stage.started_at = stage.started_at or datetime.now(UTC)
            stage.attempts += 1
            self.repository.save_run(run)
            self.repository.save_stage(stage)
            self._event(run, stage, "stage_started", f"Started: {stage.name}")
            try:
                outcome = handler(
                    StageContext(
                        settings=self.settings,
                        project=self.project,
                        run=run,
                        stage=stage,
                        paths=self.paths,
                        repository=self.repository,
                        artifact_store=self.artifact_store,
                    )
                )
                for artifact in outcome.artifacts:
                    self.repository.save_artifact(artifact)
                    stage.output_artifact_ids.append(artifact.id)
                stage.checkpoint = dict(outcome.checkpoint)
                stage.status = StageStatus.SKIPPED if outcome.skipped else StageStatus.SUCCEEDED
                stage.finished_at = datetime.now(UTC)
                stage.error = None
                self.repository.save_stage(stage)
                self._event(run, stage, "stage_completed", outcome.message or f"Completed: {stage.name}")
            except Exception as exc:
                return self._fail(run, stage, exc)

            if self._checkpoint_required(stage_name) and not self._checkpoint_acknowledged(run, stage_name):
                run.status = RunStatus.WAITING_INPUT
                self.repository.save_run(run)
                self._event(run, stage, "checkpoint_waiting", f"Approval required after {stage.name}")
                return run

        run.status = RunStatus.SUCCEEDED
        run.current_stage = None
        run.finished_at = datetime.now(UTC)
        self.repository.save_run(run)
        self._event(run, None, "run_succeeded", "Autopilot completed successfully")
        self._terminal(run)
        return run

    def _fail(self, run: GenerationRun, stage: StageRun, error: Exception) -> GenerationRun:
        stage.status = StageStatus.FAILED
        stage.finished_at = datetime.now(UTC)
        stage.error = str(error)
        run.status = RunStatus.FAILED
        run.error = f"{stage.name}: {error}"
        run.finished_at = datetime.now(UTC)
        self.repository.save_stage(stage)
        self.repository.save_run(run)
        self._event(run, stage, "stage_failed", str(error))
        self._terminal(run)
        return run

    def _terminal(self, run: GenerationRun) -> None:
        if self.terminal_hook is None or run.metadata.get("terminal_hook_done"):
            return
        try:
            self.terminal_hook(run)
        except Exception as exc:
            # Hooks may have partially completed (for example, deleting all
            # but one remote Gemini file).  Persist their reduced retry set.
            self.repository.save_run(run)
            self._event(run, None, "terminal_cleanup_failed", str(exc))
            return
        run.metadata["terminal_hook_done"] = True
        self.repository.save_run(run)

    def _checkpoint_required(self, stage: PipelineStage) -> bool:
        options = self.project.options
        return (
            (stage == PipelineStage.EXTRACT_REQUIREMENTS and options.checkpoint_requirements)
            or (stage == PipelineStage.PLAN and options.checkpoint_outline)
            or (stage == PipelineStage.FINAL_GEMINI_REVIEW and options.checkpoint_final_review)
        )

    @staticmethod
    def _checkpoint_acknowledged(run: GenerationRun, stage: PipelineStage) -> bool:
        raw = run.metadata.get("acknowledged_checkpoints", [])
        return isinstance(raw, list) and stage.value in raw

    def acknowledge_checkpoint(self, run_id: str, stage: PipelineStage) -> GenerationRun:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        raw_acknowledged = run.metadata.get("acknowledged_checkpoints", [])
        acknowledged = list(raw_acknowledged) if isinstance(raw_acknowledged, list) else []
        if stage.value not in acknowledged:
            acknowledged.append(stage.value)
        run.metadata["acknowledged_checkpoints"] = acknowledged
        run.status = RunStatus.PAUSED
        self.repository.save_run(run)
        return self.execute(run.id)

    def pause(self, run_id: str) -> GenerationRun:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status not in {RunStatus.RUNNING, RunStatus.RETRYING, RunStatus.WAITING_INPUT}:
            raise RuntimeError(f"Cannot pause a {run.status} run")
        run.status = RunStatus.PAUSED
        self.repository.save_run(run)
        self._event(run, None, "run_paused", "Autopilot paused")
        return run

    def resume(self, run_id: str) -> GenerationRun:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status not in {RunStatus.PAUSED, RunStatus.FAILED, RunStatus.WAITING_INPUT}:
            raise RuntimeError(f"Cannot resume a {run.status} run")
        run.status = RunStatus.PAUSED
        run.finished_at = None
        self.repository.save_run(run)
        return self.execute(run_id)

    def cancel(self, run_id: str) -> GenerationRun:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status == RunStatus.SUCCEEDED:
            raise RuntimeError("A completed run cannot be cancelled")
        run.status = RunStatus.CANCELLED
        run.finished_at = datetime.now(UTC)
        self.repository.save_run(run)
        self._event(run, None, "run_cancel_requested", "Cancellation requested")
        self._terminal(run)
        return run

    def retry_from(self, run_id: str, from_stage: PipelineStage) -> GenerationRun:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        order = PIPELINE_ORDER.index(from_stage)
        for stage in self.repository.list_stages(run_id):
            if stage.order < order:
                continue
            stage.status = StageStatus.QUEUED
            stage.started_at = None
            stage.finished_at = None
            stage.error = None
            stage.output_artifact_ids = []
            stage.checkpoint = {"invalidated": True}
            stage.input_hash = self._input_hash()
            self.repository.save_stage(stage)
        run.input_hash = self._input_hash()
        run.status = RunStatus.PAUSED
        run.finished_at = None
        run.error = None
        run.metadata.pop("terminal_hook_done", None)
        self.repository.save_run(run)
        self._event(run, None, "run_invalidated", f"Rebuild requested from {from_stage.value}")
        return self.execute(run_id)


class RunQuery:
    def __init__(self, repository: RepositoryPort) -> None:
        self.repository = repository

    def status(self, run_id: str) -> GenerationRun:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def stages(self, run_id: str) -> list[StageRun]:
        return self.repository.list_stages(run_id)

    def events(self, run_id: str, *, after_sequence: int = 0) -> list[tuple[int, RunEvent]]:
        return self.repository.list_events(run_id, after_sequence=after_sequence)

    def issues(self, run_id: str) -> list[QAIssue]:
        report = self.repository.get_latest_qa_report(run_id)
        return report.issues if report else []

    def cost(self, run_id: str) -> Decimal:
        return self.status(run_id).cost
