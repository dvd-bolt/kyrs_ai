"""Durable JSONL worker protocol v1 implementation."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from papercraft.application.api import DesktopApplication, WorkerEvent, WorkerRequest
from papercraft.config import AppSettings
from papercraft.domain import RunEvent


def _safe_error_code(error: Exception) -> str:
    if isinstance(error, (ValueError, KeyError)):
        return "VALIDATION_ERROR"
    return "WORKER_FAILED"


class JsonlWorker:
    """Execute one protocol request and persist/replay its terminal events.

    A request record lives in the same per-project SQLite database as the
    run. That gives request-id replay the same crash boundary as stage
    checkpoints: an interrupted request has no terminal record and can be
    safely reissued, while a completed request never performs its mutation a
    second time.
    """

    def __init__(
        self,
        settings: AppSettings,
        *,
        application_factory: Callable[[AppSettings], DesktopApplication] = DesktopApplication,
    ) -> None:
        self.settings = settings
        self.application_factory = application_factory

    def handle(self, payload: dict[str, Any]) -> list[WorkerEvent]:
        request = WorkerRequest.model_validate(payload)
        application = self.application_factory(self.settings)
        workspace = application._projects.open(request.project_id)
        repository = workspace.repository
        known = repository.get_worker_request(request.request_id)
        fingerprint = request.fingerprint()
        if known is not None:
            known_project, known_fingerprint, outcome = known
            if known_project != request.project_id or known_fingerprint != fingerprint:
                return [
                    self._event(
                        repository,
                        request,
                        event_type="request_failed",
                        error_code="REQUEST_ID_CONFLICT",
                        message="Request identifier was already used for a different command.",
                    )
                ]
            if outcome is not None:
                return [WorkerEvent.model_validate(item) for item in json.loads(outcome)]
        else:
            try:
                repository.record_worker_request(request.request_id, request.project_id, fingerprint)
            except sqlite3.IntegrityError:
                # Another local process won the request-id race. Read its
                # durable result instead of launching a duplicate pipeline.
                known = repository.get_worker_request(request.request_id)
                if known is None or known[1] != fingerprint:
                    return [
                        self._event(
                            repository,
                            request,
                            event_type="request_failed",
                            error_code="REQUEST_ID_CONFLICT",
                            message="Request identifier was already used for a different command.",
                        )
                    ]
                if known[2] is not None:
                    return [WorkerEvent.model_validate(item) for item in json.loads(known[2])]
                # An in-flight process owns this request. Do not duplicate it.
                return [
                    self._event(
                        repository,
                        request,
                        event_type="heartbeat",
                        message="Request is already being processed.",
                    )
                ]

        events: list[WorkerEvent] = []
        try:
            snapshot = application.execute_worker_request(request)
            accepted = self._event(
                repository,
                request,
                run_id=snapshot.id,
                event_type="request_accepted",
                stage=snapshot.stage,
                status=snapshot.status,
                progress=snapshot.progress,
                message="Worker request accepted.",
            )
            finished = self._event(
                repository,
                request,
                run_id=snapshot.id,
                event_type="request_finished",
                stage=snapshot.stage,
                status=snapshot.status,
                progress=snapshot.progress,
                message="Worker request finished.",
                estimated_cost=snapshot.actual_cost,
            )
            events = [accepted, finished]
        except Exception as error:
            events = [
                self._event(
                    repository,
                    request,
                    event_type="request_failed",
                    error_code=_safe_error_code(error),
                    message="Worker request could not be completed.",
                )
            ]
        repository.complete_worker_request(
            request.request_id,
            json.dumps([event.model_dump(mode="json") for event in events], ensure_ascii=False),
        )
        return events

    @staticmethod
    def _event(
        repository: Any,
        request: WorkerRequest,
        *,
        event_type: str,
        run_id: str | None = None,
        stage: str | None = None,
        status: str | None = None,
        progress: float = 0,
        message: str = "",
        error_code: str | None = None,
        estimated_cost: Any = None,
    ) -> WorkerEvent:
        resolved_run_id = run_id or request.run_id
        if resolved_run_id is not None and repository.get_run(resolved_run_id) is not None:
            sequence = repository.append_event(
                RunEvent(
                    run_id=resolved_run_id,
                    event_type=f"worker_{event_type}",
                    message=message,
                    data={"request_id": request.request_id, "error_code": error_code or ""},
                )
            )
        else:
            # start-generation validation can fail before a run exists. The
            # protocol still needs a valid JSONL event, but this sequence is
            # not a run sequence and is therefore deliberately local.
            sequence = 1
        return WorkerEvent(
            request_id=request.request_id,
            project_id=request.project_id,
            run_id=resolved_run_id,
            sequence=sequence,
            event_type=event_type,  # type: ignore[arg-type]
            stage=stage,
            status=status,
            progress=progress,
            message=message,
            error_code=error_code,
            estimated_cost=estimated_cost,
        )


__all__ = ["JsonlWorker"]
