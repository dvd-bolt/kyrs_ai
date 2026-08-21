"""Persist token usage and conservative cost estimates per run and stage."""

from __future__ import annotations

from decimal import Decimal

from papercraft.domain import RunEvent
from papercraft.infrastructure.gemini import UsageRecord

from .ports import RepositoryPort


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
        run = self.repository.get_run(self.run_id)
        if run is None:
            raise KeyError(self.run_id)
        run.cost += record.estimated_cost
        self.repository.save_run(run)
        if run.current_stage:
            for stage in self.repository.list_stages(run.id):
                if stage.name == run.current_stage:
                    stage.cost += record.estimated_cost
                    self.repository.save_stage(stage)
                    break
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
                },
            )
        )
        if self.maximum_cost is not None and run.cost > self.maximum_cost:
            raise CostLimitExceeded(
                f"Estimated run cost {run.cost} {run.currency} exceeds limit "
                f"{self.maximum_cost} {run.currency}"
            )


__all__ = ["CostLimitExceeded", "RunUsageTracker"]
