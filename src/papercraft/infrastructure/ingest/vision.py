"""OCR port with a deterministic local fake for scanned-input tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class OCRResult:
    text: str
    confidence: float
    tables: tuple[str, ...] = ()
    captions: tuple[str, ...] = ()
    low_confidence_numbers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("OCR confidence must be between 0 and 1")


class VisionOCRPort(Protocol):
    def recognize(self, image: bytes, *, page_number: int) -> OCRResult: ...


class FakeVision:
    """Fixture-friendly OCR fake keyed by page number or image digest."""

    def __init__(self, pages: dict[int | str, OCRResult] | None = None) -> None:
        self.pages = pages or {}

    def recognize(self, image: bytes, *, page_number: int) -> OCRResult:
        digest = hashlib.sha256(image).hexdigest()
        return self.pages.get(page_number, self.pages.get(digest, OCRResult("", 0.0)))


__all__ = ["FakeVision", "OCRResult", "VisionOCRPort"]
