from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from .gateway import GroundedResult, RemoteFile

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class FakeGeminiGateway:
    """Explicit deterministic provider for unit and contract tests."""

    def __init__(self) -> None:
        self.responses: dict[str, list[Any]] = defaultdict(list)
        self.calls: list[dict[str, Any]] = []
        self.deleted_files: list[str] = []

    def enqueue(self, operation: str, response: Any) -> None:
        self.responses[operation].append(response)

    def _take(self, operation: str) -> Any:
        queued = self.responses[operation]
        if not queued:
            raise AssertionError(f"No fake response queued for {operation}")
        response = queued.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def health_check(self) -> None:
        self.calls.append({"operation": "health_check"})

    def generate_text(
        self,
        *,
        prompt: str,
        role: str,
        system_instruction: str | None = None,
    ) -> str:
        self.calls.append({"operation": "generate_text", "prompt": prompt, "role": role})
        return str(self._take("generate_text"))

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: type[SchemaT],
        role: str,
        system_instruction: str | None = None,
        files: list[Any] | None = None,
    ) -> SchemaT:
        self.calls.append(
            {
                "operation": "generate_structured",
                "prompt": prompt,
                "schema": schema.__name__,
                "role": role,
                "system_instruction": system_instruction,
                "files": files or [],
            }
        )
        value = self._take("generate_structured")
        if callable(value):
            value = value(
                schema=schema,
                prompt=prompt,
                role=role,
                files=files or [],
            )
        if isinstance(value, schema):
            return value
        if isinstance(value, str):
            return schema.model_validate_json(value)
        return schema.model_validate(value)

    def search_grounded(
        self,
        *,
        prompt: str,
        role: str = "architect",
        system_instruction: str | None = None,
    ) -> GroundedResult:
        self.calls.append({"operation": "search_grounded", "prompt": prompt, "role": role})
        value = self._take("search_grounded")
        if isinstance(value, GroundedResult):
            return value
        if isinstance(value, str):
            return GroundedResult(text=value, model=f"fake-{role}")
        return GroundedResult(**value)

    def upload_file(self, path: Path) -> RemoteFile:
        self.calls.append({"operation": "upload_file", "path": str(path)})
        value = self.responses["upload_file"].pop(0) if self.responses["upload_file"] else None
        if isinstance(value, RemoteFile):
            return value
        if isinstance(value, dict):
            return RemoteFile(**value)
        return RemoteFile(name=f"files/{path.stem}", uri=f"fake://{path.name}")

    def delete_file(self, name: str) -> None:
        self.deleted_files.append(name)

    def generate_image(self, *, prompt: str, destination: Path) -> Path:
        self.calls.append({"operation": "generate_image", "prompt": prompt})
        payload = self._take("generate_image")
        if isinstance(payload, Path):
            return payload
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(bytes(payload))
        return destination
