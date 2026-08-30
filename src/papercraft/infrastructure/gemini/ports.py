from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)
_INTERACTION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,511}")


def validate_interaction_id(value: str) -> str:
    """Reject values that cannot be a provider interaction identifier.

    Interaction IDs are sent back to the provider in lookup, cancellation and
    deletion requests.  Keep validation strict and never reflect the rejected
    value in an exception, because it is provider-controlled data.
    """

    if not isinstance(value, str) or _INTERACTION_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("Unexpected Gemini interaction ID")
    return value


class GeminiPort(Protocol):
    """Provider operations used by the application layer."""

    def health_check(self, *, fail_fast: bool = False) -> None: ...

    def generate_text(
        self,
        *,
        prompt: str,
        role: str,
        system_instruction: str | None = None,
    ) -> str: ...

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: type[SchemaT],
        role: str,
        system_instruction: str | None = None,
        files: list[Any] | None = None,
    ) -> SchemaT: ...

    def search_grounded(
        self,
        *,
        prompt: str,
        role: str = "research",
        system_instruction: str | None = None,
    ) -> Any: ...

    def upload_file(self, path: Path) -> Any: ...

    def delete_file(self, name: str) -> None: ...

    def generate_image(self, *, prompt: str, destination: Path) -> Path: ...

    def embed_texts(
        self,
        texts: list[str],
        *,
        output_dimensionality: int = 768,
    ) -> list[list[float]]: ...

    def start_background_text(self, *, prompt: str, role: str) -> str: ...

    def cancel_interaction(self, interaction_id: str) -> str: ...

    def get_interaction_status(self, interaction_id: str) -> str | None: ...

    def delete_interaction(self, interaction_id: str) -> None: ...
