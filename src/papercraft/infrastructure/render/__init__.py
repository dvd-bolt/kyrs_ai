"""Block-based DOCX rendering and local Office finalization."""

from .docx_renderer import (
    DocxRenderer,
    DocxRenderError,
    DocxRenderResult,
    RenderConfig,
    TitlePageInfo,
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
    "TitlePageInfo",
]
