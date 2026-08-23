"""Durable, byte-exact web source snapshots and text extraction."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from uuid import uuid4

from papercraft.domain import Locator, SourceSnapshot

from .url_verifier import URLVerificationResult


class _HTMLSnapshotParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.metadata: dict[str, str] = {}
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if lowered in {"script", "style", "noscript", "svg"}:
            self._ignored += 1
        if lowered != "meta":
            return
        values = {key.casefold(): value or "" for key, value in attrs}
        name = (values.get("name") or values.get("property") or "").casefold()
        content = values.get("content", "").strip()
        if name and content:
            self.metadata.setdefault(name, content)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored and data.strip():
            self.text.append(data)


@dataclass(frozen=True, slots=True)
class SnapshotCaptureResult:
    snapshot: SourceSnapshot
    extracted_text: str


def _suffix(content_type: str | None) -> str:
    return {
        "text/html": ".html",
        "text/plain": ".txt",
        "application/json": ".json",
        "application/pdf": ".pdf",
        "application/xml": ".xml",
        "text/xml": ".xml",
    }.get(content_type or "", ".bin")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    match = re.search(r"\d{4}-\d{2}-\d{2}", value)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(0))
    except ValueError:
        return None


def _extract_text(body: bytes, content_type: str) -> tuple[str, dict[str, Any]]:
    if content_type == "application/pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return "", {}
        from io import BytesIO

        reader = PdfReader(BytesIO(body))
        pages = [str(page.extract_text() or "") for page in reader.pages]
        return "\n\n".join(pages).strip(), {"pages": len(pages)}
    decoded = body.decode("utf-8", errors="replace")
    if content_type == "text/html":
        parser = _HTMLSnapshotParser()
        parser.feed(decoded)
        text = re.sub(r"\s+", " ", " ".join(parser.text)).strip()
        return text, parser.metadata
    if content_type == "application/json":
        try:
            parsed = json.loads(decoded)
        except json.JSONDecodeError:
            return decoded.strip(), {}
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True), {}
    return re.sub(r"\s+", " ", decoded).strip(), {}


class SourceSnapshotStore:
    def __init__(self, snapshots_dir: Path) -> None:
        self.snapshots_dir = snapshots_dir.expanduser().resolve()

    def capture(
        self,
        *,
        project_id: str,
        source_id: str,
        canonical_url: str,
        verification: URLVerificationResult,
        doi: str | None = None,
        isbn: str | None = None,
        authors: list[str] | None = None,
        organization: str = "",
        publication_date: date | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SnapshotCaptureResult:
        if not verification.verified or not verification.body:
            raise ValueError("Only verified non-empty responses can become source snapshots")
        digest = hashlib.sha256(verification.body).hexdigest()
        if digest != verification.content_sha256:
            raise ValueError("Verified response digest changed before snapshot capture")
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        destination = (self.snapshots_dir / f"{digest}{_suffix(verification.content_type)}").resolve()
        destination.relative_to(self.snapshots_dir)
        if not destination.exists():
            temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(verification.body)
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
                    raise OSError("Snapshot write verification failed")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        content_type = verification.content_type or "application/octet-stream"
        extracted, page_metadata = _extract_text(verification.body, content_type)
        html_metadata = page_metadata if content_type == "text/html" else {}
        title = str(
            verification.title
            or html_metadata.get("og:title")
            or html_metadata.get("citation_title")
            or ""
        )
        discovered_authors = list(authors or [])
        if not discovered_authors:
            author = str(
                html_metadata.get("citation_author")
                or html_metadata.get("author")
                or ""
            ).strip()
            if author:
                discovered_authors = [author]
        discovered_org = organization or str(
            html_metadata.get("og:site_name") or html_metadata.get("application-name") or ""
        )
        discovered_date = publication_date or _parse_date(
            str(
                html_metadata.get("citation_publication_date")
                or html_metadata.get("article:published_time")
                or html_metadata.get("date")
                or ""
            )
        )
        snapshot = SourceSnapshot(
            project_id=project_id,
            source_id=source_id,
            canonical_url=canonical_url,
            final_url=verification.final_url,
            stored_path=str(destination),
            sha256=digest,
            content_type=content_type,
            size_bytes=len(verification.body),
            title=title,
            authors=discovered_authors,
            organization=discovered_org,
            publication_date=discovered_date,
            doi=doi,
            isbn=isbn,
            locator=Locator(source_id=source_id, url=verification.final_url, section="document"),
            metadata={
                "requested_url": verification.requested_url,
                "redirects": list(verification.redirects),
                "status_code": verification.status_code,
                "checked_ips": list(verification.checked_ips),
                "extracted_characters": len(extracted),
                **page_metadata,
                **(metadata or {}),
            },
        )
        return SnapshotCaptureResult(snapshot=snapshot, extracted_text=extracted)


__all__ = ["SnapshotCaptureResult", "SourceSnapshotStore"]
