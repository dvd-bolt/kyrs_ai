"""Deterministic requirement precedence, conflict handling and coverage."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Literal, cast

from papercraft.domain import (
    Conflict,
    Locator,
    RequirementCoverage,
    RequirementCoverageAssessment,
    RequirementCoverageEntry,
    RequirementCoverageReport,
    RequirementPriority,
    RequirementRule,
    RequirementSet,
    RuleProvenance,
)

_PRECEDENCE = {
    RequirementPriority.METHODOLOGY: 0,
    RequirementPriority.INSTITUTION_TEMPLATE: 1,
    RequirementPriority.USER: 2,
    RequirementPriority.EXAMPLE: 3,
    RequirementPriority.PROFILE: 4,
    RequirementPriority.BUILTIN: 5,
}


RequirementCriticality = Literal["critical", "standard"]


def requirement_priority(rule: RequirementRule) -> RequirementPriority:
    """Return the highest-precedence provenance priority recorded for a rule."""

    return min(
        (item.priority for item in rule.provenance),
        key=lambda priority: (_PRECEDENCE[priority], priority.value),
        default=RequirementPriority.BUILTIN,
    )


def requirement_criticality(rule: RequirementRule) -> RequirementCriticality:
    """Infer export criticality without changing the persisted rule contract."""

    explicit = rule.metadata.get("criticality")
    if isinstance(explicit, str):
        normalized = explicit.casefold()
        if normalized in {"critical", "standard"}:
            return cast(RequirementCriticality, normalized)
    if rule.metadata.get("critical") is True:
        return "critical"
    if rule.metadata.get("critical") is False:
        return "standard"
    # A profile is useful scaffolding, but it is not an institution's
    # methodology.  A generated plan can therefore report an unmet profile
    # convention without silently turning a compact smoke run (or a legacy
    # project) into an unexportable document.  Formal, user-specified and
    # institution-template requirements remain release-blocking whenever
    # mandatory.  Callers can always make a profile rule blocking through the
    # explicit ``critical`` / ``criticality`` metadata above.
    formal_priorities = {
        RequirementPriority.METHODOLOGY,
        RequirementPriority.INSTITUTION_TEMPLATE,
        RequirementPriority.USER,
    }
    return (
        "critical"
        if rule.mandatory and requirement_priority(rule) in formal_priorities
        else "standard"
    )


def _rank(rule: RequirementRule) -> int:
    return _PRECEDENCE[requirement_priority(rule)]


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


def build_requirement_coverage_report(
    requirements: RequirementSet,
    coverage: Iterable[RequirementCoverage] = (),
    *,
    assessments: Mapping[str, RequirementCoverageAssessment] | None = None,
) -> RequirementCoverageReport:
    """Build a stable, traceable coverage report for every requirement rule.

    ``coverage`` accepts the legacy uppercase status model, so existing callers
    can enrich their old result without migrating stored checkpoints. Explicit
    ``assessments`` take precedence and carry block/page/evidence provenance.
    A rule with no assessment is intentionally reported as ``missing`` rather
    than treated as implicitly satisfied.
    """

    rules_by_id = {rule.id: rule for rule in requirements.rules}
    if len(rules_by_id) != len(requirements.rules):
        raise ValueError("requirement rules must be unique before coverage is built")
    legacy_by_rule = _legacy_coverage_by_rule(coverage, rules_by_id)
    assessment_by_rule = dict(assessments or {})
    unknown_assessments = sorted(set(assessment_by_rule) - set(rules_by_id))
    if unknown_assessments:
        raise ValueError(
            "coverage assessments reference unknown requirement rules: "
            + ", ".join(unknown_assessments)
        )

    entries: list[RequirementCoverageEntry] = []
    for rule in sorted(requirements.rules, key=lambda item: (item.key, item.id)):
        assessment = assessment_by_rule.get(rule.id)
        legacy = legacy_by_rule.get(rule.id)
        if assessment is not None:
            details = assessment.model_dump()
        elif legacy is not None:
            details = {
                "status": _coverage_status_from_legacy(legacy.status),
                "evidence_summary": legacy.evidence,
                "artifact_id": legacy.artifact_id,
            }
        else:
            details = {
                "status": "missing",
                "reason": "No coverage assessment was recorded.",
            }

        provenance = _sorted_provenance(rule.provenance)
        entries.append(
            RequirementCoverageEntry(
                requirement_rule_id=rule.id,
                requirement_key=rule.key,
                statement=rule.statement,
                mandatory=rule.mandatory,
                criticality=requirement_criticality(rule),
                priority=requirement_priority(rule),
                provenance=provenance,
                source_locators=_source_locators(provenance),
                **details,
            )
        )

    return RequirementCoverageReport(
        project_id=requirements.project_id,
        requirement_set_id=requirements.id,
        entries=entries,
    )


def _legacy_coverage_by_rule(
    coverage: Iterable[RequirementCoverage], rules_by_id: Mapping[str, RequirementRule]
) -> dict[str, RequirementCoverage]:
    result: dict[str, RequirementCoverage] = {}
    for item in coverage:
        if item.requirement_rule_id not in rules_by_id:
            raise ValueError(
                "coverage references unknown requirement rule: " + item.requirement_rule_id
            )
        if item.requirement_rule_id in result:
            raise ValueError("coverage must contain at most one entry per requirement rule")
        result[item.requirement_rule_id] = item
    return result


def _coverage_status_from_legacy(
    status: Literal["SATISFIED", "NOT_APPLICABLE", "FAILED"],
) -> Literal["covered", "partial", "missing"]:
    return cast(
        Literal["covered", "partial", "missing"],
        {
            "SATISFIED": "covered",
            "NOT_APPLICABLE": "covered",
            "FAILED": "missing",
        }[status],
    )


def _sorted_provenance(provenance: Iterable[RuleProvenance]) -> list[RuleProvenance]:
    return sorted(
        (item.model_copy(deep=True) for item in provenance),
        key=lambda item: (
            _PRECEDENCE[item.priority],
            item.source_id or "",
            _locator_key(item.locator),
            item.extraction_method,
            item.confidence,
        ),
    )


def _source_locators(provenance: Iterable[RuleProvenance]) -> list[Locator]:
    locators: list[Locator] = []
    seen: set[str] = set()
    for item in provenance:
        if item.locator is None:
            continue
        key = _locator_key(item.locator)
        if key in seen:
            continue
        seen.add(key)
        locators.append(item.locator.model_copy(deep=True))
    return locators


def _locator_key(locator: Locator | None) -> str:
    if locator is None:
        return ""
    return json.dumps(locator.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


__all__ = [
    "RequirementConflictError",
    "RequirementResolver",
    "build_requirement_coverage_report",
    "coverage_for_rules",
    "requirement_criticality",
    "requirement_priority",
]
