"""Composition root shared by the desktop UI and background worker."""

from __future__ import annotations

from dataclasses import dataclass

from papercraft.config import AppSettings
from papercraft.domain import GenerationRun
from papercraft.infrastructure.gemini import GeminiGateway, GeminiPort

from .autopilot import AutopilotService
from .projects import ProjectWorkspace
from .stages import ProductionStageFactory
from .usage import RunUsageTracker


@dataclass(slots=True)
class AutopilotRuntime:
    service: AutopilotService
    gateway: GeminiPort
    run: GenerationRun


def prepare_autopilot(
    settings: AppSettings,
    workspace: ProjectWorkspace,
    *,
    run_id: str | None = None,
    gateway: GeminiPort | None = None,
) -> AutopilotRuntime:
    provider = gateway or GeminiGateway(settings)
    stage_factory = ProductionStageFactory(provider)
    service = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        stage_factory.build(),
        terminal_hook=stage_factory.cleanup_remote_files,
    )
    if run_id is None:
        run = service.create_run()
    else:
        existing_run = workspace.repository.get_run(run_id)
        if existing_run is None:
            raise KeyError(run_id)
        run = existing_run
    tracker = RunUsageTracker(
        workspace.repository,
        run.id,
        maximum_cost=workspace.project.options.maximum_cost,
    )
    # The production gateway exposes this callback; explicit fakes used by
    # contract tests may omit it and do not report token usage.
    if hasattr(provider, "usage_sink"):
        provider.usage_sink = tracker
    return AutopilotRuntime(service=service, gateway=provider, run=run)


__all__ = ["AutopilotRuntime", "prepare_autopilot"]
