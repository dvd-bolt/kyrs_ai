"""Validated domain models used by the PaperCraft autopilot.

Models are deliberately free of database and UI concerns.  Every model can be
round-tripped with ``model_dump(mode="json")`` and validated again, which makes
them suitable both for structured LLM output and durable checkpoints.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from .enums import (
    ArtifactKind,
    ChartType,
    ClaimStatus,
    DataType,
    DomainProfile,
    FactOrigin,
    QASeverity,
    QAStatus,
    QualityStatus,
    RequirementCategory,
    RequirementPriority,
    RunStatus,
    SourceRole,
    StageStatus,
    VisualKind,
    WorkType,
)


def new_id() -> str:
    """Return a URL- and filesystem-safe random identifier."""

    return uuid4().hex


def utc_now() -> datetime:
    return datetime.now(UTC)


class DomainModel(BaseModel):
    """Base class with strict, assignment-validated domain semantics."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class AutopilotOptions(DomainModel):
    checkpoint_requirements: bool = False
    checkpoint_outline: bool = False
    checkpoint_final_review: bool = False
    consent_to_remote_processing: bool = False
    maximum_cost: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    quality_mode: Literal["maximum", "balanced", "economy"] = "maximum"
    maximum_revision_cycles: int = Field(default=3, ge=1, le=10)
    # Synthetic data are an explicit, non-publishable demonstration fallback.
    allow_synthetic_data: bool = False
    # Legacy projects can still deserialize ``auto``/``word``.  The private
    # beta pipeline deliberately normalises all new and existing runs to
    # LibreOffice before finalization.
    preferred_finalizer: Literal["auto", "word", "libreoffice"] = "libreoffice"
    generate_pdf: bool = True
    generate_qa_report: bool = True

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class ScientificAuthor(DomainModel):
    name: str = Field(min_length=1)
    organization: str = Field(min_length=1)
    email: str | None = None
    orcid: str | None = None


class ScientificArticleSpec(DomainModel):
    kind: Literal["scientific_article"] = "scientific_article"
    material_type: str = Field(default="research_article", min_length=1)
    scientific_question: str = Field(default="", min_length=1)
    authors: list[ScientificAuthor] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    udc: str | None = None
    keywords: list[str] = Field(default_factory=list)
    method_description: str = ""
    data_requirements: str = ""
    funding_statement: str = ""
    conflict_of_interest_statement: str = ""


class CourseworkSpec(DomainModel):
    kind: Literal["coursework"] = "coursework"
    institution: str = ""
    programme: str = ""


class ReportSpec(DomainModel):
    kind: Literal["report"] = "report"
    organization: str = ""
    reporting_period: str = ""


class SchoolProjectSpec(DomainModel):
    kind: Literal["school_project"] = "school_project"
    school: str = ""
    grade: str = ""


class UniversalProjectSpec(DomainModel):
    kind: Literal["universal"] = "universal"


ProfileSpec = Annotated[
    ScientificArticleSpec | CourseworkSpec | ReportSpec | SchoolProjectSpec | UniversalProjectSpec,
    Field(discriminator="kind"),
]


class ProjectBrief(DomainModel):
    title: str = ""
    topic: str = ""
    prompt: str = ""
    work_type: WorkType = WorkType.COURSEWORK
    domain_profile: DomainProfile = DomainProfile.GENERAL
    language: str = "ru-RU"
    title_page: dict[str, JsonValue] = Field(default_factory=dict)
    profile_spec: ProfileSpec | None = None

    @model_validator(mode="after")
    def derive_title(self) -> ProjectBrief:
        if not self.title and self.topic:
            object.__setattr__(self, "title", self.topic)
        return self


class Project(DomainModel):
    id: str = Field(default_factory=new_id, min_length=1)
    brief: ProjectBrief = Field(default_factory=ProjectBrief)
    options: AutopilotOptions = Field(default_factory=AutopilotOptions)
    schema_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ProjectHealth(DomainModel):
    """Auditable local-project health report, independent of the UI."""

    project_id: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    integrity_ok: bool
    input_hash_valid: bool
    missing_artifact_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=utc_now)


