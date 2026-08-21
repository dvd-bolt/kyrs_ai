"""Deprecated adapter to the production Gemini gateway.

The old implicit mock and Imagen cascade were removed. Missing credentials now
fail explicitly, just as they do in the v2 application.
"""

from __future__ import annotations

from pathlib import Path

from papercraft.config import AppSettings
from papercraft.infrastructure.gemini import GeminiGateway


class CascadeLLMClient:
    def __init__(self, api_key: str | None = None) -> None:
        settings = AppSettings.from_environment()
        if api_key:
            from pydantic import SecretStr

            settings.gemini_api_key = SecretStr(api_key)
        self.gateway = GeminiGateway(settings)
        self.rpd_tracker: dict[str, int] = {}

    def send_text_request(
        self,
        prompt: str,
        category: str = "content",
        system_instruction: str | None = None,
    ) -> str:
        roles = {"architect": "architect", "analyst": "critic", "content": "writer"}
        if category not in roles:
            raise ValueError(f"Unknown Gemini request category: {category}")
        return self.gateway.generate_text(
            prompt=prompt,
            role=roles[category],
            system_instruction=system_instruction,
        )

    def generate_image(self, prompt: str, output_path: str) -> str:
        return str(
            self.gateway.generate_image(prompt=prompt, destination=Path(output_path))
        )


__all__ = ["CascadeLLMClient"]
