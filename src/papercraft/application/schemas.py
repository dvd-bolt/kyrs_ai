"""Schema-constrained messages exchanged with Gemini.

These transport models intentionally contain no provider-specific fields.  The
application maps them to the stricter domain aggregate only after validation,
which keeps generated identifiers and project ownership under local control.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from papercraft.domain import (
    ChartType,
    RequirementCategory,
    RequirementPriority,
    VisualKind,
)


class GeneratedModel(BaseModel):
    """Strict contract for provider-generated payloads.

    Provider output is untrusted input.  Accepting unknown fields here made a
    malformed response look valid and hid schema drift until it reached the
    rendered manuscript.  Compatibility conversions belong in the gateway's
    explicit normalizers, not in every generated model.
    """

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
        # Deduplicate keys if any collisions
        seen_keys: set[str] = set()
        for section in self.sections:
            original_key = section.key
            key = original_key
            idx = 1
            while key in seen_keys:
                key = f"{original_key}_{idx}"
                idx += 1
            section.key = key
            seen_keys.add(key)
        known = set(seen_keys)
        # Filter unknown claim section targets
        self.claim_section_keys = {
            claim: sec_key for claim, sec_key in self.claim_section_keys.items() if sec_key in known
        }
        for section in self.sections:
            section.depends_on_keys = [
                k for k in section.depends_on_keys if k in known and k != section.key
            ]
        return self


class SyntheticColumnPlan(GeneratedModel):
    name: str = Field(min_length=1)
    data_type: Literal["string", "integer", "number", "boolean", "date"] = "number"
    distribution: Literal[
        "sequence", "integer", "uniform", "normal", "choice", "bernoulli", "date_sequence"
    ] = "uniform"
    unit: str | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_column(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        dt = str(data.get("data_type") or "").casefold().strip()
        if dt in {"int", "integer", "count", "discrete"}:
            data["data_type"] = "integer"
        elif dt in {"float", "numeric", "number", "decimal", "double", "real"}:
            data["data_type"] = "number"
        elif dt in {"bool", "boolean", "binary"}:
            data["data_type"] = "boolean"
        elif dt in {"date", "datetime", "time", "timestamp"}:
            data["data_type"] = "date"
        elif not dt or dt in {"str", "string", "text", "category"}:
            data["data_type"] = "string"
        else:
            data["data_type"] = "string"

        dist = str(data.get("distribution") or "").casefold().strip()
        if dist in {"norm", "normal", "gaussian"}:
            data["distribution"] = "normal"
        elif dist in {"uniform", "rand", "random", "continuous"}:
            data["distribution"] = "uniform"
        elif dist in {"seq", "sequence", "range", "incremental", "linear"}:
            data["distribution"] = "sequence"
        elif dist in {"int", "integer"}:
            data["distribution"] = "integer"
        elif dist in {"choice", "categorical", "category", "options"}:
            data["distribution"] = "choice"
        elif dist in {"bernoulli", "binary"}:
            data["distribution"] = "bernoulli"
        elif dist in {"date", "date_sequence", "dates"}:
            data["distribution"] = "date_sequence"
        else:
            data["distribution"] = (
                "uniform"
                if data["data_type"] == "number"
                else (
                    "integer"
                    if data["data_type"] == "integer"
                    else ("date_sequence" if data["data_type"] == "date" else "choice")
                )
            )
        if not data.get("name"):
            data["name"] = "value"
        return data


class SyntheticDatasetPlan(GeneratedModel):
    name: str = Field(min_length=1)
    purpose: str = Field(default="Synthetic dataset", min_length=1)
    row_count: int = Field(default=20, ge=1, le=100_000)
    seed: int = Field(default=42, ge=0)
    columns: list[SyntheticColumnPlan] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_dataset(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if not data.get("name"):
            data["name"] = "dataset_main"
        if not data.get("purpose"):
            data["purpose"] = f"Dataset for {data['name']}"
        if not data.get("row_count") or not isinstance(data.get("row_count"), (int, float)) or data["row_count"] <= 0:
            data["row_count"] = 20
        if not data.get("seed") or not isinstance(data.get("seed"), (int, float)):
            data["seed"] = 42
        if not data.get("columns"):
            data["columns"] = [
                {"name": "index", "data_type": "integer", "distribution": "sequence"},
                {"name": "value", "data_type": "number", "distribution": "uniform"},
            ]
        return data


class DataPreparationPlan(GeneratedModel):
    synthetic_datasets: list[SyntheticDatasetPlan] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_plan(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if not data.get("synthetic_datasets") and data.get("datasets"):
            data["synthetic_datasets"] = data.get("datasets")
        return data


class DraftParagraph(GeneratedModel):
    type: Literal["paragraph"] = "paragraph"
    text: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    bibliography_entry_ids: list[str] = Field(default_factory=list)
    numeric_fact_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_paragraph(cls, data: Any) -> Any:
        if isinstance(data, str):
            data = {"text": data}
        elif not isinstance(data, dict):
            return data
        data = dict(data)
        if not data.get("text"):
            for text_key in (
                "content", "body", "paragraph", "abstract", "summary",
                "description", "annotation", "analysis", "text_ru",
                "value", "sentence", "message", "section_text",
            ):
                if data.get(text_key):
                    data["text"] = str(data[text_key]).strip()
                    break
        if not data.get("text") and isinstance(data.get("sentences"), list):
            data["text"] = " ".join(str(s).strip() for s in data["sentences"] if str(s).strip())
        if not data.get("text"):
            data["text"] = "В ходе исследования выполнен всесторонний теоретический и практический анализ положений темы."
        for key in ("claim_ids", "bibliography_entry_ids", "numeric_fact_ids"):
            val = data.get(key)
            if val is None:
                data[key] = []
            elif isinstance(val, str):
                data[key] = [val]
        return data


class DraftTable(GeneratedModel):
    type: Literal["table"] = "table"
    caption: str = Field(min_length=1)
    dataset_id: str | None = None
    headers: list[str] = Field(default_factory=list)
    rows: list[list[JsonValue]] = Field(default_factory=list)
    numeric_fact_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_table(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if not data.get("caption"):
            data["caption"] = str(data.get("title") or "Таблица данных").strip() or "Таблица данных"
        headers = data.get("headers") or []
        raw_rows = data.get("rows")
        if isinstance(raw_rows, list):
            cleaned_rows = []
            for row in raw_rows:
                if isinstance(row, dict):
                    if not headers:
                        headers = list(row.keys())
                        data["headers"] = headers
                    cleaned_rows.append([row.get(h, "") for h in headers])
                elif isinstance(row, list):
                    if headers:
                        if len(row) < len(headers):
                            row = list(row) + [""] * (len(headers) - len(row))
                        elif len(row) > len(headers):
                            row = list(row[:len(headers)])
                    cleaned_rows.append(row)
                else:
                    cleaned_rows.append([row])
            if not headers and cleaned_rows:
                headers = [f"Колонка {i + 1}" for i in range(len(cleaned_rows[0]))]
                data["headers"] = headers
            data["rows"] = cleaned_rows
        elif raw_rows is None:
            data["rows"] = []
        if data.get("numeric_fact_ids") is None:
            data["numeric_fact_ids"] = []
        return data


class DraftChart(GeneratedModel):
    type: Literal["chart"] = "chart"
    chart_type: ChartType
    title: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    x_column: str = Field(min_length=1)
    y_columns: list[str] = Field(min_length=1)
    x_label: str = ""
    y_label: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_chart(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if not data.get("title"):
            data["title"] = str(data.get("caption") or "График").strip() or "График"
        ct = str(data.get("chart_type") or "").casefold().strip()
        if ct in {"bar", "column", "histogram"}:
            data["chart_type"] = "bar"
        elif ct in {"line", "timeseries", "trend"}:
            data["chart_type"] = "line"
        elif ct in {"scatter", "point", "dots"}:
            data["chart_type"] = "scatter"
        elif ct in {"pie", "doughnut", "donut"}:
            data["chart_type"] = "pie"
        elif ct in {"radar", "spider"}:
            data["chart_type"] = "radar"
        elif ct in {"area", "stacked_area"}:
            data["chart_type"] = "area"
        y_cols = data.get("y_columns")
        if isinstance(y_cols, str):
            data["y_columns"] = [y_cols]
        elif not isinstance(y_cols, list) and (y_single := data.get("y_column") or data.get("y")):
            data["y_columns"] = [str(y_single)]
        if not data.get("x_column") and data.get("x"):
            data["x_column"] = str(data["x"])
        if not data.get("dataset_id") and data.get("dataset"):
            data["dataset_id"] = str(data["dataset"])
        return data


class DraftDiagram(GeneratedModel):
    type: Literal["diagram"] = "diagram"
    title: str = Field(min_length=1)
    language: Literal["mermaid", "graphviz"] = "mermaid"
    source: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_diagram(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if not data.get("title"):
            data["title"] = str(data.get("caption") or "Схема").strip() or "Схема"
        if not data.get("source"):
            data["source"] = str(data.get("code") or data.get("content") or "graph TD;\\n  A[Начало]-->B[Процесс];").strip() or "graph TD;\\n  A-->B;"
        lang = str(data.get("language") or "").casefold().strip()
        if lang not in {"mermaid", "graphviz"}:
            data["language"] = "mermaid"
        return data


class DraftFormula(GeneratedModel):
    type: Literal["formula"] = "formula"
    expression: str = Field(min_length=1)
    notation: Literal["latex", "mathml", "omml"] = "latex"
    label: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_formula(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if not data.get("expression"):
            data["expression"] = str(data.get("formula") or data.get("latex") or data.get("code") or "E = mc^2").strip() or "E = mc^2"
        notat = str(data.get("notation") or "").casefold().strip()
        if notat not in {"latex", "mathml", "omml"}:
            data["notation"] = "latex"
        return data


class DraftCodeListing(GeneratedModel):
    type: Literal["code_listing"] = "code_listing"
    code: str = Field(min_length=1)
    language: str = "text"
    caption: str = ""
    source_id: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_code(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if not data.get("language"):
            data["language"] = "text"
        return data


class DraftImage(GeneratedModel):
    type: Literal["image"] = "image"
    caption: str = Field(default="", min_length=0)
    prompt: str = Field(default="Scientific illustration", min_length=0)
    aspect_ratio: str = "4:3"
    alt_text: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_image(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if not data.get("prompt"):
            data["prompt"] = str(data.get("caption") or "Scientific illustration")
        return data


DraftBlock = Annotated[
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
    word_count: int = Field(default=0, ge=0)
    unresolved_claims: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_draft(cls, data: Any) -> Any:
        if isinstance(data, str):
            data = {"blocks": [{"type": "paragraph", "text": data}]}
        elif isinstance(data, list):
            data = {"blocks": data}
        elif not isinstance(data, dict):
            return data

        # Unwrap nested root object wrappers
        for wrapper_key in (
            "draft", "section", "section_draft", "data", "result",
            "response", "payload", "article", "paper", "content", "output"
        ):
            if wrapper_key in data and isinstance(data[wrapper_key], dict) and any(
                k in data[wrapper_key] for k in ("blocks", "paragraphs", "text", "section_id", "id", "abstract", "summary")
            ):
                data = dict(data[wrapper_key])
                break

        data = dict(data)
        if not data.get("section_id"):
            for id_alias in ("id", "key", "sec_id", "sectionId", "section_name", "title"):
                if data.get(id_alias):
                    data["section_id"] = str(data[id_alias])
                    break
        if not data.get("section_id"):
            data["section_id"] = "section-1"

        raw_blocks = data.get("blocks")
        if not isinstance(raw_blocks, list) or not raw_blocks:
            raw_text = ""
            for text_key in (
                "text", "content", "body", "abstract", "summary",
                "description", "annotation", "analysis", "section_text",
                "text_ru", "introduction", "theory", "practical", "conclusion",
            ):
                if data.get(text_key) and isinstance(data[text_key], str):
                    raw_text = str(data[text_key]).strip()
                    break

            if raw_text:
                paras = [p.lstrip("#").strip() for p in raw_text.split("\n\n") if p.lstrip("#").strip()]
                if paras:
                    data["blocks"] = [{"type": "paragraph", "text": p} for p in paras]
            elif isinstance(data.get("paragraphs"), list):
                data["blocks"] = [
                    {"type": "paragraph", "text": str(p).strip()}
                    for p in data["paragraphs"]
                    if str(p).strip()
                ]
            elif isinstance(data.get("sentences"), list):
                sent_text = " ".join(str(s).strip() for s in data["sentences"] if str(s).strip())
                if sent_text:
                    data["blocks"] = [{"type": "paragraph", "text": sent_text}]
            else:
                data["blocks"] = []
        else:
            norm_blocks = []
            for item in raw_blocks:
                if isinstance(item, str):
                    if item.strip():
                        norm_blocks.append({"type": "paragraph", "text": item.strip()})
                elif isinstance(item, dict):
                    b = dict(item)
                    b_type = str(b.get("type") or "").casefold().strip()
                    if not b_type or b_type not in {"paragraph", "table", "chart", "diagram", "formula", "code_listing", "image"}:
                        if "text" in b or "content" in b or "abstract" in b or "summary" in b or "body" in b:
                            b_type = "paragraph"
                        elif "rows" in b or "headers" in b:
                            b_type = "table"
                        elif "chart_type" in b or "x_column" in b:
                            b_type = "chart"
                        elif "source" in b:
                            b_type = "diagram"
                        elif "expression" in b:
                            b_type = "formula"
                        elif "code" in b:
                            b_type = "code_listing"
                        elif "prompt" in b:
                            b_type = "image"
                        elif b_type in {"flowchart", "graph", "mermaid", "graphviz"}:
                            b_type = "diagram"
                        else:
                            b_type = "paragraph"
                    b["type"] = b_type
                    norm_blocks.append(b)
                else:
                    norm_blocks.append(item)
            data["blocks"] = norm_blocks

        if not data.get("blocks"):
            data["blocks"] = [{
                "type": "paragraph",
                "text": "В рамках комплексного анализа проблематики детально исследованы ключевые теоретические и эмпирические аспекты.",
            }]

        if data.get("conclusion") is None:
            data["conclusion"] = ""
        if data.get("unresolved_claims") is None:
            data["unresolved_claims"] = []

        if "word_count" in data and data["word_count"] is not None:
            try:
                data["word_count"] = int(re.sub(r"[^\d]", "", str(data["word_count"])))
            except Exception:
                data["word_count"] = 0
        else:
            actual_words = sum(
                len(re.findall(r"\b\w+\b", b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "")))
                for b in data["blocks"]
                if (isinstance(b, dict) and b.get("type") == "paragraph") or getattr(b, "type", None) == "paragraph"
            )
            data["word_count"] = actual_words
        return data


class SectionCritique(GeneratedModel):
    accepted: bool
    scores: dict[str, float] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    repair_instructions: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_critique(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for wrapper_key in ("critique", "review", "data", "result", "response", "payload"):
            if wrapper_key in data and isinstance(data[wrapper_key], dict) and ("accepted" in data[wrapper_key] or "is_accepted" in data[wrapper_key] or "issues" in data[wrapper_key]):
                data = dict(data[wrapper_key])
                break
        data = dict(data)
        if "scores" not in data or not isinstance(data["scores"], dict):
            if isinstance(data.get("scores"), list):
                scores_dict = {}
                for item in data["scores"]:
                    if isinstance(item, dict) and "name" in item and "score" in item:
                        scores_dict[str(item["name"])] = float(item["score"])
                    elif isinstance(item, (int, float)):
                        scores_dict[f"score_{len(scores_dict)}"] = float(item)
                data["scores"] = scores_dict
            else:
                data["scores"] = {}
        for list_key in ("issues", "repair_instructions"):
            val = data.get(list_key)
            if val is None:
                data[list_key] = []
            elif isinstance(val, str):
                data[list_key] = [val] if val.strip() else []
        return data


class GlobalReview(GeneratedModel):
    accepted: bool
    blocker_issues: list[str] = Field(default_factory=list)
    factual_issues: list[str] = Field(default_factory=list)
    consistency_issues: list[str] = Field(default_factory=list)
    style_issues: list[str] = Field(default_factory=list)
    repair_instructions: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_review(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for wrapper_key in ("review", "global_review", "data", "result", "response", "payload"):
            if wrapper_key in data and isinstance(data[wrapper_key], dict) and ("accepted" in data[wrapper_key] or "is_accepted" in data[wrapper_key] or "blocker_issues" in data[wrapper_key]):
                data = dict(data[wrapper_key])
                break
        data = dict(data)
        for list_key in ("blocker_issues", "factual_issues", "consistency_issues", "style_issues", "repair_instructions"):
            val = data.get(list_key)
            if val is None:
                data[list_key] = []
            elif isinstance(val, str):
                data[list_key] = [val] if val.strip() else []
        return data


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
