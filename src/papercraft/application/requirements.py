"""Deterministic requirement precedence, conflict handling and coverage."""

from __future__ import annotations

from collections import defaultdict

from papercraft.domain import (
    Conflict,
    RequirementCoverage,
    RequirementPriority,
    RequirementRule,
    RequirementSet,
)

_PRECEDENCE = {
    RequirementPriority.METHODOLOGY: 0,
    RequirementPriority.INSTITUTION_TEMPLATE: 1,
    RequirementPriority.USER: 2,
    RequirementPriority.EXAMPLE: 3,
    RequirementPriority.PROFILE: 4,
    RequirementPriority.BUILTIN: 5,
}


def _rank(rule: RequirementRule) -> int:
    priority = min((item.priority for item in rule.provenance), key=lambda item: _PRECEDENCE[item], default=RequirementPriority.BUILTIN)
    return _PRECEDENCE[priority]


class RequirementConflictError(RuntimeError):
    """The caller must transition the run to WAITING_INPUT, not guess."""


class RequirementResolver:
    def resolve(self, requirements: RequirementSet) -> RequirementSet:
        grouped: dict[str, list[RequirementRule]] = defaultdict(list)
        for rule in requirements.rules:
            grouped[rule.key].append(rule)
        conflicts: list[Conflict] = []
        for key, rules in grouped.items():
            distinct = {str(item.value) for item in rules}
            if len(rules) < 2 or len(distinct) < 2:
                continue
            best_rank = min(_rank(rule) for rule in rules)
            winners = [
                rule
                for rule in rules
                if _rank(rule) == best_rank
            ]
            conflicts.append(
                Conflict(
                    key=key,
                    rule_ids=[item.id for item in rules],
                    description=f"conflicting requirement values for {key}",
                    resolved_rule_id=winners[0].id if len(winners) == 1 else None,
                    resolution_reason="fixed precedence" if len(winners) == 1 else "equal-priority conflict",
                )
            )
        return requirements.model_copy(update={"conflicts": conflicts})

    @staticmethod
    def require_resolved(requirements: RequirementSet) -> None:
        if any(conflict.resolved_rule_id is None for conflict in requirements.conflicts):
            raise RequirementConflictError("requirements contain an ambiguous equal-priority conflict")


def coverage_for_rules(
    requirements: RequirementSet,
    satisfied_rule_ids: set[str],
    not_applicable_rule_ids: set[str] | None = None,
) -> list[RequirementCoverage]:
    not_applicable_rule_ids = not_applicable_rule_ids or set()
    return [
        RequirementCoverage(
            requirement_rule_id=rule.id,
            status=(
                "SATISFIED"
                if rule.id in satisfied_rule_ids
                else "NOT_APPLICABLE"
                if rule.id in not_applicable_rule_ids
                else "FAILED"
            ),
        )
        for rule in requirements.rules
        if rule.mandatory
    ]


__all__ = ["RequirementConflictError", "RequirementResolver", "coverage_for_rules"]
