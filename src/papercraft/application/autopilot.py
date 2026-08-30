from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
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
from papercraft.infrastructure.gemini import GeminiUnavailableError
from papercraft.infrastructure.persistence import AtomicArtifactStore, ProjectPaths, sha256_file

from .ports import RepositoryPort
from .run_state import durable_run_state_lock
from .usage import CostLimitExceeded
from .worker_control import (
    CancellationToken,
    RunCancelled,
    StageDependencyGraph,
    recover_stale_stages,
)


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
    cancellation: CancellationToken


class StageHandler(Protocol):
    def __call__(self, context: StageContext) -> StageOutcome: ...


class MissingStageHandler(RuntimeError):
    pass


class ProviderCooldown(RuntimeError):
    """Safe, resumable wrapper for a transient Gemini provider failure."""

    waiting_input = True

    def __init__(self, retry_after_seconds: int | None = None) -> None:
        self.retry_after_seconds = retry_after_seconds
        message = "Gemini временно недоступен; повторите запуск позже"
        if retry_after_seconds is not None:
            message += f" (примерно через {retry_after_seconds} с)"
        super().__init__(message)


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
        self.dependency_graph = StageDependencyGraph.linear(tuple(stage.value for stage in PIPELINE_ORDER))

    def _input_hash(self) -> str:
        # Verified web sources are produced by the pipeline itself and must not
        # invalidate the user's input hash during resume/retry.  ``reference``
        # is also a user-selectable upload role, so role alone is not enough
        # to distinguish generated web material from a real project input.
        sources = [
            source
            for source in self.repository.list_sources(self.project.id)
            if not source.metadata.get("generated")
        ]
        options = self.project.options.model_dump(mode="json")
        # `quality_mode` remains readable for old projects, but all legacy
        # values now execute the same maximum-quality pipeline. It must not
        # create a false cache miss merely because an old JSON file says
        # "balanced" or "economy".
        options["quality_mode"] = "maximum"
        value = {
            # Do not include Project.updated_at/created_at: the desktop saves
            # the assignment before every start and those bookkeeping times do
            # not change a generation input.
            "brief": self.project.brief.model_dump(mode="json"),
            "options": options,
            "project_schema_version": self.project.schema_version,
            "sources": sorted(
                (
                    {
                        "sha256": source.sha256,
                        "role": source.role.value,
                        "mime_type": source.mime_type,
                    }
                    for source in sources
                ),
                key=lambda item: (str(item["sha256"]), str(item["role"]), str(item["mime_type"])),
            ),
            "models": self.settings.model_policy.model_dump(mode="json"),
            "thinking": self.settings.thinking_policy.model_dump(mode="json"),
            "pipeline": "fast-generation-v2",
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

    @staticmethod
    def _retry_after_seconds(error: GeminiUnavailableError) -> int | None:
        explicit = getattr(error, "retry_after_seconds", None)
        if isinstance(explicit, (int, float)) and explicit >= 0:
            return max(1, round(explicit))
        match = re.search(r"(?i)retry\s+in\s+([0-9]+(?:\.[0-9]+)?)\s*s", str(error))
        return max(1, round(float(match.group(1)))) if match else None

    def _provider_cooldown(
        self,
        stage: StageRun,
        error: GeminiUnavailableError,
    ) -> tuple[StageRun, ProviderCooldown]:
        """Checkpoint any Gemini stage, including non-parallel provider calls.

        The request coordinator is process-local; this durable deadline keeps
        a resumed worker from immediately reissuing a 429 after its process
        has been restarted.  Only safe timing/state fields are persisted.
        """

        retry_after_seconds = self._retry_after_seconds(error)
        retry_at = (
            datetime.now(UTC) + timedelta(seconds=retry_after_seconds)
            if retry_after_seconds is not None
            else None
        )
        with durable_run_state_lock():
            current = self.repository.get_stage(stage.id) or stage
            current.checkpoint = {
                **current.checkpoint,
                "progress_message": "Gemini временно ограничил запросы",
                "waiting_for_quota": True,
                "retry_after_seconds": retry_after_seconds or 0,
                "retry_at": retry_at.isoformat() if retry_at is not None else "",
            }
            self.repository.save_stage(current)
        return current, ProviderCooldown(retry_after_seconds)

    def _active_provider_cooldown(self, stage: StageRun) -> ProviderCooldown | None:
        """Honor a durable retry deadline before *any* stage handler runs."""

        checkpoint = stage.checkpoint
        raw_retry_at = checkpoint.get("retry_at")
        retry_at: datetime | None = None
        if isinstance(raw_retry_at, str) and raw_retry_at:
            try:
                retry_at = datetime.fromisoformat(raw_retry_at.replace("Z", "+00:00"))
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
            except ValueError:
                retry_at = None
        if retry_at is not None:
            remaining = (retry_at - datetime.now(UTC)).total_seconds()
            if remaining > 0:
                return ProviderCooldown(max(1, round(remaining)))
        if checkpoint.get("waiting_for_quota") or raw_retry_at:
            # A user has explicitly resumed after a deadline (or after an
            # outage without Retry-After). Clear stale UI state before the
            # handler can emit fresh progress.
            for key in ("waiting_for_quota", "retry_after_seconds", "retry_at", "retry_wait_ms"):
                checkpoint.pop(key, None)
            if checkpoint.get("progress_message") == "Gemini временно ограничил запросы":
                checkpoint.pop("progress_message", None)
            self.repository.save_stage(stage)
        return None

    def _cost_limit_error(self, run: GenerationRun) -> CostLimitExceeded | None:
        maximum_cost = self.project.options.maximum_cost
        if maximum_cost is not None and run.cost >= maximum_cost:
            return CostLimitExceeded(
                f"Estimated run cost {run.cost} {run.currency} has reached limit "
                f"{maximum_cost} {run.currency}"
            )
        if not bool(run.metadata.get("cost_limit_exceeded")):
            return None
        if maximum_cost is None:
            return CostLimitExceeded("Estimated run cost exceeded the configured limit")
        return CostLimitExceeded(
            f"Estimated run cost {run.cost} {run.currency} exceeds limit "
            f"{maximum_cost} {run.currency}"
        )

    def _halt_after_committed_cost_limit(
        self,
        run: GenerationRun,
        stage: StageRun,
        error: CostLimitExceeded,
    ) -> GenerationRun:
        """Stop before another request while retaining the just-saved stage."""

        run.status = RunStatus.FAILED
        run.error = f"{stage.name}: {error}"
        run.finished_at = datetime.now(UTC)
        run = self.repository.save_run_preserving_control(run)
        self._event(run, stage, "run_cost_limit_reached", str(error))
        return run

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
            pipeline_version="fast-generation-v2",
            model_policy={
                "models": self.settings.model_policy.model_dump(mode="json"),
                "thinking": self.settings.thinking_policy.model_dump(mode="json"),
            },
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
        if run.status == RunStatus.SUCCEEDED:
            corrupted = self._first_corrupt_completed_stage(run)
            if corrupted is None:
                self._terminal(run)
                return self.repository.get_run(run_id) or run
            stage, reason = corrupted
            self._invalidate_from(run, PipelineStage(stage.name), reason=reason)
            run.status = RunStatus.RETRYING
            run.finished_at = None
            run.error = None
            run.metadata.pop("terminal_hook_done", None)
            self.repository.save_run_preserving_control(
                run,
                replace_metadata_keys={"terminal_hook_done"},
            )
            self._event(run, stage, "artifact_corruption_recovered", reason)
        elif run.status == RunStatus.CANCELLED:
            # A provider cleanup can fail after the last pipeline stage.  A
            # repeated worker invocation must retry that terminal obligation.
            self._terminal(run)
            return self.repository.get_run(run_id) or run
        if run.input_hash != self._input_hash():
            raise RuntimeError("Project inputs changed; use retry_from to invalidate dependent stages")

        corrupted = self._first_corrupt_completed_stage(run)
        if corrupted is not None:
            stage, reason = corrupted
            self._invalidate_from(run, PipelineStage(stage.name), reason=reason)
            self._event(run, stage, "artifact_corruption_recovered", reason)

        now = datetime.now(UTC)
        run.status = RunStatus.RUNNING
        run.started_at = run.started_at or now
        run.error = None
        run = self.repository.save_run_preserving_control(run)
        if run.status == RunStatus.CANCELLED:
            self._terminal(run)
            return run
        if run.status == RunStatus.PAUSED:
            return run
        self._event(run, None, "run_started", "Autopilot execution started")

        recovered = recover_stale_stages(self.repository, run)
        for stage in recovered:
            self._event(run, stage, "stale_lease_recovered", "Recovered abandoned worker stage")

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
            if cost_error := self._cost_limit_error(run):
                return self._fail(run, stage, cost_error)
            handler = self.handlers.get(stage_name)
            if handler is None:
                return self._fail(run, stage, MissingStageHandler(f"No handler for {stage.name}"))

            if cooldown := self._active_provider_cooldown(stage):
                return self._fail(run, stage, cooldown)

            run.current_stage = stage.name
            run = self.repository.save_run_preserving_control(run)
            if run.status == RunStatus.CANCELLED:
                self._terminal(run)
                return run
            if run.status == RunStatus.PAUSED:
                return run
            stage.status = StageStatus.RUNNING
            stage.started_at = stage.started_at or datetime.now(UTC)
            stage.heartbeat_at = datetime.now(UTC)
            stage.attempts += 1
            stage.error = None
            stage.failure_code = None
            stage.failure_details = {}
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
                        cancellation=CancellationToken(self.repository, run.id, stage.id),
                    )
                )
                with durable_run_state_lock():
                    run = self.repository.get_run(run.id) or run
                    if run.status == RunStatus.CANCELLED:
                        raise RunCancelled("run was cancelled during stage execution")
                    # A parallel handler can already have checkpointed item
                    # artifacts and cost updates.  Reload before the terminal
                    # full-row write so it does not erase either field.
                    stage = self.repository.get_stage(stage.id) or stage
                    for artifact in outcome.artifacts:
                        self.repository.save_artifact(artifact)
                        if artifact.id not in stage.output_artifact_ids:
                            stage.output_artifact_ids.append(artifact.id)
                    stage.checkpoint = dict(outcome.checkpoint)
                    stage.output_hash = self._artifact_set_hash(outcome.artifacts)
                    stage.heartbeat_at = datetime.now(UTC)
                    stage.status = StageStatus.SKIPPED if outcome.skipped else StageStatus.SUCCEEDED
                    stage.finished_at = datetime.now(UTC)
                    stage.error = None
                    self.repository.save_stage(stage)
                self._event(run, stage, "stage_completed", outcome.message or f"Completed: {stage.name}")
                if run.status == RunStatus.PAUSED:
                    return run
                if cost_error := self._cost_limit_error(run):
                    return self._halt_after_committed_cost_limit(run, stage, cost_error)
            except RunCancelled:
                return self._interrupt(run, stage)
            except GeminiUnavailableError as exc:
                # Parallel stages translate this themselves to retain their
                # completed-item checkpoint. This fallback covers every
                # direct Gemini stage (requirements, planning, QA, review),
                # so all transient provider failures become resumable.
                stage, cooldown = self._provider_cooldown(stage, exc)
                return self._fail(run, stage, cooldown)
            except Exception as exc:
                latest = self.repository.get_run(run.id) or run
                if cost_error := self._cost_limit_error(latest):
                    return self._fail(latest, stage, cost_error)
                return self._fail(run, stage, exc)

            if self._checkpoint_required(stage_name) and not self._checkpoint_acknowledged(run, stage_name):
                run.status = RunStatus.WAITING_INPUT
                run = self.repository.save_run_preserving_control(run)
                if run.status == RunStatus.PAUSED:
                    return run
                if run.status == RunStatus.CANCELLED:
                    self._terminal(run)
                    return run
                self._event(run, stage, "checkpoint_waiting", f"Approval required after {stage.name}")
                return run

        run.status = RunStatus.SUCCEEDED
        run.current_stage = None
        run.finished_at = datetime.now(UTC)
        run = self.repository.save_run_preserving_control(run)
        if run.status in {RunStatus.PAUSED, RunStatus.CANCELLED}:
            if run.status == RunStatus.CANCELLED:
                self._terminal(run)
            return run
        self._terminal(run)
        run = self.repository.get_run(run.id) or run
        if run.status == RunStatus.SUCCEEDED:
            self._event(run, None, "run_succeeded", "Autopilot completed successfully")
        return run

    @staticmethod
    def _artifact_set_hash(artifacts: list[Artifact]) -> str:
        payload = [
            {"id": item.id, "sha256": item.sha256, "size_bytes": item.size_bytes}
            for item in artifacts
        ]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _first_corrupt_completed_stage(
        self, run: GenerationRun
    ) -> tuple[StageRun, str] | None:
        artifacts = {
            item.id: item
            for item in self.repository.list_artifacts(self.project.id, run_id=run.id)
        }
        for stage in self.repository.list_stages(run.id):
            if stage.status not in {StageStatus.SUCCEEDED, StageStatus.SKIPPED}:
                continue
            for artifact_id in stage.output_artifact_ids:
                artifact = artifacts.get(artifact_id)
                if artifact is None:
                    return stage, f"Output artifact record is missing: {artifact_id}"
                path = Path(artifact.path)
                try:
                    valid = (
                        path.is_file()
                        and path.stat().st_size == artifact.size_bytes
                        and sha256_file(path) == artifact.sha256
                    )
                except OSError:
                    valid = False
                if not valid:
                    return stage, f"Output artifact failed integrity verification: {artifact_id}"
        return None

    def _invalidate_from(
        self,
        run: GenerationRun,
        from_stage: PipelineStage,
        *,
        reason: str = "requested rebuild",
    ) -> None:
        affected = self.dependency_graph.affected_by(from_stage.value)
        input_hash = self._input_hash()
        for stage in self.repository.list_stages(run.id):
            if stage.name not in affected:
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
            self.repository.save_stage(stage)

    def _interrupt(self, run: GenerationRun, stage: StageRun) -> GenerationRun:
        latest = self.repository.get_run(run.id) or run
        stage.status = StageStatus.QUEUED
        stage.finished_at = None
        stage.error = None
        stage.failure_code = None
        stage.failure_details = {}
        stage.heartbeat_at = datetime.now(UTC)
        self.repository.save_stage(stage)
        if latest.status == RunStatus.CANCELLED:
            self._event(latest, stage, "stage_cancelled", "Stage stopped at a durable checkpoint")
            self._terminal(latest)
        else:
            # A pause request is durable state, not a stage failure.
            latest.status = RunStatus.PAUSED
            latest = self.repository.save_run_preserving_control(latest)
            if latest.status == RunStatus.CANCELLED:
                self._event(latest, stage, "stage_cancelled", "Stage stopped at a durable checkpoint")
                self._terminal(latest)
                return latest
            self._event(latest, stage, "stage_paused", "Stage paused at a durable checkpoint")
        return self.repository.get_run(latest.id) or latest

    def _fail(self, run: GenerationRun, stage: StageRun, error: Exception) -> GenerationRun:
        latest = self.repository.get_run(run.id) or run
        if latest.status in {RunStatus.PAUSED, RunStatus.CANCELLED}:
            return self._interrupt(latest, stage)
        stage.status = StageStatus.FAILED
        stage.finished_at = datetime.now(UTC)
        stage.error = str(error)
        stage.failure_code = type(error).__name__
        stage.failure_details = {"message": str(error)}
        needs_input = bool(getattr(error, "waiting_input", False))
        run.status = RunStatus.WAITING_INPUT if needs_input else RunStatus.FAILED
        run.error = f"{stage.name}: {error}"
        run.finished_at = None if needs_input else datetime.now(UTC)
        self.repository.save_stage(stage)
        run = self.repository.save_run_preserving_control(run)
        if run.status in {RunStatus.PAUSED, RunStatus.CANCELLED}:
            return self._interrupt(run, stage)
        self._event(run, stage, "stage_waiting_input" if needs_input else "stage_failed", str(error))
        # A failed run is intentionally resumable.  In particular, a 429 can
        # occur after ingest has uploaded local source files, while the retry
        # continues at a later stage.  Cleaning those files here would make a
        # resumed requirements/plan call silently lose its inputs.  Terminal
        # cleanup is therefore reserved for success and explicit cancellation;
        # a user can cancel a failed run to discard retained remote files.
        return run

    def _terminal(self, run: GenerationRun) -> None:
        if self.terminal_hook is None or run.metadata.get("terminal_hook_done"):
            return
        try:
            self.terminal_hook(run)
        except Exception as exc:
            # Hooks may have partially completed (for example, deleting all
            # but one remote Gemini file).  Persist their reduced retry set.
            error_type = type(exc).__name__
            run.metadata["terminal_cleanup_pending"] = True
            run.metadata["terminal_cleanup_error_type"] = error_type
            if run.status == RunStatus.SUCCEEDED:
                run.status = RunStatus.FAILED
                run.finished_at = datetime.now(UTC)
                run.error = f"terminal cleanup failed ({error_type})"
            self.repository.save_run_preserving_control(
                run,
                replace_metadata_keys={
                    "terminal_hook_done",
                    "terminal_cleanup_pending",
                    "terminal_cleanup_error_type",
                },
            )
            # Do not persist provider exception text: it can contain sensitive
            # request data.  The exception class is sufficient for retry/audit.
            self._event(run, None, "terminal_cleanup_failed", error_type)
            return
        run.metadata["terminal_hook_done"] = True
        run.metadata.pop("terminal_cleanup_pending", None)
        run.metadata.pop("terminal_cleanup_error_type", None)
        self.repository.save_run_preserving_control(
            run,
            replace_metadata_keys={
                "terminal_hook_done",
                "terminal_cleanup_pending",
                "terminal_cleanup_error_type",
            },
        )

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
        stage_run = next(
            (item for item in self.repository.list_stages(run_id) if item.name == stage.value),
            None,
        )
        if (
            run.status != RunStatus.WAITING_INPUT
            or stage_run is None
            or stage_run.status not in {StageStatus.SUCCEEDED, StageStatus.SKIPPED}
            or not self._checkpoint_required(stage)
        ):
            raise RuntimeError(f"No pending checkpoint can be acknowledged after {stage.value}")
        raw_acknowledged = run.metadata.get("acknowledged_checkpoints", [])
        acknowledged = list(raw_acknowledged) if isinstance(raw_acknowledged, list) else []
        if stage.value not in acknowledged:
            acknowledged.append(stage.value)
        run.metadata["acknowledged_checkpoints"] = acknowledged
        run.status = RunStatus.RETRYING
        run = self.repository.save_run_preserving_control(run)
        return self.execute(run.id)

    def pause(self, run_id: str) -> GenerationRun:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status not in {RunStatus.RUNNING, RunStatus.RETRYING, RunStatus.WAITING_INPUT}:
            raise RuntimeError(f"Cannot pause a {run.status} run")
        run = self.repository.transition_run_status(
            run_id,
            status=RunStatus.PAUSED,
            allowed_from={RunStatus.RUNNING, RunStatus.RETRYING, RunStatus.WAITING_INPUT},
        )
        self._event(run, None, "run_paused", "Autopilot paused")
        return run

    def resume(self, run_id: str) -> GenerationRun:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status not in {RunStatus.PAUSED, RunStatus.FAILED, RunStatus.WAITING_INPUT}:
            raise RuntimeError(f"Cannot resume a {run.status} run")
        pending_checkpoint = next(
            (
                PipelineStage(stage.name)
                for stage in self.repository.list_stages(run_id)
                if stage.status in {StageStatus.SUCCEEDED, StageStatus.SKIPPED}
                and self._checkpoint_required(PipelineStage(stage.name))
                and not self._checkpoint_acknowledged(run, PipelineStage(stage.name))
            ),
            None,
        )
        if pending_checkpoint is not None:
            raise RuntimeError(
                f"Checkpoint after {pending_checkpoint.value} must be explicitly acknowledged"
            )
        # This is an explicit user-driven transition, so it must be a CAS
        # rather than the worker-only preserving save (which deliberately
        # refuses to turn PAUSED/CANCELLED back into RUNNING-like states).
        run = self.repository.transition_run_status(
            run_id,
            status=RunStatus.RETRYING,
            allowed_from={RunStatus.PAUSED, RunStatus.FAILED, RunStatus.WAITING_INPUT},
        )
        run.finished_at = None
        run.metadata.pop("terminal_hook_done", None)
        run = self.repository.save_run_preserving_control(
            run,
            replace_metadata_keys={"terminal_hook_done"},
        )
        return self.execute(run.id)

    def cancel(self, run_id: str) -> GenerationRun:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status == RunStatus.SUCCEEDED:
            raise RuntimeError("A completed run cannot be cancelled")
        run = self.repository.transition_run_status(
            run_id,
            status=RunStatus.CANCELLED,
            allowed_from={
                RunStatus.QUEUED,
                RunStatus.RUNNING,
                RunStatus.RETRYING,
                RunStatus.PAUSED,
                RunStatus.WAITING_INPUT,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            },
            finished_at=datetime.now(UTC),
        )
        self._event(run, None, "run_cancel_requested", "Cancellation requested")
        self._terminal(run)
        return run

    def retry_from(self, run_id: str, from_stage: PipelineStage) -> GenerationRun:
        # Claim the retry and reset every dependent stage in one database
        # transaction.  In particular, CANCELLED is deliberately excluded:
        # cancelling in another process must never be overwritten by an old
        # retry request that happens to arrive afterwards.
        run = self.repository.prepare_retry(
            run_id,
            stage_names=self.dependency_graph.affected_by(from_stage.value),
            input_hash=self._input_hash(),
            reason="requested rebuild",
            allowed_from={
                RunStatus.QUEUED,
                RunStatus.RUNNING,
                RunStatus.RETRYING,
                RunStatus.PAUSED,
                RunStatus.WAITING_INPUT,
                RunStatus.SUCCEEDED,
                RunStatus.FAILED,
            },
            maximum_cost=self.project.options.maximum_cost,
        )
        self._event(run, None, "run_invalidated", f"Rebuild requested from {from_stage.value}")
        return self.execute(run.id)

    def refresh_research(self, run_id: str) -> GenerationRun:
        """Force a new web-evidence pass while preserving all other inputs."""

        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        # A source refresh deliberately starts at VERIFIED_RESEARCH so it can
        # retain the existing claim plan.  It is therefore unsafe after an
        # assignment, uploaded file, model, or other fingerprinted input has
        # changed: retry_from would otherwise replace the run hash while
        # leaving the old plan and claims in place.
        if run.input_hash != self._input_hash():
            raise RuntimeError(
                "Project inputs changed; rebuild from an upstream stage before refreshing sources"
            )
        run.metadata["force_research_refresh"] = True
        self.repository.save_run_preserving_control(run)
        self._event(run, None, "research_refresh_requested", "Fresh source verification requested")
        return self.retry_from(run_id, PipelineStage.VERIFIED_RESEARCH)


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
