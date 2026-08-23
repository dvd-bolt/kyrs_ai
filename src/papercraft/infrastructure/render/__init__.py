"""Block-based DOCX rendering and local Office finalization."""

from .docx_renderer import (
    DocxRenderer,
    DocxRenderError,
    DocxRenderResult,
    RenderConfig,
    TemplateInspection,
    TitlePageInfo,
    inspect_docx_template,
)
from .finalizer import (
    DocumentFinalizer,
    FinalizationError,
    FinalizationResult,
    FinalizationUnavailableError,
    PDFResult,
)

__all__ = [
    "DocumentFinalizer",
    "DocxRenderError",
    "DocxRenderResult",
    "DocxRenderer",
    "FinalizationError",
    "FinalizationResult",
    "FinalizationUnavailableError",
    "PDFResult",
    "RenderConfig",
    "TemplateInspection",
    "TitlePageInfo",
    "inspect_docx_template",
]
