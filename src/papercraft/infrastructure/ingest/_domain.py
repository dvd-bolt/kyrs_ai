"""Small compatibility helpers for constructing domain models.

Keeping this adaptation at the infrastructure edge makes parsing code robust
to backwards-compatible domain schema additions while still returning real
domain objects.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from papercraft.domain import Locator, SourceFragment


def construct[T: BaseModel](model: type[T], /, **values: Any) -> T:
    """Construct a Pydantic domain model using its declared fields only."""

    accepted = {key: value for key, value in values.items() if key in model.model_fields}
    return model.model_validate(accepted)


def locator(
    *,
    source_id: str,
    path: str | Path,
    page: int | None = None,
    sheet: str | None = None,
    row: int | None = None,
    cell_range: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    section: str | None = None,
    url: str | None = None,
    details: dict[str, Any] | None = None,
) -> Locator:
    locator_details: dict[str, Any] = {"path": str(path)}
    if row is not None:
        locator_details["row"] = row
    locator_details.update(details or {})
    return construct(
        Locator,
        source_id=source_id,
        path=str(path),
        page=page,
        sheet=sheet,
        row=row,
        cell_range=cell_range,
        line_start=line_start,
        line_end=line_end,
        section=section,
        url=url,
        details=locator_details,
    )


def fragment(
    *,
    source_id: str,
    content: str,
    source_locator: Locator,
    metadata: dict[str, Any] | None = None,
    ordinal: str = "",
) -> SourceFragment:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    stable_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id}:{ordinal}:{digest}"))
    fragment_metadata = {**(metadata or {}), "untrusted_input": True}
    return construct(
        SourceFragment,
        id=stable_id,
        source_id=source_id,
        content=content,
        text=content,
        locator=source_locator,
        sha256=digest,
        token_count=max(1, len(content) // 4) if content else 0,
        metadata=fragment_metadata,
    )
