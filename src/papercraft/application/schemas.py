"""Schema-constrained messages exchanged with Gemini.

These transport models intentionally contain no provider-specific fields.  The
application maps them to the stricter domain aggregate only after validation,
which keeps generated identifiers and project ownership under local control.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from papercraft.domain import (
    ChartType,
    RequirementCategory,
    RequirementPriority,
    VisualKind,
)


class GeneratedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExtractedRule(GeneratedModel):
    category: RequirementCategory
    key: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    value: JsonValue = None
    mandatory: bool = True
    confidence: float = Field(default=1.0, ge=0, le=1)
    source_id: str | None = None
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    priority: RequirementPriority = RequirementPriority.METHODOLOGY


class ExtractedConflict(GeneratedModel):
    key: str = Field(min_length=1)
    rule_keys: list[str] = Field(min_length=2)
    description: str = ""
    winner_key: str | None = None
    resolution_reason: str = ""


class RequirementExtraction(GeneratedModel):
    rules: list[ExtractedRule] = Field(default_factory=list)
    conflicts: list[ExtractedConflict] = Field(default_factory=list)
    missing_critical_data: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ProposedClaim(GeneratedModel):
    text: str = Field(min_length=1)
    section_key: str | None = None
    checkable: bool = True
    search_query: str = Field(min_length=1)
    importance: Literal["critical", "high", "normal"] = "normal"


class ResearchPlan(GeneratedModel):
    claims: list[ProposedClaim] = Field(default_factory=list, max_length=80)


class EvidenceAssessment(GeneratedModel):
    claim_supported: bool
    supported_urls: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    rationale: str = ""
    evidence_quote: str = Field(default="", max_length=4_000)
    locator_hint: str = Field(default="", max_length=500)


class PlannedVisual(GeneratedModel):
    kind: VisualKind
    purpose: str = Field(min_length=1)
    dataset_name: str | None = None
    requirements: dict[str, JsonValue] = Field(default_factory=dict)


class PlannedSection(GeneratedModel):
    key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    level: int = Field(default=1, ge=1, le=6)
    order: int = Field(ge=0)
    target_words: int = Field(ge=0, le=100_000)
    theses: list[str] = Field(default_factory=list)
    required_claim_texts: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    visuals: list[PlannedVisual] = Field(default_factory=list)
    expected_conclusion: str = ""
    goal_links: list[str] = Field(default_factory=list)
    depends_on_keys: list[str] = Field(default_factory=list)


class BlueprintGeneration(GeneratedModel):
    topic: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    tasks: list[str] = Field(min_length=1)
    object_of_study: str = ""
    subject_of_study: str = ""
    hypothesis: str = ""
    methods: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    target_words: int | None = Field(default=None, ge=0, le=1_000_000)
    target_pages: int | None = Field(default=None, ge=0, le=10_000)
    sections: list[PlannedSection] = Field(min_length=1)
    # Keys are exact claim texts supplied in the planning prompt; values are
    # PlannedSection.key values.  Kept optional for legacy saved responses,
    # while _blueprint supplies a deterministic compatibility fallback.
    claim_section_keys: dict[str, str] = Field(default_factory=dict)
    required_claims: list[str] = Field(default_factory=list)
    planned_visuals: list[PlannedVisual] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_section_graph(self) -> BlueprintGeneration:
        keys = [section.key for section in self.sections]
        if len(keys) != len(set(keys)):
            raise ValueError("section keys must be unique")
        known = set(keys)
        unknown_claim_sections = set(self.claim_section_keys.values()) - known
        if unknown_claim_sections:
            raise ValueError(f"unknown claim section: {sorted(unknown_claim_sections)}")
        for section in self.sections:
            unknown = set(section.depends_on_keys) - known
            if unknown:
                raise ValueError(f"unknown section dependency: {sorted(unknown)}")
            if section.key in section.depends_on_keys:
                raise ValueError("a section cannot depend on itself")
        return self


class SyntheticColumnPlan(GeneratedModel):
    name: str = Field(min_length=1)
    data_type: Literal["string", "integer", "number", "boolean", "date"]
    distribution: Literal[
        "sequence", "integer", "uniform", "normal", "choice", "bernoulli", "date_sequence"
    ]
    unit: str | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class SyntheticDatasetPlan(GeneratedModel):
    name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    row_count: int = Field(ge=1, le=100_000)
    seed: int
    columns: list[SyntheticColumnPlan] = Field(min_length=1)


class DataPreparationPlan(GeneratedModel):
    synthetic_datasets: list[SyntheticDatasetPlan] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DraftParagraph(GeneratedModel):
    type: Literal["paragraph"] = "paragraph"
    text: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    bibliography_entry_ids: list[str] = Field(default_factory=list)
    numeric_fact_ids: list[str] = Field(default_factory=list)


class DraftTable(GeneratedModel):
    type: Literal["table"] = "table"
    caption: str
    dataset_id: str | None = None
    headers: list[str] = Field(default_factory=list)
    rows: list[list[JsonValue]] = Field(default_factory=list)
    # An inline numeric table which is not a rendering of a Dataset must name
    # the FactLedger records that support its values.  Keeping this in the
    # structured contract prevents a model response from silently losing the
    # provenance before it becomes a domain TableBlock.
    numeric_fact_ids: list[str] = Field(default_factory=list)


class DraftChart(GeneratedModel):
    type: Literal["chart"] = "chart"
    chart_type: ChartType
    title: str
    dataset_id: str
    x_column: str
    y_columns: list[str] = Field(min_length=1)
    x_label: str = ""
    y_label: str = ""


class DraftDiagram(GeneratedModel):
    type: Literal["diagram"] = "diagram"
    title: str
    language: Literal["mermaid", "graphviz"] = "mermaid"
    source: str = Field(min_length=1)


class DraftFormula(GeneratedModel):
    type: Literal["formula"] = "formula"
    expression: str = Field(min_length=1)
    notation: Literal["latex", "mathml", "omml"] = "latex"
    label: str | None = None


class DraftCodeListing(GeneratedModel):
    type: Literal["code_listing"] = "code_listing"
    code: str = Field(min_length=1)
    language: str = "text"
    caption: str = ""
    source_id: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)


class DraftImage(GeneratedModel):
    type: Literal["image"] = "image"
    caption: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    aspect_ratio: str = "4:3"
    alt_text: str = ""


type DraftBlock = Annotated[
    DraftParagraph
    | DraftTable
    | DraftChart
    | DraftDiagram
    | DraftFormula
    | DraftCodeListing
    | DraftImage,
    Field(discriminator="type"),
]


class SectionDraft(GeneratedModel):
    section_id: str = Field(min_length=1)
    blocks: list[DraftBlock] = Field(min_length=1)
    conclusion: str = ""
    word_count: int = Field(ge=0)
    unresolved_claims: list[str] = Field(default_factory=list)


class SectionCritique(GeneratedModel):
    accepted: bool
    scores: dict[str, float] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    repair_instructions: list[str] = Field(default_factory=list)


class GlobalReview(GeneratedModel):
    accepted: bool
    blocker_issues: list[str] = Field(default_factory=list)
    factual_issues: list[str] = Field(default_factory=list)
    consistency_issues: list[str] = Field(default_factory=list)
    style_issues: list[str] = Field(default_factory=list)
    repair_instructions: list[str] = Field(default_factory=list)


class VisualPageIssue(GeneratedModel):
    page: int = Field(ge=1)
    severity: Literal["info", "warning", "error", "critical", "blocker"]
    category: Literal[
        "cropped_text",
        "blank_page",
        "orphan_heading",
        "table_overflow",
        "unreadable_image",
        "caption",
        "page_number",
        "spacing",
        "other",
    ]
    message: str = Field(min_length=1)


class VisualQAResult(GeneratedModel):
    pages_checked: list[int] = Field(default_factory=list)
    issues: list[VisualPageIssue] = Field(default_factory=list)


SectionDraft.model_rebuild()


__all__ = [
    "BlueprintGeneration",
    "DataPreparationPlan",
    "DraftBlock",
    "EvidenceAssessment",
    "GlobalReview",
    "RequirementExtraction",
    "ResearchPlan",
    "SectionCritique",
    "SectionDraft",
    "VisualQAResult",
]
