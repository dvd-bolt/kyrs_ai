from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class GeminiPort(Protocol):
    """Provider operations used by the application layer."""

    def health_check(self) -> None: ...

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
        role: str = "architect",
        system_instruction: str | None = None,
    ) -> Any: ...

    def upload_file(self, path: Path) -> Any: ...

    def delete_file(self, name: str) -> None: ...

    def generate_image(self, *, prompt: str, destination: Path) -> Path: ...
