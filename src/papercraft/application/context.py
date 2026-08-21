"""Build a least-privilege generation context for one manuscript section."""

from __future__ import annotations

from dataclasses import dataclass

from papercraft.domain import (
    BibliographyEntry,
    Claim,
    Dataset,
    Evidence,
    ProjectBlueprint,
    RequirementRule,
    SectionSpec,
)


@dataclass(frozen=True, slots=True)
class SectionContext:
    section: SectionSpec
    claims: list[Claim]
    evidence: list[Evidence]
    bibliography: list[BibliographyEntry]
    datasets: list[Dataset]
    glossary: dict[str, str]
    requirements: list[RequirementRule]
    dependency_conclusions: dict[str, str]


class ContextBuilder:
    def build(
        self,
        section: SectionSpec,
        blueprint: ProjectBlueprint,
        claims: list[Claim],
        evidence: list[Evidence],
        bibliography: list[BibliographyEntry],
        datasets: list[Dataset],
        requirements: list[RequirementRule],
        dependency_conclusions: dict[str, str] | None = None,
    ) -> SectionContext:
        selected_claims = [item for item in claims if item.section_id in {None, section.id}]
        evidence_ids = {item_id for claim in selected_claims for item_id in claim.evidence_ids}
        selected_evidence = [item for item in evidence if item.id in evidence_ids and item.verified]
        selected_sources = {item.source_id for item in selected_evidence} | set(section.source_ids)
        selected_bibliography = [item for item in bibliography if item.source_id in selected_sources]
        dataset_ids = set(section.required_fact_ids)
        selected_datasets = [item for item in datasets if not dataset_ids or item.id in dataset_ids]
        return SectionContext(
            section=section,
            claims=selected_claims,
            evidence=selected_evidence,
            bibliography=selected_bibliography,
            datasets=selected_datasets,
            glossary=blueprint.glossary,
            requirements=requirements,
            dependency_conclusions={
                item: value for item, value in (dependency_conclusions or {}).items() if item in section.depends_on
            },
        )


__all__ = ["ContextBuilder", "SectionContext"]