class BackupRecord(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    automatic: bool = True
    label: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class MigrationRecord(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str | None = None
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)
    applied_at: datetime = Field(default_factory=utc_now)
    backup_id: str | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict)


class MigrationPlan(DomainModel):
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)
    steps: list[str] = Field(default_factory=list)


class MigrationResult(DomainModel):
    plan: MigrationPlan
    applied: bool
    records: list[MigrationRecord] = Field(default_factory=list)
    backup: BackupRecord | None = None


class RevisionRecord(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str = Field(min_length=1)
    kind: Literal["requirements", "blueprint", "manuscript", "datasets", "qa"]
    revision: int = Field(ge=1)
    object_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=utc_now)


class Locator(DomainModel):
    """Stable pointer to the evidence location inside an imported source."""

    source_id: str | None = None
    snapshot_id: str | None = None
    page: int | None = Field(default=None, ge=1)
    sheet: str | None = None
    cell_range: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    url: str | None = None
    section: str | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_line_range(self) -> Locator:
        if self.line_end is not None and self.line_start is None:
            raise ValueError("line_start is required when line_end is set")
        if self.line_start is not None and self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("line_end must be greater than or equal to line_start")
        return self


class Source(DomainModel):
    id: str = Field(default_factory=new_id, min_length=1)
    project_id: str = Field(min_length=1)
    role: SourceRole = SourceRole.UNKNOWN
    original_name: str = Field(min_length=1)
    stored_path: str = ""
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str = "application/octet-stream"
    size_bytes: int = Field(ge=0)
    classification_confidence: float | None = Field(default=None, ge=0, le=1)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class SourceSnapshot(DomainModel):
    """Immutable local capture of the exact bytes used to verify web evidence."""

    id: str = Field(default_factory=new_id, min_length=1)
    project_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    canonical_url: str = Field(min_length=1)
    final_url: str = Field(min_length=1)
    stored_path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    organization: str = ""
    publication_date: date | None = None
    doi: str | None = None
    isbn: str | None = None
    accessed_at: datetime = Field(default_factory=utc_now)
    locator: Locator = Field(default_factory=Locator)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bind_locator(self) -> SourceSnapshot:
        if self.locator.source_id is None:
            self.locator.source_id = self.source_id
        elif self.locator.source_id != self.source_id:
            raise ValueError("snapshot locator.source_id must match source_id")
        if self.locator.snapshot_id is None:
            self.locator.snapshot_id = self.id
        elif self.locator.snapshot_id != self.id:
            raise ValueError("snapshot locator.snapshot_id must match snapshot id")
        return self


