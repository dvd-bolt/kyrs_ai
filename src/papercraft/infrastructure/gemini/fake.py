from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from threading import RLock
from typing import Any, TypeVar

from pydantic import BaseModel

from .gateway import GroundedResult, RemoteFile
from .ports import validate_interaction_id

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class FakeGeminiGateway:
    """Explicit deterministic provider for unit and contract tests."""

    def __init__(self) -> None:
        self.responses: dict[str, list[Any]] = defaultdict(list)
        self.calls: list[dict[str, Any]] = []
        self.deleted_files: list[str] = []
        self.deleted_interactions: list[str] = []
        self._interaction_statuses: dict[str, str] = {}
        self._lock = RLock()

    def enqueue(self, operation: str, response: Any) -> None:
        with self._lock:
            self.responses[operation].append(response)

    def _take(self, operation: str) -> Any:
        with self._lock:
            return self._take_locked(operation)

    def _take_locked(self, operation: str) -> Any:
        queued = self.responses[operation]
        if not queued:
            raise AssertionError(f"No fake response queued for {operation}")
        response = queued.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def health_check(self, *, fail_fast: bool = False) -> None:
        with self._lock:
            self.calls.append({"operation": "health_check", "fail_fast": fail_fast})

    def generate_text(
        self,
        *,
        prompt: str,
        role: str,
        system_instruction: str | None = None,
    ) -> str:
        with self._lock:
            self.calls.append({"operation": "generate_text", "prompt": prompt, "role": role})
            value = self._take_locked("generate_text")
        return str(value)

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: type[SchemaT],
        role: str,
        system_instruction: str | None = None,
        files: list[Any] | None = None,
    ) -> SchemaT:
        with self._lock:
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
            value = self._take_locked("generate_structured")
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
        role: str = "research",
        system_instruction: str | None = None,
    ) -> GroundedResult:
        with self._lock:
            self.calls.append({"operation": "search_grounded", "prompt": prompt, "role": role})
            value = self._take_locked("search_grounded")
        if isinstance(value, GroundedResult):
            return value
        if isinstance(value, str):
            return GroundedResult(text=value, model=f"fake-{role}")
        return GroundedResult(**value)

    def upload_file(self, path: Path) -> RemoteFile:
        with self._lock:
            self.calls.append({"operation": "upload_file", "path": str(path)})
            value = self.responses["upload_file"].pop(0) if self.responses["upload_file"] else None
        if isinstance(value, RemoteFile):
            return value
        if isinstance(value, dict):
            return RemoteFile(**value)
        return RemoteFile(name=f"files/{path.stem}", uri=f"fake://{path.name}")

    def delete_file(self, name: str) -> None:
        with self._lock:
            self.deleted_files.append(name)

    def generate_image(self, *, prompt: str, destination: Path) -> Path:
        with self._lock:
            self.calls.append({"operation": "generate_image", "prompt": prompt})
            payload = self._take_locked("generate_image")
        if isinstance(payload, Path):
            return payload
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(bytes(payload))
        return destination

    def embed_texts(
        self,
        texts: list[str],
        *,
        output_dimensionality: int = 768,
    ) -> list[list[float]]:
        with self._lock:
            self.calls.append(
                {
                    "operation": "embed_texts",
                    "count": len(texts),
                    "output_dimensionality": output_dimensionality,
                }
            )
            queued = self.responses["embed_texts"]
            if queued:
                value = self._take_locked("embed_texts")
                return [[float(item) for item in row] for row in value]
        return [[0.0] * output_dimensionality for _ in texts]

    def start_background_text(self, *, prompt: str, role: str) -> str:
        with self._lock:
            self.calls.append({"operation": "start_background_text", "prompt": prompt, "role": role})
            queued = self.responses["start_background_text"]
            interaction_id = str(self._take_locked("start_background_text")) if queued else "v1_fake"
            interaction_id = validate_interaction_id(interaction_id)
            self._interaction_statuses[interaction_id] = "in_progress"
            return interaction_id

    def cancel_interaction(self, interaction_id: str) -> str:
        interaction_id = validate_interaction_id(interaction_id)
        with self._lock:
            self.calls.append({"operation": "cancel_interaction", "interaction_id": interaction_id})
            queued = self.responses["cancel_interaction"]
            status = str(self._take_locked("cancel_interaction")) if queued else "cancelled"
            normalized = status.casefold()
            if normalized in {"cancelled", "canceled"}:
                self._interaction_statuses[interaction_id] = "cancelled"
            return normalized

    def get_interaction_status(self, interaction_id: str) -> str | None:
        interaction_id = validate_interaction_id(interaction_id)
        with self._lock:
            self.calls.append({"operation": "get_interaction_status", "interaction_id": interaction_id})
            if interaction_id in self.deleted_interactions:
                return None
            queued = self.responses["get_interaction_status"]
            if queued:
                value = self._take_locked("get_interaction_status")
                if value is None:
                    return None
                status = str(value).casefold()
                self._interaction_statuses[interaction_id] = status
                return status
            return self._interaction_statuses.get(interaction_id)

    def delete_interaction(self, interaction_id: str) -> None:
        interaction_id = validate_interaction_id(interaction_id)
        with self._lock:
            self.calls.append({"operation": "delete_interaction", "interaction_id": interaction_id})
            if self.responses["delete_interaction"]:
                self._take_locked("delete_interaction")
            self._interaction_statuses.pop(interaction_id, None)
            if interaction_id not in self.deleted_interactions:
                self.deleted_interactions.append(interaction_id)
