"""Persist token usage and conservative cost estimates per run and stage."""

from __future__ import annotations

from decimal import Decimal

from papercraft.domain import RunEvent
from papercraft.infrastructure.gemini import UsageRecord

from .ports import RepositoryPort
from .run_state import durable_run_state_lock


class CostLimitExceeded(RuntimeError):
    pass


class RunUsageTracker:
    def __init__(
        self,
        repository: RepositoryPort,
        run_id: str,
        *,
        maximum_cost: Decimal | None = None,
    ) -> None:
        self.repository = repository
        self.run_id = run_id
        self.maximum_cost = maximum_cost

    def __call__(self, record: UsageRecord) -> None:
        # Gateway calls happen in section/research worker threads.  The
        # callback that checkpoints a completed item updates the same StageRun
        # JSON record on the coordinator thread, so make the whole
        # read-modify-write sequence indivisible within this worker process.
        with durable_run_state_lock():
            # SQLite performs this mutation in one immediate transaction, so
            # a pause/cancel issued by the desktop in another process keeps
            # its status instead of being overwritten by a stale RUNNING row.
            run = self.repository.add_run_usage(
                self.run_id,
                record.estimated_cost,
                maximum_cost=self.maximum_cost,
            )
            self.repository.append_event(
                RunEvent(
                    run_id=run.id,
                    event_type="gemini_usage",
                    message=f"{record.operation}: {record.total_tokens} tokens",
                    data={
                        "model": record.model,
                        "operation": record.operation,
                        "input_tokens": record.input_tokens,
                        "output_tokens": record.output_tokens,
                        "estimated_cost": float(record.estimated_cost),
                        "currency": run.currency,
                        "thought_tokens": int(record.metadata.get("thought_tokens", 0) or 0),
                        "tool_use_tokens": int(record.metadata.get("tool_use_tokens", 0) or 0),
                        "search_queries": int(record.metadata.get("search_queries", 0) or 0),
                        "interaction_id": str(record.metadata.get("interaction_id", "") or ""),
                        "request_id": str(record.metadata.get("request_id", "") or ""),
                        "duration_ms": int(record.metadata.get("duration_ms", 0) or 0),
                        "attempts": int(record.metadata.get("attempts", 1) or 1),
                        "retry_wait_ms": int(record.metadata.get("retry_wait_ms", 0) or 0),
                        "cache_hit": False,
                        "work_item_id": str(record.metadata.get("work_item_id", "") or ""),
                    },
                )
            )
            # Do not throw from the provider's usage callback. The response
            # that triggered the cap is already billable, and an exception
            # here would discard it before the section/research scheduler can
            # checkpoint the completed work item. Parallel stages observe the
            # durable flag as a soft admission stop, drain in-flight results,
            # and then surface CostLimitExceeded at the stage boundary.

    def limit_reached(self) -> bool:
        """Expose the durable cap to the gateway before its next request."""

        run = self.repository.get_run(self.run_id)
        return bool(
            run is not None
            and (
                run.metadata.get("cost_limit_exceeded")
                or (
                    self.maximum_cost is not None
                    and run.cost >= self.maximum_cost
                )
            )
        )


__all__ = ["CostLimitExceeded", "RunUsageTracker"]
