"""Crossref, OpenAlex and DOI discovery at the verified network boundary."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from .bibliography import normalize_doi
from .url_verifier import URLVerificationResult, URLVerifier


@dataclass(frozen=True, slots=True)
class ScholarlyRecord:
    title: str
    landing_url: str
    source_api: str
    authors: tuple[str, ...] = ()
    year: int | None = None
    doi: str | None = None
    abstract: str = ""
    organization: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def canonical_url(self) -> str:
        return f"https://doi.org/{quote(self.doi, safe='/()') }" if self.doi else self.landing_url


def _clean_markup(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def _crossref_authors(item: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for author in item.get("author", []) or []:
        if not isinstance(author, dict):
            continue
        name = " ".join(
            part for part in (str(author.get("given", "")).strip(), str(author.get("family", "")).strip()) if part
        )
        if name:
            values.append(name)
    return tuple(values)


def _crossref_year(item: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "issued", "created"):
        value = item.get(key)
        if not isinstance(value, dict):
            continue
        parts = value.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                continue
    return None


class CrossrefClient:
    endpoint = "https://api.crossref.org/works"

    def __init__(self, verifier: URLVerifier | None = None) -> None:
        self.verifier = verifier or URLVerifier()

    def search(self, query: str, *, rows: int = 5) -> list[ScholarlyRecord]:
        if not query.strip():
            return []
        bounded_rows = max(1, min(rows, 20))
        url = f"{self.endpoint}?{urlencode({'query.bibliographic': query, 'rows': bounded_rows, 'select': 'DOI,title,author,published-print,published-online,issued,created,URL,abstract,publisher,type,ISBN'})}"
        result = self.verifier.verify(url)
        if not result.verified:
            return []
        try:
            payload = json.loads(result.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
        items = payload.get("message", {}).get("items", [])
        records: list[ScholarlyRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_titles = item.get("title") or []
            title = str(raw_titles[0] if isinstance(raw_titles, list) and raw_titles else "").strip()
            landing_url = str(item.get("URL") or "").strip()
            doi = normalize_doi(str(item.get("DOI") or ""))
            if not title or not (landing_url or doi):
                continue
            records.append(
                ScholarlyRecord(
                    title=title,
                    landing_url=landing_url or f"https://doi.org/{quote(doi or '', safe='/()')}",
                    source_api="crossref",
                    authors=_crossref_authors(item),
                    year=_crossref_year(item),
                    doi=doi,
                    abstract=_clean_markup(str(item.get("abstract") or "")),
                    organization=str(item.get("publisher") or ""),
                    metadata={
                        "type": str(item.get("type") or ""),
                        "isbn": [str(value) for value in item.get("ISBN", []) or []],
                    },
                )
            )
        return records


def _openalex_abstract(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in index.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            try:
                positioned.append((int(position), str(word)))
            except (TypeError, ValueError):
                continue
    return " ".join(word for _, word in sorted(positioned))


class OpenAlexClient:
    endpoint = "https://api.openalex.org/works"

    def __init__(self, verifier: URLVerifier | None = None) -> None:
        self.verifier = verifier or URLVerifier()

    def search(self, query: str, *, per_page: int = 5) -> list[ScholarlyRecord]:
        if not query.strip():
            return []
        url = f"{self.endpoint}?{urlencode({'search': query, 'per-page': max(1, min(per_page, 20))})}"
        result = self.verifier.verify(url)
        if not result.verified:
            return []
        try:
            payload = json.loads(result.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
        records: list[ScholarlyRecord] = []
        for item in payload.get("results", []) or []:
            if not isinstance(item, dict):
                continue
            raw_primary = item.get("primary_location")
            primary: dict[str, Any] = raw_primary if isinstance(raw_primary, dict) else {}
            landing_url = str(primary.get("landing_page_url") or item.get("doi") or item.get("id") or "").strip()
            title = str(item.get("display_name") or item.get("title") or "").strip()
            doi = normalize_doi(str(item.get("doi") or ""))
            if not title or not landing_url:
                continue
            authors = tuple(
                str(authorship.get("author", {}).get("display_name") or "").strip()
                for authorship in item.get("authorships", []) or []
                if isinstance(authorship, dict) and str(authorship.get("author", {}).get("display_name") or "").strip()
            )
            raw_source = primary.get("source")
            source: dict[str, Any] = raw_source if isinstance(raw_source, dict) else {}
            records.append(
                ScholarlyRecord(
                    title=title,
                    landing_url=landing_url,
                    source_api="openalex",
                    authors=authors,
                    year=int(item["publication_year"]) if item.get("publication_year") else None,
                    doi=doi,
                    abstract=_openalex_abstract(item.get("abstract_inverted_index")),
                    organization=str(source.get("display_name") or ""),
                    metadata={"openalex_id": str(item.get("id") or "")},
                )
            )
        return records


class DOIResolver:
    def __init__(self, verifier: URLVerifier | None = None) -> None:
        self.verifier = verifier or URLVerifier()

    def resolve(self, doi: str) -> URLVerificationResult:
        normalized = normalize_doi(doi)
        if not normalized:
            raise ValueError("Invalid DOI")
        return self.verifier.verify(f"https://doi.org/{quote(normalized, safe='/()')}")


class OfficialSourcePolicy:
    """Conservative classification for state and first-party technology sources."""

    public_suffixes = (".gov", ".gov.uk", ".gov.ru", ".gouv.fr", ".bund.de")
    technology_hosts = frozenset(
        {
            "ai.google.dev",
            "cloud.google.com",
            "docs.python.org",
            "learn.microsoft.com",
            "developer.mozilla.org",
            "openalex.org",
            "api.openalex.org",
            "crossref.org",
            "api.crossref.org",
        }
    )

    def is_official(self, url: str) -> bool:
        hostname = (urlsplit(url).hostname or "").casefold().rstrip(".")
        return hostname in self.technology_hosts or any(
            hostname.endswith(suffix) for suffix in self.public_suffixes
        )


class ScholarlyDiscovery:
    def __init__(
        self,
        crossref: CrossrefClient | None = None,
        openalex: OpenAlexClient | None = None,
    ) -> None:
        self.crossref = crossref or CrossrefClient()
        self.openalex = openalex or OpenAlexClient()

    def search(self, query: str, *, limit: int = 6) -> list[ScholarlyRecord]:
        collected: list[ScholarlyRecord] = []
        for client in (self.crossref, self.openalex):
            try:
                collected.extend(client.search(query, rows=limit) if isinstance(client, CrossrefClient) else client.search(query, per_page=limit))
            except Exception:
                continue
        unique: list[ScholarlyRecord] = []
        keys: set[str] = set()
        for record in collected:
            key = record.doi or record.landing_url.casefold()
            if key in keys:
                continue
            keys.add(key)
            unique.append(record)
            if len(unique) >= limit:
                break
        return unique


__all__ = [
    "CrossrefClient",
    "DOIResolver",
    "OfficialSourcePolicy",
    "OpenAlexClient",
    "ScholarlyDiscovery",
    "ScholarlyRecord",
]
