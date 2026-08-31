"""Enumerations shared by the PaperCraft domain and its adapters."""

from __future__ import annotations

from enum import StrEnum


class WorkType(StrEnum):
    COURSEWORK = "coursework"
    SCIENTIFIC_ARTICLE = "scientific_article"
    PRACTICE_REPORT = "practice_report"
    LAB_REPORT = "lab_report"
    INDUSTRIAL_REPORT = "industrial_report"
    SCHOOL_PROJECT = "school_project"
    UNIVERSAL = "universal"


class DomainProfile(StrEnum):
    IT = "it"
    PROGRAMMING = "programming"
    FINANCE = "finance"
    ACCOUNTING = "accounting"
    GENERAL = "general"
    SCIENCE = "science"
    SCHOOL = "school"
    UNIVERSAL = "universal"


class SourceRole(StrEnum):
    METHODOLOGY = "methodology"
    EXAMPLE = "example"
    TEMPLATE = "template"
    SOURCE_DATA = "source_data"
    CODEBASE = "codebase"
    IMAGE = "image"
    REFERENCE = "reference"
    UNKNOWN = "unknown"


class RequirementPriority(StrEnum):
    METHODOLOGY = "methodology"
    INSTITUTION_TEMPLATE = "institution_template"
    USER = "user"
    EXAMPLE = "example"
    PROFILE = "profile"
    BUILTIN = "builtin"


class RequirementCategory(StrEnum):
    STRUCTURE = "structure"
    VOLUME = "volume"
    TITLE_PAGE = "title_page"
    PAGE_LAYOUT = "page_layout"
    TYPOGRAPHY = "typography"
    HEADINGS = "headings"
    TABLES = "tables"
    FIGURES = "figures"
    FORMULAS = "formulas"
    CODE_LISTINGS = "code_listings"
    PAGINATION = "pagination"
    CITATIONS = "citations"
    BIBLIOGRAPHY = "bibliography"
    APPENDICES = "appendices"
    CUSTOM = "custom"


class ClaimStatus(StrEnum):
    PENDING = "pending"
    SUPPORTED = "supported"
    DISPUTED = "disputed"
    UNSUPPORTED = "unsupported"


class FactOrigin(StrEnum):
    USER = "user"
    VERIFIED_SOURCE = "verified_source"
    CALCULATED = "calculated"
    SYNTHETIC = "synthetic"


class DataType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    PAUSED = "paused"
    WAITING_INPUT = "waiting_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    PAUSED = "paused"
    WAITING_INPUT = "waiting_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ArtifactKind(StrEnum):
    SOURCE_COPY = "source_copy"
    EXTRACTED_TEXT = "extracted_text"
    REQUIREMENTS = "requirements"
    BLUEPRINT = "blueprint"
    OUTLINE = "outline"
    DATASET = "dataset"
    IMAGE = "image"
    CHART = "chart"
    DIAGRAM = "diagram"
    MANUSCRIPT = "manuscript"
    DOCX = "docx"
    PDF = "pdf"
    PAGE_PREVIEW = "page_preview"
    QA_JSON = "qa_json"
    QA_HTML = "qa_html"
    OTHER = "other"


class QASeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    BLOCKER = "blocker"


class QAStatus(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class QualityStatus(StrEnum):
    """Publication-quality state, independent from a pipeline run lifecycle."""

    VALID = "valid"
    NEEDS_REPAIR = "needs_repair"
    WAITING_INPUT = "waiting_input"
    FAILED_QUALITY = "failed_quality"
    NON_PUBLISHABLE_SYNTHETIC_DEMO = "non_publishable_synthetic_demo"


class VisualKind(StrEnum):
    TABLE = "table"
    CHART = "chart"
    DIAGRAM = "diagram"
    FORMULA = "formula"
    IMAGE = "image"
    CODE_LISTING = "code_listing"


class ChartType(StrEnum):
    BAR = "bar"
    LINE = "line"
    PIE = "pie"
    SCATTER = "scatter"
    HISTOGRAM = "histogram"
    AREA = "area"


# Name used by the public plan; keep the shorter spelling convenient internally.
QAIssueSeverity = QASeverity


__all__ = [
    "ArtifactKind",
    "ChartType",
    "ClaimStatus",
    "DataType",
    "DomainProfile",
    "FactOrigin",
    "QAIssueSeverity",
    "QASeverity",
    "QAStatus",
    "RequirementCategory",
    "RequirementPriority",
    "RunStatus",
    "SourceRole",
    "StageStatus",
    "VisualKind",
    "WorkType",
]
