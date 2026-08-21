"""Deterministic, offline gates that must pass before a run can succeed."""

from __future__ import annotations

import math
import re
import zipfile
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
    RequirementSet,
    TableBlock,
)
from papercraft.infrastructure.calculations import FactLedger, FactLedgerError


@dataclass(frozen=True, slots=True)
class QAGateContext:
    project_id: str
    run_id: str
    manuscript: Manuscript
    facts: Sequence[FactRecord] = ()
    datasets: Sequence[Dataset] = ()
    claims: Sequence[Claim] = ()
    evidence: Sequence[Evidence] = ()
    citations: Sequence[Citation] = ()
    requirements: RequirementSet | None = None
    artifact_paths: Mapping[str, str | Path] = field(default_factory=dict)
    docx_path: str | Path | None = None
    pdf_path: str | Path | None = None


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
        self._check_datasets(context, issues, metrics)
        self._check_evidence_and_citations(context, flattened, issues, metrics)
        self._check_requirements(context, flattened, words, issues)
        self._check_docx(context.docx_path, issues, metrics)
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
            summary=summary,
            metadata={"gate_version": 1, "deterministic": True},
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

        for citation in context.citations:
            if citation.claim_id and citation.claim_id not in claims:
                issues.append(self._issue(QASeverity.ERROR, "citation_claim", f"Citation {citation.id} references missing claim"))
            if citation.evidence_id and citation.evidence_id not in evidence:
                issues.append(self._issue(QASeverity.ERROR, "citation_evidence", f"Citation {citation.id} references missing evidence"))
            if citation.bibliography_entry_id not in bibliography:
                issues.append(self._issue(QASeverity.BLOCKER, "citation_bibliography", f"Citation {citation.id} references missing bibliography entry"))

        referenced_citations: set[str] = set()
        for block in blocks:
            if isinstance(block, ParagraphBlock):
                referenced_citations.update(block.citation_ids)
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

    def _check_docx(
        self, raw_path: str | Path | None, issues: list[QAIssue], metrics: list[Metric]
    ) -> None:
        if raw_path is None:
            return
        path = Path(raw_path)
        if not path.is_file():
            issues.append(self._issue(QASeverity.BLOCKER, "docx", f"DOCX does not exist: {path}"))
            return
        try:
            with zipfile.ZipFile(path) as archive:
                corrupt = archive.testzip()
                names = set(archive.namelist())
                document_xml = archive.read("word/document.xml")
                settings_xml = archive.read("word/settings.xml")
                header_xml = b"".join(
                    archive.read(name)
                    for name in sorted(names)
                    if name.startswith("word/header") and name.endswith(".xml")
                )
                footer_xml = b"".join(
                    archive.read(name)
                    for name in sorted(names)
                    if name.startswith("word/footer") and name.endswith(".xml")
                )
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            issues.append(self._issue(QASeverity.BLOCKER, "docx", f"DOCX is invalid: {exc}"))
            return
        if corrupt:
            issues.append(self._issue(QASeverity.BLOCKER, "docx", f"DOCX contains corrupt member {corrupt}"))
        required = {"[Content_Types].xml", "word/document.xml", "word/styles.xml"}
        if missing := required - names:
            issues.append(self._issue(QASeverity.BLOCKER, "docx", f"DOCX lacks required members: {sorted(missing)}"))
        if b"TOC " not in document_xml:
            issues.append(self._issue(QASeverity.WARNING, "docx_toc", "DOCX has no dynamic TOC field"))
        if b"updateFields" not in settings_xml:
            issues.append(self._issue(QASeverity.WARNING, "docx_fields", "DOCX does not request field updates"))
        if b"PAGE" not in header_xml + footer_xml:
            issues.append(self._issue(QASeverity.WARNING, "docx_pagination", "DOCX has no dynamic PAGE field"))
        metrics.append(Metric(name="docx_size", value=float(path.stat().st_size), unit="bytes"))

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
