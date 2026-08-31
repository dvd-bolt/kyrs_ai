"""Conservative discovery of citable, downloadable public datasets.

Crossref and OpenAlex describe literature.  They must never be treated as a
dataset source merely because a work record happens to contain a URL.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlencode

from .url_verifier import URLPolicy, URLVerifier

_DATA_EXTENSIONS = (".csv", ".tsv", ".xlsx", ".json")
_DATA_CONTENT_TYPES = frozenset(
    {
        "text/csv",
        "text/tab-separated-values",
        "application/json",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",  # extension is checked as a second control
    }
)


@dataclass(frozen=True, slots=True)
class DiscoveredDataset:
    repository: str
    stable_id: str
    version: str
    title: str
    authors: tuple[str, ...]
    license: str
    landing_url: str
    download_url: str
    filename: str


@dataclass(frozen=True, slots=True)
class DatasetSnapshot:
    dataset: DiscoveredDataset
    retrieved_at: datetime
    sha256: str
    body: bytes


class DatasetDiscoveryPort(Protocol):
    def search(self, query: str, *, limit: int = 5) -> list[DiscoveredDataset]: ...

    def download(self, dataset: DiscoveredDataset) -> DatasetSnapshot: ...


def _is_tabular_file(filename: str) -> bool:
    return filename.casefold().endswith(_DATA_EXTENSIONS)


class _RepositoryDatasetDiscovery:
    repository: str

    def __init__(self, verifier: URLVerifier | None = None) -> None:
        self.verifier = verifier or URLVerifier()
        if hasattr(self.verifier, "policy") and isinstance(self.verifier.policy, URLPolicy):
            policy = self.verifier.policy
            self.download_verifier = URLVerifier(
                policy=URLPolicy(
                    allowed_schemes=policy.allowed_schemes,
                    allowed_ports=policy.allowed_ports,
                    allowed_content_types=policy.allowed_content_types | _DATA_CONTENT_TYPES,
                    max_redirects=policy.max_redirects,
                    max_response_bytes=policy.max_response_bytes,
                    timeout_seconds=policy.timeout_seconds,
                    user_agent=policy.user_agent,
                ),
                resolver=getattr(self.verifier, "resolver", None),
                transport=getattr(self.verifier, "transport", None),
            )
        else:
            self.download_verifier = self.verifier

    def download(self, dataset: DiscoveredDataset) -> DatasetSnapshot:
        if not _is_tabular_file(dataset.filename):
            raise ValueError(f"Unsupported dataset format: {dataset.filename}")
        result = self.download_verifier.verify(dataset.download_url)
        if not result.verified or not result.body:
            raise ValueError(f"Dataset download could not be verified: {dataset.download_url}")
        return DatasetSnapshot(
            dataset=dataset,
            retrieved_at=datetime.now(UTC),
            sha256=hashlib.sha256(result.body).hexdigest(),
            body=result.body,
        )


class ZenodoDatasetDiscovery(_RepositoryDatasetDiscovery):
    """Zenodo records with an explicitly downloadable tabular file."""

    repository = "zenodo"
    endpoint = "https://zenodo.org/api/records"

    def search(self, query: str, *, limit: int = 5) -> list[DiscoveredDataset]:
        if not query.strip():
            return []
        url = f"{self.endpoint}?{urlencode({'q': query, 'size': max(1, min(limit, 20))})}"
        response = self.verifier.verify(url)
        if not response.verified:
            return []
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
        records = payload.get("hits", {}).get("hits", []) if isinstance(payload, dict) else []
        result: list[DiscoveredDataset] = []
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict):
                continue
            metadata_raw = record.get("metadata")
            metadata: Mapping[str, object] = metadata_raw if isinstance(metadata_raw, Mapping) else {}
            stable_id = str(record.get("doi") or record.get("id") or "").strip()
            version = str(metadata.get("version") or record.get("created") or "").strip()
            licence_raw = metadata.get("license")
            licence: Mapping[str, object] = licence_raw if isinstance(licence_raw, Mapping) else {}
            license_id = str(licence.get("id") or "").strip()
            title = str(metadata.get("title") or "").strip()
            creators = metadata.get("creators")
            creator_items: list[object] = creators if isinstance(creators, list) else []
            authors = tuple(
                str(item.get("name") or "").strip()
                for item in creator_items
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            )
            links_raw = record.get("links")
            links: Mapping[str, object] = links_raw if isinstance(links_raw, Mapping) else {}
            landing_url = str(links.get("html") or links.get("self_html") or "").strip()
            for file in record.get("files", []) or []:
                if not isinstance(file, dict):
                    continue
                filename = str(file.get("key") or "").strip()
                file_links_raw = file.get("links")
                file_links: Mapping[str, object] = file_links_raw if isinstance(file_links_raw, Mapping) else {}
                download_url = str(file_links.get("content") or file_links.get("self") or "").strip()
                if all((stable_id, version, license_id, title, landing_url, download_url)) and _is_tabular_file(filename):
                    result.append(DiscoveredDataset(self.repository, stable_id, version, title, authors, license_id, landing_url, download_url, filename))
        return result


class DataCiteDatasetDiscovery(_RepositoryDatasetDiscovery):
    """DataCite records only when their metadata contains a direct data URL."""

    repository = "datacite"
    endpoint = "https://api.datacite.org/dois"

    def search(self, query: str, *, limit: int = 5) -> list[DiscoveredDataset]:
        if not query.strip():
            return []
        url = f"{self.endpoint}?{urlencode({'query': query, 'page[size]': max(1, min(limit, 20)), 'resource-type-id': 'dataset'})}"
        response = self.verifier.verify(url)
        if not response.verified:
            return []
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
        result: list[DiscoveredDataset] = []
        for item in payload.get("data", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict) or not isinstance(item.get("attributes"), dict):
                continue
            attrs = item["attributes"]
            stable_id = str(attrs.get("doi") or item.get("id") or "").strip()
            version = str(attrs.get("version") or "").strip()
            license_id = str(attrs.get("rightsList", [{}])[0].get("rights") or "").strip() if isinstance(attrs.get("rightsList"), list) and attrs.get("rightsList") else ""
            title = str((attrs.get("titles") or [{}])[0].get("title") or "").strip() if isinstance(attrs.get("titles"), list) else ""
            landing_url = str(attrs.get("url") or "").strip()
            authors = tuple(
                str(item.get("name") or "").strip()
                for item in attrs.get("creators", []) or []
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            )
            for related in attrs.get("relatedIdentifiers", []) or []:
                if not isinstance(related, dict):
                    continue
                download_url = str(related.get("relatedIdentifier") or "").strip()
                filename = download_url.rsplit("/", 1)[-1].split("?", 1)[0]
                if all((stable_id, version, license_id, title, landing_url, download_url)) and _is_tabular_file(filename):
                    result.append(DiscoveredDataset(self.repository, stable_id, version, title, authors, license_id, landing_url, download_url, filename))
        return result


class CompositeDatasetDiscovery:
    """Combines Zenodo and DataCite dataset discovery with deduplication."""

    def __init__(
        self,
        discoverers: tuple[DatasetDiscoveryPort, ...] | None = None,
        verifier: URLVerifier | None = None,
    ) -> None:
        if discoverers is not None:
            self._discoverers = discoverers
        else:
            v = verifier or URLVerifier()
            self._discoverers = (ZenodoDatasetDiscovery(v), DataCiteDatasetDiscovery(v))

    def search(self, query: str, *, limit: int = 5) -> list[DiscoveredDataset]:
        results: list[DiscoveredDataset] = []
        seen_ids: set[str] = set()
        per_disc_limit = max(1, limit)
        for disc in self._discoverers:
            try:
                found = disc.search(query, limit=per_disc_limit)
                for item in found:
                    if item.stable_id not in seen_ids and item.download_url not in seen_ids:
                        seen_ids.add(item.stable_id)
                        seen_ids.add(item.download_url)
                        results.append(item)
                        if len(results) >= limit:
                            return results
            except Exception:
                continue
        return results

    def download(self, dataset: DiscoveredDataset) -> DatasetSnapshot:
        for disc in self._discoverers:
            if getattr(disc, "repository", None) == dataset.repository:
                return disc.download(dataset)
        if self._discoverers:
            return self._discoverers[0].download(dataset)
        raise ValueError(f"No discoverer available for dataset {dataset.stable_id}")


__all__ = [
    "CompositeDatasetDiscovery",
    "DataCiteDatasetDiscovery",
    "DatasetDiscoveryPort",
    "DatasetSnapshot",
    "DiscoveredDataset",
    "ZenodoDatasetDiscovery",
]

