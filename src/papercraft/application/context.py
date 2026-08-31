"""Build a least-privilege generation context for one manuscript section."""

from __future__ import annotations

from dataclasses import dataclass

from papercraft.domain import (
    BibliographyEntry,
    Claim,
    ClaimStatus,
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
        selected_claims = [
            item
            for item in claims
            if (item.id in section.required_claim_ids or item.section_id == section.id)
            and item.status == ClaimStatus.SUPPORTED
        ]
        if not selected_claims and claims:
            selected_claims = [item for item in claims if item.status == ClaimStatus.SUPPORTED]
        evidence_ids = {item_id for claim in selected_claims for item_id in claim.evidence_ids}
        selected_evidence = [item for item in evidence if item.id in evidence_ids and item.verified]
        # A fresh web verification can deduplicate a new URL record into an
        # existing canonical bibliography entry.  Select that entry by the
        # evidence binding rather than only by source_id; otherwise a writer
        # could cite the discarded duplicate and later fail citation audit.
        evidence_bibliography_ids = {
            str(item.metadata.get("bibliography_entry_id") or "")
            for item in selected_evidence
            if str(item.metadata.get("bibliography_entry_id") or "")
        }
        sources_without_bibliography_binding = {
            item.source_id
            for item in selected_evidence
            if not str(item.metadata.get("bibliography_entry_id") or "")
        }
        selected_sources = sources_without_bibliography_binding | set(section.source_ids)
        selected_bibliography = [
            item
            for item in bibliography
            if item.id in evidence_bibliography_ids or item.source_id in selected_sources
        ]
        if not selected_bibliography and bibliography:
            selected_bibliography = list(bibliography)

        target_dataset_ids = {
            vr.dataset_id for vr in section.visual_requests if vr.dataset_id
        } | set(section.required_fact_ids)
        selected_datasets = [
            item for item in datasets
            if not target_dataset_ids or item.id in target_dataset_ids or item.name in target_dataset_ids
        ]
        if not selected_datasets and datasets:
            selected_datasets = list(datasets)
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
