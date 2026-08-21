"""Legacy adapter to production Gemini image generation (no mock fallback)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from papercraft.config import AppSettings
from papercraft.infrastructure.gemini import GeminiGateway


def generate_ai_illustration(
    prompt: str,
    output_png_path: str,
    cascade_client: Any | None = None,
) -> str:
    if cascade_client is not None:
        raise RuntimeError(
            "Legacy CascadeLLMClient is disabled for image generation; configure the production Gemini gateway"
        )
    gateway = GeminiGateway(AppSettings.from_environment())
    output = gateway.generate_image(
        prompt=(
            "Professional academic illustration, accurate labels, clean composition, "
            f"high readability: {prompt}"
        ),
        destination=Path(output_png_path),
    )
    return str(output)


__all__ = ["generate_ai_illustration"]