class SourceFragment(DomainModel):
    id: str = Field(default_factory=new_id, min_length=1)
    source_id: str = Field(min_length=1)
    content: str
    locator: Locator = Field(default_factory=Locator)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    token_count: int | None = Field(default=None, ge=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bind_locator(self) -> SourceFragment:
        if self.locator.source_id is None:
            self.locator.source_id = self.source_id
        elif self.locator.source_id != self.source_id:
            raise ValueError("locator.source_id must match source_id")
        if self.sha256 is None:
            object.__setattr__(self, "sha256", hashlib.sha256(self.content.encode("utf-8")).hexdigest())
        return self


# Backwards-compatible vocabulary used by ingestion implementations.
SourceChunk = SourceFragment


class RuleProvenance(DomainModel):
    source_id: str | None = None
    locator: Locator | None = None
    priority: RequirementPriority = RequirementPriority.BUILTIN
    extraction_method: str = ""
    confidence: float = Field(default=1.0, ge=0, le=1)


class RequirementRule(DomainModel):
    id: str = Field(default_factory=new_id, min_length=1)
    category: RequirementCategory = RequirementCategory.CUSTOM
    key: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    value: JsonValue = None
    mandatory: bool = True
    provenance: list[RuleProvenance] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Conflict(DomainModel):
    id: str = Field(default_factory=new_id)
    key: str = Field(min_length=1)
    rule_ids: list[str] = Field(min_length=2)
    description: str = ""
    resolved_rule_id: str | None = None
    resolution_reason: str = ""

    @model_validator(mode="after")
    def validate_resolution(self) -> Conflict:
        if self.resolved_rule_id is not None and self.resolved_rule_id not in self.rule_ids:
            raise ValueError("resolved_rule_id must refer to a conflicting rule")
        return self


class RequirementSet(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str = Field(min_length=1)
    rules: list[RequirementRule] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1)


class RequirementCoverage(DomainModel):
    requirement_rule_id: str = Field(min_length=1)
    status: Literal["SATISFIED", "NOT_APPLICABLE", "FAILED"]
    evidence: str = ""
    artifact_id: str | None = None


class RequirementPdfPageMapping(DomainModel):
    """Rendered PDF pages on which one manuscript block appears."""

    block_id: str = Field(min_length=1)
    pages: list[Annotated[int, Field(ge=1)]] = Field(min_length=1)

    @field_validator("pages")
    @classmethod
    def normalize_pages(cls, value: list[int]) -> list[int]:
        return sorted(set(value))


class RequirementCoverageAssessment(DomainModel):
    """Observed coverage for a requirement before its rule metadata is attached."""

    status: Literal["covered", "partial", "missing"]
    block_ids: list[str] = Field(default_factory=list)
    pdf_page_mappings: list[RequirementPdfPageMapping] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    evidence_summary: str = ""
    artifact_id: str | None = None
    reason: str = ""

    @field_validator("block_ids")
    @classmethod
    def validate_block_ids(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("block_ids must not contain empty values")
        if len(value) != len(set(value)):
            raise ValueError("block_ids must be unique")
        return sorted(value)

    @field_validator("pdf_page_mappings")
    @classmethod
    def validate_pdf_page_mappings(
        cls, value: list[RequirementPdfPageMapping]
    ) -> list[RequirementPdfPageMapping]:
        block_ids = [item.block_id for item in value]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("pdf_page_mappings must contain at most one entry per block")
        return sorted(value, key=lambda item: item.block_id)

    @field_validator("evidence_gaps")
    @classmethod
    def validate_evidence_gaps(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("evidence_gaps must not contain empty values")
        return sorted(set(value))

    @model_validator(mode="after")
    def validate_pdf_blocks(self) -> RequirementCoverageAssessment:
        unknown = {item.block_id for item in self.pdf_page_mappings} - set(self.block_ids)
        if unknown:
            raise ValueError(
                "pdf_page_mappings must refer to block_ids: " + ", ".join(sorted(unknown))
            )
        return self

    @property
    def has_coverage_location(self) -> bool:
        """Whether the assessment points to a manuscript block or artifact."""

        return bool(self.block_ids or self.artifact_id)


class RequirementCoverageEntry(RequirementCoverageAssessment):
    """Complete, traceable coverage state for one requirement rule."""

    requirement_rule_id: str = Field(min_length=1)
    requirement_key: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    # Default preserves stored reports created before mandatory coverage was
    # made an export gate. New reports always copy this from RequirementRule.
    mandatory: bool = False
    criticality: Literal["critical", "standard"]
    priority: RequirementPriority
    provenance: list[RuleProvenance] = Field(default_factory=list)
    source_locators: list[Locator] = Field(default_factory=list)


class RequirementCoverageReport(DomainModel):
    """A deterministic, typed coverage record for every rule in a requirement set."""

    project_id: str = Field(min_length=1)
    requirement_set_id: str = Field(min_length=1)
    entries: list[RequirementCoverageEntry] = Field(default_factory=list)
    schema_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_rule_entries(self) -> RequirementCoverageReport:
        rule_ids = [entry.requirement_rule_id for entry in self.entries]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("requirement coverage entries must be unique per rule")
        return self

    @property
    def blocking_entries(self) -> tuple[RequirementCoverageEntry, ...]:
        """Entries which must stop export until the gap is resolved."""

        return tuple(
            entry
            for entry in self.entries
            if (entry.criticality == "critical" and entry.status != "covered")
            or (
                entry.criticality == "critical"
                and entry.status == "covered"
                and not entry.has_coverage_location
            )
            or bool(entry.evidence_gaps)
        )

    @property
    def has_blocking_gaps(self) -> bool:
        return bool(self.blocking_entries)


class TemplateSection(DomainModel):
    title: str
    level: int = Field(ge=1, le=9)
    order: int = Field(ge=0)


class TemplateStyle(DomainModel):
    name: str
    based_on: str | None = None
    properties: dict[str, JsonValue] = Field(default_factory=dict)


class TemplateRelationship(DomainModel):
    relationship_id: str
    relationship_type: str
    target: str


class TemplateAnalysis(DomainModel):
    source_id: str | None = None
    sections: list[TemplateSection] = Field(default_factory=list)
    styles: list[TemplateStyle] = Field(default_factory=list)
    relationships: list[TemplateRelationship] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TemplateApplicationPlan(DomainModel):
    """A safe declarative plan; it never accepts a copied XML body."""

    template_source_id: str | None = None
    use_styles: list[str] = Field(default_factory=list)
    preserve_headers_footers: bool = True
    preserve_page_setup: bool = True
    section_style_map: dict[str, str] = Field(default_factory=dict)
    allowed_relationship_types: list[str] = Field(default_factory=list)

    @field_validator("section_style_map")
    @classmethod
    def reject_xml_payload(cls, value: dict[str, str]) -> dict[str, str]:
        if any("<" in key or "<" in style for key, style in value.items()):
            raise ValueError("TemplateApplicationPlan must not contain raw XML")
        return value

    @field_validator("use_styles", "allowed_relationship_types")
    @classmethod
    def reject_markup_lists(cls, value: list[str]) -> list[str]:
        if any("<" in item or ">" in item or any(ord(character) < 32 for character in item) for item in value):
            raise ValueError("TemplateApplicationPlan values must be plain identifiers")
        return value


class VisualRequest(DomainModel):
    kind: VisualKind
    purpose: str
    dataset_id: str | None = None
    requirements: dict[str, JsonValue] = Field(default_factory=dict)


class SectionSpec(DomainModel):
    id: str = Field(default_factory=new_id)
    title: str = Field(min_length=1)
    level: int = Field(default=1, ge=1, le=6)
    order: int = Field(default=0, ge=0)
    target_words: int = Field(default=0, ge=0)
    theses: list[str] = Field(default_factory=list)
    required_claim_ids: list[str] = Field(default_factory=list)
    required_fact_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    visual_requests: list[VisualRequest] = Field(default_factory=list)
    expected_conclusion: str = ""
    goal_links: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class Outline(DomainModel):
    id: str = Field(default_factory=new_id)
    sections: list[SectionSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sections(self) -> Outline:
        ids = [section.id for section in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("section ids must be unique")
        known = set(ids)
        for section in self.sections:
            unknown = set(section.depends_on) - known
            if unknown:
                raise ValueError(f"unknown section dependencies: {sorted(unknown)}")
            if section.id in section.depends_on:
                raise ValueError("a section cannot depend on itself")
        return self


class ProjectBlueprint(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    goal: str = ""
    tasks: list[str] = Field(default_factory=list)
    object_of_study: str = ""
    subject_of_study: str = ""
    hypothesis: str = ""
    methods: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    target_words: int | None = Field(default=None, ge=0)
    target_pages: int | None = Field(default=None, ge=0)
    outline: Outline = Field(default_factory=Outline)
    required_claims: list[str] = Field(default_factory=list)
    planned_visuals: list[VisualRequest] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class Claim(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    section_id: str | None = None
    checkable: bool = True
    evidence_ids: list[str] = Field(default_factory=list)
    status: ClaimStatus = ClaimStatus.PENDING
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Evidence(DomainModel):
    id: str = Field(default_factory=new_id)
    claim_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    snapshot_id: str | None = None
    locator: Locator
    excerpt: str = ""
    supports: bool = True
    confidence: float = Field(default=1.0, ge=0, le=1)
    verified: bool = False
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bind_source(self) -> Evidence:
        if self.locator.source_id is None:
            self.locator.source_id = self.source_id
        elif self.locator.source_id != self.source_id:
            raise ValueError("locator.source_id must match source_id")
        if self.snapshot_id is None and self.locator.snapshot_id is not None:
            self.snapshot_id = self.locator.snapshot_id
        elif self.snapshot_id is not None and self.locator.snapshot_id is None:
            self.locator.snapshot_id = self.snapshot_id
        elif self.snapshot_id is not None and self.locator.snapshot_id != self.snapshot_id:
            raise ValueError("locator.snapshot_id must match evidence.snapshot_id")
        return self


EvidenceItem = Evidence


class BibliographyEntry(DomainModel):
    id: str = Field(default_factory=new_id)
    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=1000, le=9999)
    publisher: str | None = None
    source_type: str = "other"
    doi: str | None = None
    isbn: str | None = None
    url: str | None = None
    accessed_on: date | None = None
    source_id: str | None = None
    citation_text: str = ""
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Citation(DomainModel):
    id: str = Field(default_factory=new_id)
    claim_id: str | None = None
    evidence_id: str | None = None
    bibliography_entry_id: str = Field(min_length=1)
    marker: str = ""
    page: str | None = None


class FactRecord(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    value: JsonValue
    unit: str | None = None
    origin: FactOrigin
    source_id: str | None = None
    evidence_id: str | None = None
    calculation_id: str | None = None
    synthetic_seed: int | None = None
    generation_method: str | None = None
    constraints: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class DatasetColumn(DomainModel):
    name: str = Field(min_length=1)
    data_type: DataType = DataType.STRING
    unit: str | None = None
    nullable: bool = True


class Dataset(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    columns: list[DatasetColumn]
    rows: list[dict[str, JsonValue]] = Field(default_factory=list)
    origin: FactOrigin
    source_ids: list[str] = Field(default_factory=list)
    synthetic_seed: int | None = None
    generation_method: str | None = None
    repository: str | None = None
    stable_id: str | None = None
    version: str | None = None
    license: str | None = None
    retrieved_at: datetime | None = None
    snapshot_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    publishability: Literal["publishable", "non_publishable_synthetic_demo", "unknown"] = "unknown"
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_rows(self) -> Dataset:
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("dataset column names must be unique")
        allowed = set(names)
        for index, row in enumerate(self.rows):
            unknown = set(row) - allowed
            if unknown:
                raise ValueError(f"row {index} has unknown columns: {sorted(unknown)}")
            missing = {
                column.name for column in self.columns if not column.nullable and column.name not in row
            }
            if missing:
                raise ValueError(f"row {index} lacks required columns: {sorted(missing)}")
        return self

    @model_validator(mode="after")
    def label_synthetic_publishability(self) -> Dataset:
        if self.origin is FactOrigin.SYNTHETIC:
            object.__setattr__(self, "publishability", "non_publishable_synthetic_demo")
        return self


class ClaimBinding(DomainModel):
    """A precise text span that is supported by an evidence record."""

    claim_id: str = Field(min_length=1)
    block_id: str = Field(min_length=1)
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)
    evidence_id: str = Field(min_length=1)
    locator: Locator

    @model_validator(mode="after")
    def validate_span(self) -> ClaimBinding:
        if self.span_end <= self.span_start:
            raise ValueError("binding span_end must be greater than span_start")
        return self


class NumericFactBinding(DomainModel):
    """A numeric text span with its FactLedger and evidence provenance."""

    fact_id: str = Field(min_length=1)
    block_id: str = Field(min_length=1)
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)
    evidence_id: str | None = None
    locator: Locator | None = None

    @model_validator(mode="after")
    def validate_span(self) -> NumericFactBinding:
        if self.span_end <= self.span_start:
            raise ValueError("binding span_end must be greater than span_start")
        return self


class Calculation(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    expression: str = Field(min_length=1)
    input_fact_ids: list[str] = Field(default_factory=list)
    output_fact_id: str | None = None
    result: JsonValue = None
    checks: dict[str, bool] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Metric(DomainModel):
    name: str = Field(min_length=1)
    value: float
    unit: str | None = None
    target: float | None = None
    passed: bool | None = None
    details: str = ""


class FactLedger(DomainModel):
    """Single source of truth for every number used in a manuscript."""

    project_id: str = Field(min_length=1)
    facts: list[FactRecord] = Field(default_factory=list)
    datasets: list[Dataset] = Field(default_factory=list)
    calculations: list[Calculation] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)


class TableSpec(DomainModel):
    caption: str = ""
    dataset_id: str | None = None
    headers: list[str] = Field(default_factory=list)
    rows: list[list[JsonValue]] = Field(default_factory=list)
    column_widths: list[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shape(self) -> TableSpec:
        if self.headers:
            expected = len(self.headers)
            if any(len(row) != expected for row in self.rows):
                raise ValueError("every table row must match the header width")
        return self


class ChartSpec(DomainModel):
    chart_type: ChartType
    title: str = ""
    dataset_id: str
    x_column: str
    y_columns: list[str]
    x_label: str = ""
    y_label: str = ""
    options: dict[str, JsonValue] = Field(default_factory=dict)


class DiagramSpec(DomainModel):
    title: str = ""
    language: Literal["mermaid", "graphviz"] = "mermaid"
    source: str = Field(min_length=1)


class FormulaSpec(DomainModel):
    expression: str = Field(min_length=1)
    notation: Literal["latex", "mathml", "omml"] = "latex"
    label: str | None = None


class ImageSpec(DomainModel):
    prompt: str = Field(min_length=1)
    aspect_ratio: str = "4:3"
    alt_text: str = ""


class ParagraphBlock(DomainModel):
    type: Literal["paragraph"] = "paragraph"
    id: str = Field(default_factory=new_id)
    text: str
    citation_ids: list[str] = Field(default_factory=list)
    numeric_fact_ids: list[str] = Field(default_factory=list)
    style: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class HeadingBlock(DomainModel):
    type: Literal["heading"] = "heading"
    id: str = Field(default_factory=new_id)
    text: str
    level: int = Field(default=1, ge=1, le=6)
    section_id: str | None = None


class TableBlock(DomainModel):
    type: Literal["table"] = "table"
    id: str = Field(default_factory=new_id)
    spec: TableSpec
    # Inline table values do not otherwise carry a path back to the FactLedger.
    # A numeric table must either name a valid ``dataset_id`` or bind its
    # values to these records, regardless of whether it was model- or
    # user-authored.
    numeric_fact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ChartBlock(DomainModel):
    type: Literal["chart"] = "chart"
    id: str = Field(default_factory=new_id)
    spec: ChartSpec
    artifact_id: str | None = None


class DiagramBlock(DomainModel):
    type: Literal["diagram"] = "diagram"
    id: str = Field(default_factory=new_id)
    spec: DiagramSpec
    artifact_id: str | None = None


class FormulaBlock(DomainModel):
    type: Literal["formula"] = "formula"
    id: str = Field(default_factory=new_id)
    spec: FormulaSpec


class CodeListingBlock(DomainModel):
    type: Literal["code_listing"] = "code_listing"
    id: str = Field(default_factory=new_id)
    code: str
    language: str = "text"
    caption: str = ""
    locator: Locator | None = None


class FigureBlock(DomainModel):
    type: Literal["figure"] = "figure"
    id: str = Field(default_factory=new_id)
    caption: str
    artifact_id: str | None = None
    image_spec: ImageSpec | None = None
    alt_text: str = ""

    @model_validator(mode="after")
    def require_figure_source(self) -> FigureBlock:
        if self.artifact_id is None and self.image_spec is None:
            raise ValueError("a figure needs artifact_id or image_spec")
        return self


class CitationBlock(DomainModel):
    type: Literal["citation"] = "citation"
    id: str = Field(default_factory=new_id)
    citation_id: str
    text: str = ""


class PageBreakBlock(DomainModel):
    type: Literal["page_break"] = "page_break"
    id: str = Field(default_factory=new_id)


class AppendixBlock(DomainModel):
    type: Literal["appendix"] = "appendix"
    id: str = Field(default_factory=new_id)
    title: str
    blocks: list[ManuscriptBlock] = Field(default_factory=list)


ManuscriptBlock = Annotated[
    ParagraphBlock
    | HeadingBlock
    | TableBlock
    | ChartBlock
    | DiagramBlock
    | FormulaBlock
    | CodeListingBlock
    | FigureBlock
    | CitationBlock
    | PageBreakBlock
    | AppendixBlock,
    Field(discriminator="type"),
]


class Manuscript(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str = Field(min_length=1)
    title: str
    blocks: list[ManuscriptBlock] = Field(default_factory=list)
    bibliography: list[BibliographyEntry] = Field(default_factory=list)
    revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    quality_status: QualityStatus = QualityStatus.NEEDS_REPAIR
    claim_bindings: list[ClaimBinding] = Field(default_factory=list)
    numeric_fact_bindings: list[NumericFactBinding] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class GenerationRun(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str = Field(min_length=1)
    status: RunStatus = RunStatus.QUEUED
    pipeline_version: str = "1"
    model_policy: dict[str, JsonValue] = Field(default_factory=dict)
    current_stage: str | None = None
    input_hash: str = ""
    cost: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = "USD"
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def normalize_run_currency(cls, value: str) -> str:
        return value.upper()


class StageRun(DomainModel):
    id: str = Field(default_factory=new_id)
    run_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    order: int = Field(default=0, ge=0)
    status: StageStatus = StageStatus.QUEUED
    attempts: int = Field(default=0, ge=0)
    input_hash: str = ""
    dependency_hashes: dict[str, str] = Field(default_factory=dict)
    heartbeat_at: datetime | None = None
    progress_current: int = Field(default=0, ge=0)
    progress_total: int = Field(default=0, ge=0)
    output_hash: str = ""
    failure_code: str | None = None
    failure_details: dict[str, JsonValue] = Field(default_factory=dict)
    remote_resource_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    cost: Decimal = Field(default=Decimal("0"), ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    checkpoint: dict[str, JsonValue] = Field(default_factory=dict)


class RemoteResource(DomainModel):
    """A provider-side resource persisted immediately after upload."""

    id: str = Field(default_factory=new_id)
    project_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    stage_id: str | None = None
    provider: str = "gemini"
    remote_id: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    local_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mime_type: str = "application/octet-stream"
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class Artifact(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str = Field(min_length=1)
    run_id: str | None = None
    stage_id: str | None = None
    kind: ArtifactKind = ArtifactKind.OTHER
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str = "application/octet-stream"
    size_bytes: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class RunEvent(DomainModel):
    id: str = Field(default_factory=new_id)
    run_id: str = Field(min_length=1)
    stage_id: str | None = None
    event_type: str = Field(min_length=1)
    message: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    data: dict[str, JsonValue] = Field(default_factory=dict)


class QAIssue(DomainModel):
    id: str = Field(default_factory=new_id)
    severity: QASeverity
    category: str = Field(min_length=1)
    message: str = Field(min_length=1)
    requirement_rule_id: str | None = None
    artifact_id: str | None = None
    locator: Locator | None = None
    auto_fixable: bool = False
    resolved: bool = False
    resolution: str = ""
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class QAReport(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    status: QAStatus = QAStatus.PASS
    issues: list[QAIssue] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    requirement_coverage: RequirementCoverageReport | None = None
    created_at: datetime = Field(default_factory=utc_now)
    summary: str = ""
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def derive_status(self) -> QAReport:
        active = {issue.severity for issue in self.issues if not issue.resolved}
        derived = QAStatus.PASS
        if QASeverity.BLOCKER in active or QASeverity.CRITICAL in active or QASeverity.ERROR in active:
            derived = QAStatus.FAIL
        elif QASeverity.WARNING in active:
            derived = QAStatus.WARNING
        rank = {QAStatus.PASS: 0, QAStatus.WARNING: 1, QAStatus.FAIL: 2}
        if rank[derived] > rank[self.status]:
            object.__setattr__(self, "status", derived)
        return self


# Resolve the recursive AppendixBlock -> ManuscriptBlock annotation.
AppendixBlock.model_rebuild()
Manuscript.model_rebuild()


# Concise names from the architecture plan remain aliases of explicit block
# models, making their rendering semantics unambiguous while keeping the API
# pleasant for callers.
Paragraph = ParagraphBlock
Heading = HeadingBlock
Table = TableBlock
Chart = ChartBlock
Diagram = DiagramBlock
Formula = FormulaBlock
CodeListing = CodeListingBlock
Figure = FigureBlock
Appendix = AppendixBlock
