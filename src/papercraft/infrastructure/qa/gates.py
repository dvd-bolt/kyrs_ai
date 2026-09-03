"""Deterministic, offline gates that must pass before a run can succeed."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Generator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from papercraft.domain import (
    AppendixBlock,
    ChartBlock,
    Citation,
    CitationBlock,
    Claim,
    ClaimStatus,
    CodeListingBlock,
    Dataset,
    DataType,
    DiagramBlock,
    Evidence,
    FactOrigin,
    FactRecord,
    FigureBlock,
    HeadingBlock,
    Manuscript,
    Metric,
    ParagraphBlock,
    QAIssue,
    QAReport,
    QASeverity,
    RequirementCategory,
    RequirementCoverageReport,
    RequirementSet,
    Source,
    SourceSnapshot,
    TableBlock,
)
from papercraft.infrastructure.calculations import FactLedger, FactLedgerError
from papercraft.profiles import WorkProfile

from .document import DocumentInspectionError, inspect_docx_package


@dataclass(frozen=True, slots=True)
class QAGateContext:
    project_id: str
    run_id: str
    manuscript: Manuscript
    profile: WorkProfile
    facts: Sequence[FactRecord] = ()
    datasets: Sequence[Dataset] = ()
    claims: Sequence[Claim] = ()
    evidence: Sequence[Evidence] = ()
    citations: Sequence[Citation] = ()
    sources: Sequence[Source] = ()
    source_snapshots: Sequence[SourceSnapshot] = ()
    requirements: RequirementSet | None = None
    requirement_coverage: RequirementCoverageReport | None = None
    artifact_paths: Mapping[str, str | Path] = field(default_factory=dict)
    docx_path: str | Path | None = None
    pdf_path: str | Path | None = None
    input_hash: str | None = None
    expected_manuscript_hash: str | None = None
    expected_docx_hash: str | None = None
    docx_finalized: bool = False


class DeterministicQualityGate:
    PLACEHOLDER_PATTERN = re.compile(
        r"(?i)(\bTODO\b|\bTBD\b|lorem\s+ipsum|\[\[?\s*(?:missing|placeholder|вставить|ошибка)|"
        r"\[(?:здесь|сюда)\s+(?:будет|вставить)|место\s+для\s+вставки)"
    )
    DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)

    def run(self, context: QAGateContext) -> QAReport:
        issues: list[QAIssue] = []
        metrics: list[Metric] = []
        if context.manuscript.project_id != context.project_id:
            issues.append(self._issue(QASeverity.BLOCKER, "identity", "Manuscript belongs to another project"))

        flattened = list(_flatten_blocks(context.manuscript.blocks))
        words = sum(len(_words(_block_text(block))) for block in flattened)
        metrics.extend(
            [
                Metric(name="manuscript_blocks", value=float(len(flattened)), unit="blocks"),
                Metric(name="word_count", value=float(words), unit="words"),
            ]
        )
        self._check_manuscript(context, flattened, words, issues)
        self._check_facts(context, issues)
        self._check_numeric_provenance(context, flattened, issues)
        self._check_datasets(context, issues, metrics)
        self._check_evidence_and_citations(context, flattened, issues, metrics)
        self._check_requirements(context, flattened, words, issues)
        self._check_requirement_coverage(context, issues)
        self._check_profile_compliance(context, flattened, issues)
        docx_hash = self._check_docx(context, flattened, issues, metrics)
        self._check_pdf(context.pdf_path, issues, metrics)

        counts = {
            severity.value: sum(1 for issue in issues if issue.severity == severity and not issue.resolved)
            for severity in QASeverity
        }
        summary = (
            f"Deterministic QA completed: {len(issues)} issue(s); "
            + ", ".join(f"{name}={count}" for name, count in counts.items() if count)
        ).rstrip("; ")
        return QAReport(
            project_id=context.project_id,
            run_id=context.run_id,
            issues=issues,
            metrics=metrics,
            requirement_coverage=context.requirement_coverage,
            summary=summary,
            metadata={
                "gate_version": 2,
                "deterministic": True,
                "release_hashes": {
                    "input_hash": context.input_hash,
                    "manuscript_hash": _stable_hash(
                        context.manuscript.model_dump(mode="json")
                    ),
                    "docx_hash": docx_hash,
                    "pdf_hash": _file_sha256(context.pdf_path),
                },
            },
        )

    def _check_manuscript(
        self,
        context: QAGateContext,
        blocks: list[Any],
        words: int,
        issues: list[QAIssue],
    ) -> None:
        if not blocks:
            issues.append(self._issue(QASeverity.BLOCKER, "manuscript", "Manuscript has no blocks"))
            return
        if words == 0:
            issues.append(self._issue(QASeverity.ERROR, "manuscript", "Manuscript has no textual content"))
        ids = [getattr(block, "id", "") for block in blocks]
        duplicates = sorted({block_id for block_id in ids if block_id and ids.count(block_id) > 1})
        if duplicates:
            issues.append(
                self._issue(
                    QASeverity.BLOCKER,
                    "manuscript_ids",
                    f"Duplicate block ids: {', '.join(duplicates)}",
                )
            )

        previous_heading_level = 0
        datasets = {dataset.id: dataset for dataset in context.datasets}
        for block in blocks:
            text = _block_text(block)
            if text and self.PLACEHOLDER_PATTERN.search(text):
                excerpt = re.sub(r"\s+", " ", text).strip()[:120]
                issues.append(
                    self._issue(
                        QASeverity.BLOCKER,
                        "placeholder",
                        f"Unresolved placeholder in block {block.id}: {excerpt}",
                        metadata={"block_id": block.id},
                    )
                )
            if isinstance(block, HeadingBlock):
                if previous_heading_level and block.level > previous_heading_level + 1:
                    issues.append(
                        self._issue(
                            QASeverity.WARNING,
                            "heading_hierarchy",
                            f"Heading {block.id} jumps from level {previous_heading_level} to {block.level}",
                            metadata={"block_id": block.id},
                        )
                    )
                previous_heading_level = block.level
            if isinstance(block, TableBlock):
                if block.spec.dataset_id and block.spec.dataset_id not in datasets:
                    issues.append(
                        self._issue(
                            QASeverity.BLOCKER,
                            "missing_dataset",
                            f"Table {block.id} references missing dataset {block.spec.dataset_id}",
                        )
                    )
                if not block.spec.rows and not block.spec.dataset_id:
                    issues.append(
                        self._issue(QASeverity.ERROR, "empty_table", f"Table {block.id} has no data")
                    )
            if isinstance(block, (ChartBlock, DiagramBlock, FigureBlock)):
                artifact_id = getattr(block, "artifact_id", None)
                if not artifact_id:
                    issues.append(
                        self._issue(
                            QASeverity.BLOCKER,
                            "missing_artifact",
                            f"Visual block {block.id} has no rendered artifact",
                        )
                    )
                else:
                    raw_path = context.artifact_paths.get(artifact_id)
                    if raw_path is None or not Path(raw_path).is_file():
                        issues.append(
                            self._issue(
                                QASeverity.BLOCKER,
                                "missing_artifact",
                                f"Artifact {artifact_id} for block {block.id} does not exist",
                                metadata={"artifact_id": artifact_id, "block_id": block.id},
                            )
                        )

    def _check_facts(self, context: QAGateContext, issues: list[QAIssue]) -> None:
        ids: set[str] = set()
        for fact in context.facts:
            if fact.id in ids:
                issues.append(self._issue(QASeverity.BLOCKER, "fact_id", f"Duplicate fact id {fact.id}"))
            ids.add(fact.id)
            if fact.project_id != context.project_id:
                issues.append(self._issue(QASeverity.ERROR, "fact_project", f"Fact {fact.id} belongs to another project"))
            try:
                FactLedger.validate_provenance(fact)
            except FactLedgerError as exc:
                issues.append(self._issue(QASeverity.BLOCKER, "fact_provenance", str(exc)))
            if isinstance(fact.value, float) and not math.isfinite(fact.value):
                issues.append(self._issue(QASeverity.BLOCKER, "fact_value", f"Fact {fact.id} is non-finite"))
        if context.facts:
            try:
                ledger = FactLedger(context.project_id, context.facts)
            except FactLedgerError as exc:
                issues.append(self._issue(QASeverity.BLOCKER, "fact_ledger", str(exc)))
            else:
                for violation in ledger.validate_constraints():
                    issues.append(self._issue(QASeverity.ERROR, "fact_constraint", violation))

    def _check_numeric_provenance(
        self, context: QAGateContext, blocks: list[Any], issues: list[QAIssue]
    ) -> None:
        fact_ids = {fact.id for fact in context.facts}
        dataset_ids = {dataset.id for dataset in context.datasets}
        for block in blocks:
            if isinstance(block, ParagraphBlock):
                numbers = _extract_empirical_numbers(block.text)
                if not numbers:
                    continue
                unknown = set(block.numeric_fact_ids) - fact_ids
                if unknown:
                    issues.append(self._issue(QASeverity.BLOCKER, "numeric_fact", f"Paragraph {block.id} references unknown facts: {sorted(unknown)}"))
                elif not block.numeric_fact_ids:
                    issues.append(self._issue(QASeverity.ERROR, "numeric_provenance", f"Paragraph {block.id} has numbers without FactLedger provenance"))
                continue

            # Every inline numeric table needs a verified local provenance
            # route.  This applies equally to generated and user-authored
            # blocks; a model can generate literal cell values without a
            # Dataset. Text-only tables remain freely editable.
            if not isinstance(block, TableBlock):
                continue
            if not _table_has_numeric_values(block):
                continue
            unknown = set(block.numeric_fact_ids) - fact_ids
            if unknown:
                issues.append(
                    self._issue(
                        QASeverity.BLOCKER,
                        "numeric_fact",
                        f"Table {block.id} references unknown facts: {sorted(unknown)}",
                    )
                )
            if block.spec.dataset_id:
                if block.spec.dataset_id not in dataset_ids:
                    issues.append(
                        self._issue(
                            QASeverity.BLOCKER,
                            "numeric_dataset",
                            f"Table {block.id} references unknown dataset: {block.spec.dataset_id}",
                        )
                    )
                continue
            if not block.numeric_fact_ids:
                issues.append(
                    self._issue(
                        QASeverity.ERROR,
                        "numeric_provenance",
                        f"Table {block.id} has numbers without dataset or FactLedger provenance",
                    )
                )

    def _check_datasets(
        self,
        context: QAGateContext,
        issues: list[QAIssue],
        metrics: list[Metric],
    ) -> None:
        seen: set[str] = set()
        row_total = 0
        for dataset in context.datasets:
            row_total += len(dataset.rows)
            if dataset.id in seen:
                issues.append(self._issue(QASeverity.BLOCKER, "dataset_id", f"Duplicate dataset id {dataset.id}"))
            seen.add(dataset.id)
            if dataset.project_id != context.project_id:
                issues.append(self._issue(QASeverity.ERROR, "dataset_project", f"Dataset {dataset.id} belongs to another project"))
            if dataset.origin == FactOrigin.VERIFIED_SOURCE and not dataset.source_ids:
                issues.append(self._issue(QASeverity.BLOCKER, "dataset_provenance", f"Verified dataset {dataset.id} has no source"))
            if dataset.origin == FactOrigin.SYNTHETIC and (
                dataset.synthetic_seed is None or not dataset.generation_method
            ):
                issues.append(self._issue(QASeverity.BLOCKER, "dataset_provenance", f"Synthetic dataset {dataset.id} lacks seed or method"))
            columns = {column.name: column for column in dataset.columns}
            for row_index, row in enumerate(dataset.rows):
                for name, column in columns.items():
                    value = row.get(name)
                    if value is None and not column.nullable:
                        issues.append(self._issue(QASeverity.ERROR, "dataset_null", f"Dataset {dataset.id}, row {row_index}, column {name} is null"))
                        continue
                    if value is not None and not _matches_type(value, column.data_type):
                        issues.append(self._issue(QASeverity.ERROR, "dataset_type", f"Dataset {dataset.id}, row {row_index}, column {name} has the wrong type"))
                    if isinstance(value, float) and not math.isfinite(value):
                        issues.append(self._issue(QASeverity.BLOCKER, "dataset_value", f"Dataset {dataset.id} contains a non-finite value"))
        metrics.append(Metric(name="dataset_rows", value=float(row_total), unit="rows"))

    def _check_evidence_and_citations(
        self,
        context: QAGateContext,
        blocks: list[Any],
        issues: list[QAIssue],
        metrics: list[Metric],
    ) -> None:
        evidence = {item.id: item for item in context.evidence}
        claims = {claim.id: claim for claim in context.claims}
        citations = {citation.id: citation for citation in context.citations}
        bibliography = {entry.id: entry for entry in context.manuscript.bibliography}
        sources = {source.id: source for source in context.sources}
        snapshots = {snapshot.id: snapshot for snapshot in context.source_snapshots}

        for snapshot in context.source_snapshots:
            source = sources.get(snapshot.source_id)
            if source is None:
                issues.append(self._issue(QASeverity.BLOCKER, "snapshot_source", f"Snapshot {snapshot.id} references missing source"))
                continue
            path = Path(snapshot.stored_path)
            if not path.is_file():
                issues.append(self._issue(QASeverity.BLOCKER, "snapshot_file", f"Snapshot {snapshot.id} file is missing"))
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() != snapshot.sha256:
                issues.append(self._issue(QASeverity.BLOCKER, "snapshot_hash", f"Snapshot {snapshot.id} hash does not match"))
            if source.sha256 != snapshot.sha256:
                issues.append(self._issue(QASeverity.BLOCKER, "snapshot_hash", f"Source {source.id} is not bound to snapshot {snapshot.id}"))

        for claim in context.claims:
            if claim.project_id != context.project_id:
                issues.append(self._issue(QASeverity.ERROR, "claim_project", f"Claim {claim.id} belongs to another project"))
            if claim.checkable and (claim.status != ClaimStatus.SUPPORTED or not claim.evidence_ids):
                issues.append(self._issue(QASeverity.BLOCKER, "unsupported_claim", f"Checkable claim {claim.id} is not supported"))
            for evidence_id in claim.evidence_ids:
                item = evidence.get(evidence_id)
                if item is None:
                    issues.append(self._issue(QASeverity.BLOCKER, "missing_evidence", f"Claim {claim.id} references missing evidence {evidence_id}"))
                elif not item.verified or not item.supports:
                    issues.append(self._issue(QASeverity.BLOCKER, "unverified_evidence", f"Evidence {evidence_id} is not verified support"))
                elif item.source_id not in sources:
                    issues.append(self._issue(QASeverity.BLOCKER, "evidence_source", f"Evidence {evidence_id} references missing source"))
                elif sources[item.source_id].metadata.get("remote_url") and (
                    not item.snapshot_id or item.snapshot_id not in snapshots
                ):
                    issues.append(self._issue(QASeverity.BLOCKER, "evidence_snapshot", f"Web evidence {evidence_id} has no verified snapshot"))

        for citation in context.citations:
            if citation.claim_id and citation.claim_id not in claims:
                issues.append(self._issue(QASeverity.ERROR, "citation_claim", f"Citation {citation.id} references missing claim"))
            if citation.evidence_id and citation.evidence_id not in evidence:
                issues.append(self._issue(QASeverity.ERROR, "citation_evidence", f"Citation {citation.id} references missing evidence"))
            if citation.bibliography_entry_id not in bibliography:
                issues.append(self._issue(QASeverity.BLOCKER, "citation_bibliography", f"Citation {citation.id} references missing bibliography entry"))
            if citation.evidence_id and citation.evidence_id in evidence:
                item = evidence[citation.evidence_id]
                if citation.claim_id and item.claim_id != citation.claim_id:
                    issues.append(self._issue(QASeverity.BLOCKER, "citation_binding", f"Citation {citation.id} claim/evidence binding is inconsistent"))
                entry = bibliography.get(citation.bibliography_entry_id)
                if entry is not None and entry.source_id != item.source_id:
                    issues.append(self._issue(QASeverity.BLOCKER, "citation_binding", f"Citation {citation.id} evidence/source/bibliography binding is inconsistent"))

        referenced_citations: set[str] = set()
        for block in blocks:
            if isinstance(block, ParagraphBlock):
                referenced_citations.update(block.citation_ids)
                if bool(block.metadata.get("user_override")):
                    raw_claim_ids = block.metadata.get("claim_ids")
                    raw_entry_ids = block.metadata.get("bibliography_entry_ids")
                    claim_ids = raw_claim_ids if isinstance(raw_claim_ids, list) else []
                    entry_ids = raw_entry_ids if isinstance(raw_entry_ids, list) else []
                    if (
                        bool(block.metadata.get("evidence_review_required"))
                        or not claim_ids
                        or not entry_ids
                        or not block.citation_ids
                    ):
                        issues.append(
                            self._issue(
                                QASeverity.BLOCKER,
                                "user_edit_evidence",
                                "User-edited paragraph has no verified evidence binding",
                                metadata={"block_id": block.id},
                            )
                        )
            elif isinstance(block, CitationBlock):
                referenced_citations.add(block.citation_id)
        for citation_id in referenced_citations:
            if citation_id not in citations:
                issues.append(self._issue(QASeverity.BLOCKER, "missing_citation", f"Manuscript references missing citation {citation_id}"))

        for entry in context.manuscript.bibliography:
            if entry.doi and not self.DOI_PATTERN.match(entry.doi):
                issues.append(self._issue(QASeverity.ERROR, "invalid_doi", f"Bibliography entry {entry.id} has an invalid DOI"))
            if entry.url:
                parsed = urlparse(entry.url)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    issues.append(self._issue(QASeverity.ERROR, "invalid_url", f"Bibliography entry {entry.id} has an invalid URL"))
        supported = sum(1 for claim in context.claims if claim.status == ClaimStatus.SUPPORTED)
        ratio = supported / len(context.claims) * 100 if context.claims else 100.0
        metrics.append(Metric(name="supported_claims", value=ratio, unit="percent", target=100, passed=ratio == 100))

    def _check_requirements(
        self,
        context: QAGateContext,
        blocks: list[Any],
        words: int,
        issues: list[QAIssue],
    ) -> None:
        requirements = context.requirements
        if requirements is None:
            return
        unresolved = [conflict for conflict in requirements.conflicts if not conflict.resolved_rule_id]
        for conflict in unresolved:
            issues.append(self._issue(QASeverity.BLOCKER, "requirement_conflict", f"Requirement conflict {conflict.id} is unresolved"))
        headings = [block.text.casefold() for block in blocks if isinstance(block, HeadingBlock)]
        for rule in requirements.rules:
            if not rule.mandatory:
                continue
            if rule.category == RequirementCategory.VOLUME and rule.key in {"minimum_words", "min_words"}:
                minimum = _safe_int(rule.value)
                if minimum is not None and words < minimum:
                    issues.append(self._issue(QASeverity.ERROR, "word_count", f"Word count {words} is below required {minimum}", requirement_rule_id=rule.id))
            if rule.category == RequirementCategory.VOLUME and rule.key in {"maximum_words", "max_words"}:
                maximum = _safe_int(rule.value)
                if maximum is not None and words > maximum:
                    issues.append(self._issue(QASeverity.ERROR, "word_count", f"Word count {words} exceeds required {maximum}", requirement_rule_id=rule.id))
            if rule.category == RequirementCategory.STRUCTURE and rule.key == "required_heading":
                required = str(rule.value).casefold()
                if required not in headings:
                    issues.append(self._issue(QASeverity.ERROR, "required_heading", f"Required heading is missing: {rule.value}", requirement_rule_id=rule.id))

    def _check_requirement_coverage(
        self, context: QAGateContext, issues: list[QAIssue]
    ) -> None:
        report = context.requirement_coverage
        if report is None:
            return
        if report.project_id != context.project_id:
            issues.append(
                self._issue(
                    QASeverity.BLOCKER,
                    "requirement_coverage_identity",
                    "Requirement coverage report belongs to another project",
                )
            )
            return

        requirements = context.requirements
        if requirements is not None:
            if report.requirement_set_id != requirements.id:
                issues.append(
                    self._issue(
                        QASeverity.BLOCKER,
                        "requirement_coverage_requirements",
                        "Requirement coverage report belongs to another requirement set",
                    )
                )
                return
            expected_rule_ids = {rule.id for rule in requirements.rules}
            reported_rule_ids = {entry.requirement_rule_id for entry in report.entries}
            missing_rule_ids = sorted(expected_rule_ids - reported_rule_ids)
            unknown_rule_ids = sorted(reported_rule_ids - expected_rule_ids)
            if missing_rule_ids:
                issues.append(
                    self._issue(
                        QASeverity.BLOCKER,
                        "requirement_coverage_incomplete",
                        "Coverage report omits requirement rules: " + ", ".join(missing_rule_ids),
                        metadata={"requirement_rule_ids": missing_rule_ids},
                    )
                )
            if unknown_rule_ids:
                issues.append(
                    self._issue(
                        QASeverity.BLOCKER,
                        "requirement_coverage_unknown_rule",
                        "Coverage report contains unknown requirement rules: "
                        + ", ".join(unknown_rule_ids),
                        metadata={"requirement_rule_ids": unknown_rule_ids},
                    )
                )

        for entry in sorted(report.entries, key=lambda item: item.requirement_rule_id):
            metadata = {
                "coverage_status": entry.status,
                "block_ids": entry.block_ids,
                "pdf_page_mappings": [item.model_dump(mode="json") for item in entry.pdf_page_mappings],
                "artifact_id": entry.artifact_id,
            }
            if entry.criticality == "critical" and entry.status != "covered":
                issues.append(
                    self._issue(
                        QASeverity.BLOCKER,
                        "requirement_coverage",
                        f"Critical requirement {entry.requirement_key} is {entry.status}",
                        requirement_rule_id=entry.requirement_rule_id,
                        metadata=metadata,
                    )
                )
            if (
                entry.criticality == "critical"
                and entry.status == "covered"
                and not entry.has_coverage_location
            ):
                issues.append(
                    self._issue(
                        QASeverity.BLOCKER,
                        "requirement_coverage_traceability",
                        f"Critical requirement {entry.requirement_key} is covered without a block or artifact location",
                        requirement_rule_id=entry.requirement_rule_id,
                        metadata=metadata,
                    )
                )
            for gap in entry.evidence_gaps:
                issues.append(
                    self._issue(
                        QASeverity.BLOCKER,
                        "requirement_evidence_gap",
                        f"Requirement {entry.requirement_key} has an evidence gap: {gap}",
                        requirement_rule_id=entry.requirement_rule_id,
                        metadata={**metadata, "evidence_gap": gap},
                    )
                )

    def _check_profile_compliance(
        self, context: QAGateContext, blocks: list[Any], issues: list[QAIssue]
    ) -> None:
        profile = context.profile
        min_sources = profile.policy.minimum_sources
        if min_sources > 0 and len(context.manuscript.bibliography) < min_sources:
            issues.append(
                self._issue(
                    QASeverity.ERROR,
                    "profile_minimum_sources",
                    f"Manuscript has {len(context.manuscript.bibliography)} sources; profile requires at least {min_sources}",
                )
            )

        cited_entries: set[str] = set()
        for citation in context.citations:
            if citation.bibliography_entry_id:
                cited_entries.add(citation.bibliography_entry_id)
        for block in blocks:
            if isinstance(block, ParagraphBlock):
                raw_entries = block.metadata.get("bibliography_entry_ids")
                if isinstance(raw_entries, list):
                    cited_entries.update(str(e) for e in raw_entries if e)
        for entry in context.manuscript.bibliography:
            if entry.id not in cited_entries:
                issues.append(
                    self._issue(
                        QASeverity.BLOCKER,
                        "uncited_bibliography_entry",
                        f"Bibliography entry {entry.id} ({entry.title[:40]}) is never cited in the manuscript text",
                    )
                )

        has_synthetic = any(ds.origin == FactOrigin.SYNTHETIC for ds in context.datasets)
        if has_synthetic:
            full_text = " ".join(_block_text(b) for b in blocks).casefold()
            if not any(term in full_text for term in ("синтетическ", "модельн", "демонстрационн", "synthetic", "генеративн")):
                issues.append(
                    self._issue(
                        QASeverity.BLOCKER,
                        "synthetic_data_disclosure",
                        "Project uses synthetic data but manuscript lacks explicit synthetic demonstration disclosure",
                    )
                )

    def _check_docx(
        self,
        context: QAGateContext,
        blocks: list[Any],
        issues: list[QAIssue],
        metrics: list[Metric],
    ) -> str | None:
        if context.docx_path is None:
            return None
        path = Path(context.docx_path)
        try:
            inspection = inspect_docx_package(path)
        except DocumentInspectionError:
            issues.append(
                self._issue(
                    QASeverity.BLOCKER,
                    "docx",
                    "DOCX is not a valid release package",
                )
            )
            return None

        if context.expected_docx_hash and inspection.sha256 != context.expected_docx_hash:
            issues.append(
                self._issue(
                    QASeverity.BLOCKER,
                    "docx_hash",
                    "DOCX hash does not match the release artifact",
                )
            )
        manuscript_hash = _stable_hash(context.manuscript.model_dump(mode="json"))
        if (
            context.expected_manuscript_hash
            and manuscript_hash != context.expected_manuscript_hash
        ):
            issues.append(
                self._issue(
                    QASeverity.BLOCKER,
                    "manuscript_hash",
                    "Manuscript hash changed before release QA",
                )
            )
        if inspection.forbidden_parts:
            issues.append(
                self._issue(
                    QASeverity.BLOCKER,
                    "docx_active_content",
                    "DOCX contains macros, embedded objects, or active content",
                )
            )
        if inspection.external_relationships or inspection.active_fields:
            issues.append(
                self._issue(
                    QASeverity.BLOCKER,
                    "docx_external_link",
                    "DOCX contains an external relationship or active external field",
                )
            )
        required_styles = {"Normal"}
        if any(isinstance(block, HeadingBlock) for block in blocks):
            required_styles.add("Heading 1")
        if any(isinstance(block, (TableBlock, ChartBlock, DiagramBlock, FigureBlock)) for block in blocks):
            required_styles.add("Caption")
        available_styles = {style.casefold().replace(" ", "") for style in inspection.styles}
        if missing_styles := {
            style
            for style in required_styles
            if style.casefold().replace(" ", "") not in available_styles
        }:
            issues.append(
                self._issue(
                    QASeverity.BLOCKER,
                    "docx_styles",
                    "DOCX lacks required styles: " + ", ".join(sorted(missing_styles)),
                )
            )
        field_codes = "\n".join(inspection.field_codes).upper()
        if not inspection.update_fields_on_open:
            issues.append(
                self._issue(
                    QASeverity.BLOCKER,
                    "docx_fields",
                    "DOCX does not request field updates on open",
                )
            )
        if "PAGE" not in field_codes:
            issues.append(
                self._issue(QASeverity.BLOCKER, "docx_pagination", "DOCX has no PAGE field")
            )
        if any(isinstance(block, TableBlock) for block in blocks) and "SEQ TABLE" not in field_codes:
            issues.append(
                self._issue(QASeverity.BLOCKER, "docx_fields", "DOCX table numbering field is missing")
            )
        if any(isinstance(block, (ChartBlock, DiagramBlock, FigureBlock)) for block in blocks) and "SEQ FIGURE" not in field_codes:
            issues.append(
                self._issue(QASeverity.BLOCKER, "docx_fields", "DOCX figure numbering field is missing")
            )
        expected_tables = sum(isinstance(block, TableBlock) for block in blocks)
        if inspection.table_count < expected_tables or inspection.malformed_tables:
            issues.append(
                self._issue(QASeverity.BLOCKER, "docx_tables", "DOCX table structure is incomplete")
            )
        expected_images = len(
            {
                str(getattr(block, "artifact_id", ""))
                for block in blocks
                if isinstance(block, (ChartBlock, DiagramBlock, FigureBlock))
                and getattr(block, "artifact_id", None)
            }
        )
        if inspection.image_count < expected_images or inspection.invalid_images:
            issues.append(
                self._issue(QASeverity.BLOCKER, "docx_images", "DOCX image parts are missing or invalid")
            )
        if context.manuscript.bibliography:
            normalized_text = " ".join(inspection.visible_text.casefold().split())
            missing_entries = [
                entry.title
                for entry in context.manuscript.bibliography
                if self._normalized(entry.title) not in normalized_text
            ]
            if missing_entries:
                issues.append(
                    self._issue(
                        QASeverity.BLOCKER,
                        "docx_bibliography",
                        "DOCX bibliography is missing expected entries",
                    )
                )
        if context.docx_finalized and "обновите оглавление" in inspection.visible_text.casefold():
            issues.append(
                self._issue(
                    QASeverity.BLOCKER,
                    "docx_fields",
                    "DOCX still contains an unupdated field placeholder",
                )
            )
        metrics.append(Metric(name="docx_size", value=float(path.stat().st_size), unit="bytes"))
        metrics.extend(
            [
                Metric(name="docx_tables", value=float(inspection.table_count), unit="tables"),
                Metric(name="docx_images", value=float(inspection.image_count), unit="images"),
            ]
        )
        return inspection.sha256

    @staticmethod
    def _normalized(value: str) -> str:
        return " ".join(value.casefold().split())

    def _check_pdf(
        self, raw_path: str | Path | None, issues: list[QAIssue], metrics: list[Metric]
    ) -> None:
        if raw_path is None:
            return
        path = Path(raw_path)
        if not path.is_file():
            issues.append(self._issue(QASeverity.BLOCKER, "pdf", f"PDF does not exist: {path}"))
            return
        try:
            with path.open("rb") as stream:
                header = stream.read(5)
                stream.seek(max(0, path.stat().st_size - 1024))
                trailer = stream.read()
        except OSError as exc:
            issues.append(self._issue(QASeverity.BLOCKER, "pdf", f"PDF cannot be read: {exc}"))
            return
        if header != b"%PDF-" or b"%%EOF" not in trailer:
            issues.append(self._issue(QASeverity.BLOCKER, "pdf", "PDF header or EOF marker is invalid"))
            return
        try:
            from pypdf import PdfReader

            reader = PdfReader(path, strict=False)
            page_count = len(reader.pages)
            if page_count == 0:
                issues.append(self._issue(QASeverity.BLOCKER, "pdf", "PDF has no pages"))
            metrics.append(Metric(name="pdf_pages", value=float(page_count), unit="pages"))
        except Exception as exc:
            issues.append(self._issue(QASeverity.ERROR, "pdf", f"PDF structure check failed: {exc}"))

    @staticmethod
    def _issue(
        severity: QASeverity,
        category: str,
        message: str,
        *,
        requirement_rule_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> QAIssue:
        return QAIssue(
            severity=severity,
            category=category,
            message=message,
            requirement_rule_id=requirement_rule_id,
            metadata=metadata or {},
        )


def _flatten_blocks(blocks: Sequence[Any]) -> Generator[Any, None, None]:
    for block in blocks:
        yield block
        if isinstance(block, AppendixBlock):
            yield from _flatten_blocks(block.blocks)


def _block_text(block: Any) -> str:
    if isinstance(block, (ParagraphBlock, HeadingBlock)):
        return block.text
    if isinstance(block, CitationBlock):
        return block.text
    if isinstance(block, CodeListingBlock):
        return f"{block.caption}\n{block.code}"
    if isinstance(block, TableBlock):
        return " ".join([block.spec.caption, *block.spec.headers, *(str(value) for row in block.spec.rows for value in row)])
    if isinstance(block, (ChartBlock, DiagramBlock)):
        return block.spec.title
    if isinstance(block, FigureBlock):
        return block.caption
    return ""


def _table_has_numeric_values(block: TableBlock) -> bool:
    """Return whether an inline table carries a numeric factual value.

    Only row values are inspected. A caption such as ``Table 1`` is a layout
    label rather than a claim, whereas a number inside a cell needs a dataset
    or FactLedger provenance.
    """

    return any(_json_value_has_number(value) for row in block.spec.rows for value in row)


def _json_value_has_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return bool(re.search(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?", value))
    if isinstance(value, Mapping):
        return any(_json_value_has_number(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_json_value_has_number(item) for item in value)
    return False


def _words(text: str) -> list[str]:
    return re.findall(r"[\w-]+", text, flags=re.UNICODE)


def _matches_type(value: Any, data_type: DataType) -> bool:
    if data_type == DataType.STRING:
        return isinstance(value, str)
    if data_type == DataType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if data_type == DataType.NUMBER:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if data_type == DataType.BOOLEAN:
        return isinstance(value, bool)
    if data_type in {DataType.DATE, DataType.DATETIME}:
        return isinstance(value, str)
    return False


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_empirical_numbers(text: str) -> list[str]:
    cleaned = re.sub(r"\[\d+(?:\s*[,–-]\s*\d+)*\]", " ", text)
    cleaned = re.sub(r"(?i)\b(?:ГОСТ(?:\s+Р)?|ISO|IEC|IEEE|СанПиН|СНиП)\s+[\d\.\-]+", " ", cleaned)
    cleaned = re.sub(r"\b\d{1,2}[\./]\d{1,2}[\./]\d{2,4}\b", " ", cleaned)
    cleaned = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", cleaned)
    cleaned = re.sub(r"\b(19\d\d|20\d\d)(?:\s*[-–]\s*(?:19\d\d|20\d\d))?\s*(?:г\.|гг\.|год[а-я]*)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?i)\b(?:рис(?:\.|унк[а-я]*)?|табл(?:\.|иц[а-я]*)?|раздел[а-я]*|глав[а-я]*|пункт[а-я]*|п\.|ч\.|формул[а-я]*)\s*(?:\(\s*\d+\s*\)|\d+(?:\.\d+)*)", " ", cleaned)
    cleaned = re.sub(r"(?:^|\n|\.\s+)\d+[\.\)]\s+", " ", cleaned)
    return re.findall(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?", cleaned)


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(raw_path: str | Path | None) -> str | None:
    if raw_path is None:
        return None
    path = Path(raw_path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()
