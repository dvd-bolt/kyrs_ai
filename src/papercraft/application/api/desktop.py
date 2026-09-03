"""The sole public in-process entry point for a PaperCraft desktop UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from papercraft.application import (
    DocumentService,
    PipelineStage,
    ProjectService,
    ProjectWorkspace,
    SourceService,
    prepare_autopilot,
)
from papercraft.config import AppSettings
from papercraft.domain import GenerationRun, ProjectBrief, RunStatus, SourceRole
from papercraft.infrastructure.gemini import CredentialSecretStore, SecretStore

from ..credentials import GatewayFactory, GeminiCredentialService
from .contracts import (
    APPLICATION_API_VERSION,
    CredentialStatus,
    Money,
    ProviderCheck,
    RunSnapshot,
    WorkerAction,
    WorkerRequest,
)


def _timestamp(value: object) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else None


def _public_status(run: GenerationRun) -> str:
    if run.status in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.RETRYING}:
        return "RUNNING"
    if run.status is RunStatus.WAITING_INPUT:
        return "WAITING_PROVIDER" if run.metadata.get("retry_at") else "WAITING_INPUT"
    if run.status is RunStatus.PAUSED:
        return "WAITING_INPUT"
    if run.status is RunStatus.CANCELLED:
        return "CANCELLED"
    if run.status is RunStatus.SUCCEEDED:
        return "READY_TO_SUBMIT"
    return "QUALITY_FAILED" if run.metadata.get("quality_failed") else "DRAFT"


@dataclass(slots=True)
class DesktopApplication:
    """Facade that owns all project/database/pipeline access for the UI.

    The return values intentionally contain only JSON-safe DTO dictionaries or
    versioned run snapshots. This keeps the legacy domain model private while
    allowing the module-10 UI to be implemented without SQLite imports.
    """

    settings: AppSettings
    credential_store: SecretStore = field(default_factory=CredentialSecretStore, kw_only=True, repr=False)
    gateway_factory: GatewayFactory | None = field(default=None, kw_only=True, repr=False)
    _projects: ProjectService = field(init=False, repr=False)
    _credentials: GeminiCredentialService = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.settings.ensure_directories()
        self._projects = ProjectService(self.settings)
        self._credentials = GeminiCredentialService(
            self.settings,
            self.credential_store,
            gateway_factory=self.gateway_factory,
        )

    @property
    def api_version(self) -> int:
        return APPLICATION_API_VERSION

    def create_project(self, draft: dict[str, Any]) -> dict[str, Any]:
        work_type = draft.get("work_type", "coursework")
        brief = ProjectBrief(
            topic=str(draft["topic"]),
            title=str(draft.get("title") or ""),
            prompt=str(draft.get("instructions") or ""),
            work_type=work_type,
            language=str(draft.get("language") or "ru-RU"),
            title_page=dict(draft.get("title_page") or {}),
        )
        workspace = self._projects.create(brief)
        return self._project_view(workspace.project)

    def list_projects(self) -> list[dict[str, Any]]:
        return [self._project_view(project) for project in self._projects.list()]

    def get_project(self, project_id: str) -> dict[str, Any]:
        return self._project_view(self._projects.open(project_id).project)

    def update_project(self, project_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        workspace = self._projects.open(project_id)
        current = workspace.project
        brief_data = current.brief.model_dump(mode="python")
        aliases = {"instructions": "prompt"}
        for key, value in patch.items():
            if key in {"topic", "title", "work_type", "language", "title_page", "instructions"}:
                brief_data[aliases.get(key, key)] = value
        updated = self._projects.update(project_id, brief=ProjectBrief.model_validate(brief_data))
        return self._project_view(updated.project)

    def list_sources(self, project_id: str) -> list[dict[str, Any]]:
        workspace = self._projects.open(project_id)
        return [source.model_dump(mode="json") | {"api_version": APPLICATION_API_VERSION} for source in workspace.repository.list_sources(project_id)]

    def import_source(self, project_id: str, source: dict[str, Any]) -> list[dict[str, Any]]:
        if source.get("kind") not in {"file", "table", "code_directory"}:
            raise ValueError("This compatibility facade currently imports local file sources only")
        workspace = self._projects.open(project_id)
        role = SourceRole(source["role"]) if source.get("role") else SourceRole.UNKNOWN
        result = SourceService(workspace).import_files([Path(str(source["location"]))], role)
        return [item.model_dump(mode="json") | {"api_version": APPLICATION_API_VERSION} for item in result.sources]

    def start_generation(self, project_id: str) -> RunSnapshot:
        workspace = self._projects.open(project_id)
        runtime = prepare_autopilot(self.settings, workspace)
        return self._snapshot(runtime.run, workspace)

    def pause_generation(self, run_id: str) -> RunSnapshot:
        workspace = self._workspace_for_run(run_id)
        runtime = prepare_autopilot(self.settings, workspace, run_id=run_id)
        return self._snapshot(runtime.service.pause(run_id), workspace)

    def resume_generation(self, run_id: str) -> RunSnapshot:
        workspace = self._workspace_for_run(run_id)
        runtime = prepare_autopilot(self.settings, workspace, run_id=run_id)
        return self._snapshot(runtime.service.resume(run_id), workspace)

    def cancel_generation(self, run_id: str) -> RunSnapshot:
        workspace = self._workspace_for_run(run_id)
        runtime = prepare_autopilot(self.settings, workspace, run_id=run_id)
        return self._snapshot(runtime.service.cancel(run_id), workspace)

    def retry_generation(self, run_id: str, stage_id: str | None = None) -> RunSnapshot:
        workspace = self._workspace_for_run(run_id)
        runtime = prepare_autopilot(self.settings, workspace, run_id=run_id)
        stage = PipelineStage(stage_id) if stage_id else PipelineStage.PREFLIGHT
        return self._snapshot(runtime.service.retry_from(run_id, stage), workspace)

    def regenerate_section(self, run_id: str, section_id: str) -> RunSnapshot:
        workspace = self._workspace_for_run(run_id)
        runtime = prepare_autopilot(self.settings, workspace, run_id=run_id)
        return self._snapshot(
            DocumentService(workspace.project.id, workspace.repository).rebuild_section(
                runtime.service, run_id, section_id
            ),
            workspace,
        )

    def get_run_snapshot(self, run_id: str) -> RunSnapshot:
        workspace = self._workspace_for_run(run_id)
        run = workspace.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return self._snapshot(run, workspace)

    def execute_worker_request(self, request: WorkerRequest) -> RunSnapshot:
        """Dispatch a protocol command; long generation remains worker-owned."""

        if request.action is WorkerAction.START_GENERATION:
            snapshot = self.start_generation(request.project_id)
            workspace = self._projects.open(request.project_id)
            runtime = prepare_autopilot(self.settings, workspace, run_id=snapshot.id)
            return self._snapshot(runtime.service.execute(snapshot.id), workspace)
        if request.run_id is None:  # validated by WorkerRequest, keeps type narrowing explicit.
            raise ValueError("run_id is required")
        if request.action is WorkerAction.PAUSE_GENERATION:
            return self.pause_generation(request.run_id)
        if request.action is WorkerAction.RESUME_GENERATION:
            return self.resume_generation(request.run_id)
        if request.action is WorkerAction.CANCEL_GENERATION:
            return self.cancel_generation(request.run_id)
        if request.action is WorkerAction.RETRY_GENERATION:
            return self.retry_generation(request.run_id, request.stage_id)
        return self.regenerate_section(request.run_id, str(request.section_id))

    def credential_status(self) -> CredentialStatus:
        return self._credentials.status()

    def configure_gemini(self, api_key: str) -> CredentialStatus:
        return self._credentials.configure(api_key)

    def verify_gemini(self) -> ProviderCheck:
        return self._credentials.verify()

    def delete_gemini_key(self) -> None:
        self._credentials.delete()

    def _workspace_for_run(self, run_id: str) -> ProjectWorkspace:
        for project in self._projects.list():
            workspace = self._projects.open(project.id)
            if workspace.repository.get_run(run_id) is not None:
                return workspace
        raise KeyError(run_id)

    @staticmethod
    def _project_view(project: Any) -> dict[str, Any]:
        payload = cast(dict[str, Any], project.model_dump(mode="json"))
        payload["api_version"] = APPLICATION_API_VERSION
        return payload

    @staticmethod
    def _snapshot(run: GenerationRun, workspace: Any) -> RunSnapshot:
        stages = workspace.repository.list_stages(run.id)
        active = next((stage for stage in stages if stage.name == run.current_stage), None)
        progress = 0.0
        if active is not None and active.progress_total:
            progress = active.progress_current / active.progress_total
        retry_at = run.metadata.get("retry_at")
        return RunSnapshot(
            id=run.id,
            project_id=run.project_id,
            status=_public_status(run),
            stage=run.current_stage,
            progress=progress,
            message=str(run.error or ""),
            retry_at=str(retry_at) if retry_at else None,
            actual_cost=Money.from_decimal(run.cost, run.currency),
            started_at=_timestamp(run.started_at),
            finished_at=_timestamp(run.finished_at),
            error_code="RUN_FAILED" if run.error else None,
            can_pause=run.status in {RunStatus.RUNNING, RunStatus.RETRYING, RunStatus.WAITING_INPUT},
            can_resume=run.status in {RunStatus.PAUSED, RunStatus.FAILED, RunStatus.WAITING_INPUT},
            can_cancel=run.status not in {RunStatus.SUCCEEDED, RunStatus.CANCELLED},
            can_retry=run.status is RunStatus.FAILED,
        )


__all__ = ["DesktopApplication"]
