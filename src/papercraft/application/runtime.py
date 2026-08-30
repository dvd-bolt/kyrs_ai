"""Composition root shared by the desktop UI and background worker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue

from papercraft.config import AppSettings
from papercraft.domain import GenerationRun
from papercraft.infrastructure.gemini import GeminiGateway, GeminiPort

from .autopilot import AutopilotService
from .projects import ProjectWorkspace
from .run_state import durable_run_state_lock
from .stages import ProductionStageFactory
from .usage import RunUsageTracker


@dataclass(slots=True)
class AutopilotRuntime:
    service: AutopilotService
    gateway: GeminiPort
    run: GenerationRun


def _adaptive_state_revision(value: object) -> int:
    """Return a durable ordering token for safe coordinator snapshots.

    Older projects contain only the two original adaptive fields, so they map
    to revision zero. A malformed value is likewise never allowed to win over
    a real coordinator update.
    """

    if not isinstance(value, dict):
        return -1
    raw_revision = value.get("revision", 0)
    try:
        return max(0, int(raw_revision))
    except (TypeError, ValueError):
        return 0


def prepare_autopilot(
    settings: AppSettings,
    workspace: ProjectWorkspace,
    *,
    run_id: str | None = None,
    gateway: GeminiPort | None = None,
) -> AutopilotRuntime:
    provider = gateway or GeminiGateway(settings)
    stage_factory = ProductionStageFactory(provider, repository=workspace.repository)
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

    if isinstance(provider, GeminiGateway):
        raw_adaptive_state = run.metadata.get("provider_adaptive_state")
        persisted_adaptive_state = (
            raw_adaptive_state if isinstance(raw_adaptive_state, dict) else None
        )

        def persist_adaptive_state(state: dict[str, int]) -> None:
            # Gemini callbacks can originate in parallel section/research
            # workers. Persist only safe throttling counters, using the same
            # control-preserving path as cost and stage checkpoints.
            with durable_run_state_lock():
                latest = workspace.repository.get_run(run.id)
                if latest is None:
                    return
                existing_state = latest.metadata.get("provider_adaptive_state")
                # The coordinator publishes outside its lock, so a delayed
                # callback from an older success must never overwrite a newer
                # 429 downshift (or a later recovery counter) in the run JSON.
                if _adaptive_state_revision(existing_state) >= _adaptive_state_revision(state):
                    return
                latest.metadata["provider_adaptive_state"] = cast(JsonValue, dict(state))
                workspace.repository.save_run_preserving_control(
                    latest,
                    replace_metadata_keys={"provider_adaptive_state"},
                )

        provider.request_coordinator.restore_adaptive_state(
            persisted_adaptive_state,
            on_change=persist_adaptive_state,
        )
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
