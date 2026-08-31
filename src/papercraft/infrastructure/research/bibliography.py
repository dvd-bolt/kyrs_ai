"""Bibliographic normalization, validation and conservative deduplication."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from papercraft.domain import BibliographyEntry

_DOI = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", flags=re.IGNORECASE)
_TRACKING_PARAMETERS = {"fbclid", "gclid", "yclid", "ref", "source"}


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    normalized = re.sub(r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)", "", normalized, flags=re.IGNORECASE)
    return normalized.strip().rstrip(".,;").casefold() or None


def valid_doi(value: str | None) -> bool:
    normalized = normalize_doi(value)
    return bool(normalized and _DOI.fullmatch(normalized))


def normalize_isbn(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[^0-9Xx]", "", value).upper()
    return normalized or None


def valid_isbn(value: str | None) -> bool:
    normalized = normalize_isbn(value)
    if normalized is None:
        return False
    if len(normalized) == 10:
        if not re.fullmatch(r"\d{9}[\dX]", normalized):
            return False
        values = [int(character) for character in normalized[:9]] + [10 if normalized[-1] == "X" else int(normalized[-1])]
        return sum((10 - index) * number for index, number in enumerate(values)) % 11 == 0
    if len(normalized) == 13 and normalized.isdigit():
        expected = (10 - sum((1 if index % 2 == 0 else 3) * int(number) for index, number in enumerate(normalized[:12])) % 10) % 10
        return expected == int(normalized[-1])
    return False


def canonical_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme.casefold() not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    try:
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return None
    if "%" in host or "\\" in host:
        return None
    scheme = parts.scheme.casefold()
    default_port = 443 if scheme == "https" else 80
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = rendered_host if port in (None, default_port) else f"{rendered_host}:{port}"
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_PARAMETERS
    ]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, urlencode(sorted(query)), ""))


def _compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).strip()


def _normalized_authors(authors: Iterable[str]) -> list[str]:
    unique: dict[str, str] = {}
    for author in authors:
        rendered = re.sub(r"\s+", " ", author).strip(" ,;")
        if rendered:
            unique.setdefault(_compact(rendered), rendered)
    return list(unique.values())


@dataclass(frozen=True, slots=True)
class BibliographyValidation:
    entry: BibliographyEntry
    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class BibliographyValidator:
    def normalize(self, entry: BibliographyEntry) -> BibliographyEntry:
        updates = {
            "title": re.sub(r"\s+", " ", entry.title).strip(" ."),
            "authors": _normalized_authors(entry.authors),
            "doi": normalize_doi(entry.doi),
            "isbn": normalize_isbn(entry.isbn),
            "url": canonical_url(entry.url),
        }
        return entry.model_copy(update=updates)

    def validate(self, entry: BibliographyEntry) -> BibliographyValidation:
        normalized = self.normalize(entry)
        errors: list[str] = []
        warnings: list[str] = []
        if len(normalized.title) < 3:
            errors.append("title-too-short")
        if entry.doi and not valid_doi(entry.doi):
            errors.append("invalid-doi")
        if entry.isbn and not valid_isbn(entry.isbn):
            errors.append("invalid-isbn")
        if entry.url and canonical_url(entry.url) is None:
            errors.append("invalid-url")
        if normalized.year is not None and not 1000 <= normalized.year <= date.today().year + 1:
            errors.append("implausible-year")
        if not normalized.authors and not normalized.publisher:
            warnings.append("missing-author-or-organization")
        if normalized.year is None:
            warnings.append("missing-year")
        if normalized.url and normalized.accessed_on is None:
            warnings.append("missing-access-date")
        if normalized.accessed_on and normalized.accessed_on > date.today():
            errors.append("future-access-date")
        if not any((normalized.doi, normalized.isbn, normalized.url, normalized.source_id)):
            warnings.append("no-verifiable-identifier")
        return BibliographyValidation(normalized, not errors, tuple(errors), tuple(warnings))


def format_gost_bibliography(entry: BibliographyEntry) -> str:
    """Render a deterministic ГОСТ-base reference from structured fields.

    ``citation_text`` is intentionally ignored: it is model/user free text and
    cannot be a trustworthy final bibliographic record.  This is a base format,
    not a claim of conformance to a particular journal's house style.
    """
    normalized = BibliographyValidator().normalize(entry)
    responsibility = ", ".join(normalized.authors)
    parts = [f"{responsibility}." if responsibility else "", normalized.title.rstrip(".") + "."]
    if normalized.publisher:
        parts.append(f"— {normalized.publisher}.")
    if normalized.year:
        parts.append(f"— {normalized.year}.")
    if normalized.doi:
        parts.append(f"— DOI: {normalized.doi}.")
    elif normalized.isbn:
        parts.append(f"— ISBN {normalized.isbn}.")
    if normalized.url:
        access = (
            f" (дата обращения: {normalized.accessed_on.strftime('%d.%m.%Y')})"
            if normalized.accessed_on
            else ""
        )
        parts.append(f"— URL: {normalized.url}{access}.")
    return " ".join(part for part in parts if part).replace("..", ".")


@dataclass(frozen=True, slots=True)
class DeduplicationResult:
    entries: tuple[BibliographyEntry, ...]
    merged_ids: Mapping[str, str] = field(default_factory=dict)


class BibliographyDeduplicator:
    def __init__(self, validator: BibliographyValidator | None = None) -> None:
        self.validator = validator or BibliographyValidator()

    def deduplicate(self, entries: Iterable[BibliographyEntry]) -> DeduplicationResult:
        unique: list[BibliographyEntry] = []
        merged_ids: dict[str, str] = {}
        for raw_entry in entries:
            entry = self.validator.normalize(raw_entry)
            duplicate_index = next(
                (index for index, candidate in enumerate(unique) if self._same(candidate, entry)),
                None,
            )
            if duplicate_index is None:
                unique.append(entry)
                continue
            retained = unique[duplicate_index]
            merged_ids[entry.id] = retained.id
            unique[duplicate_index] = self._merge(retained, entry)
        return DeduplicationResult(tuple(unique), merged_ids)

    @staticmethod
    def _same(left: BibliographyEntry, right: BibliographyEntry) -> bool:
        left_doi, right_doi = normalize_doi(left.doi), normalize_doi(right.doi)
        if left_doi and right_doi and valid_doi(left_doi) and valid_doi(right_doi):
            return left_doi == right_doi
        left_isbn, right_isbn = normalize_isbn(left.isbn), normalize_isbn(right.isbn)
        if left_isbn and right_isbn and valid_isbn(left_isbn) and valid_isbn(right_isbn):
            return left_isbn == right_isbn
        left_url, right_url = canonical_url(left.url), canonical_url(right.url)
        if left_url and right_url:
            return left_url == right_url
        if left.year and right.year and left.year != right.year:
            return False
        title_similarity = SequenceMatcher(None, _compact(left.title), _compact(right.title)).ratio()
        if title_similarity < 0.94:
            return False
        left_authors = {_compact(author) for author in left.authors}
        right_authors = {_compact(author) for author in right.authors}
        if left_authors and right_authors:
            return bool(left_authors & right_authors)
        return left.year is not None and left.year == right.year

    @staticmethod
    def _merge(primary: BibliographyEntry, duplicate: BibliographyEntry) -> BibliographyEntry:
        authors = _normalized_authors([*primary.authors, *duplicate.authors])
        metadata: dict[str, Any] = {**duplicate.metadata, **primary.metadata}
        raw_duplicate_ids = metadata.get("duplicate_entry_ids", [])
        existing_duplicate_ids = (
            {str(item) for item in raw_duplicate_ids}
            if isinstance(raw_duplicate_ids, list)
            else set()
        )
        metadata["duplicate_entry_ids"] = sorted(
            existing_duplicate_ids | {duplicate.id}
        )
        updates: dict[str, Any] = {"authors": authors, "metadata": metadata}
        for field_name in (
            "year", "publisher", "doi", "isbn", "url", "accessed_on", "source_id", "citation_text"
        ):
            if not getattr(primary, field_name) and getattr(duplicate, field_name):
                updates[field_name] = getattr(duplicate, field_name)
        if primary.source_type == "other" and duplicate.source_type != "other":
            updates["source_type"] = duplicate.source_type
        if len(duplicate.title) > len(primary.title):
            updates["title"] = duplicate.title
        return primary.model_copy(update=updates)


def deduplicate_bibliography(entries: Iterable[BibliographyEntry]) -> list[BibliographyEntry]:
    return list(BibliographyDeduplicator().deduplicate(entries).entries)


def validate_bibliography_entry(entry: BibliographyEntry) -> BibliographyValidation:
    return BibliographyValidator().validate(entry)


__all__ = [
    "BibliographyDeduplicator",
    "BibliographyValidation",
    "BibliographyValidator",
    "DeduplicationResult",
    "canonical_url",
    "deduplicate_bibliography",
    "format_gost_bibliography",
    "normalize_doi",
    "normalize_isbn",
    "valid_doi",
    "valid_isbn",
    "validate_bibliography_entry",
]
