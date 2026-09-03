"""Deterministic quality gates and portable QA reports."""

from .document import (
    DocumentInspectionError,
    DocxPackageInspection,
    PageLayoutFinding,
    inspect_docx_package,
    inspect_pdf_layout,
)
from .gates import DeterministicQualityGate, QAGateContext
from .reports import QAReportArtifacts, QAReportWriter, write_qa_report

__all__ = [
    "DeterministicQualityGate",
    "DocumentInspectionError",
    "DocxPackageInspection",
    "PageLayoutFinding",
    "QAGateContext",
    "QAReportArtifacts",
    "QAReportWriter",
    "inspect_docx_package",
    "inspect_pdf_layout",
    "write_qa_report",
]
