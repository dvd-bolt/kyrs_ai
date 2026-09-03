"""Gemini image adapter that verifies and normalizes provider output locally."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from papercraft.infrastructure.gemini import GeminiPort


class ImageRenderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ImageRenderResult:
    path: Path
    sha256: str
    width_pixels: int
    height_pixels: int
    model: str


class GeminiImageAdapter:
    """Generate one image, then prove it is a usable, non-corrupt PNG artifact."""

    def __init__(self, gateway: GeminiPort, *, model: str) -> None:
        self._gateway = gateway
        self._model = model

    def generate(self, *, prompt: str, destination: str | os.PathLike[str]) -> ImageRenderResult:
        output = Path(destination).expanduser().resolve()
        if output.suffix.lower() != ".png":
            raise ImageRenderError("normalized generated images must use .png")
        if not prompt.strip():
            raise ImageRenderError("image prompt must not be empty")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.provider")
        try:
            self._gateway.generate_image(prompt=prompt, destination=temporary)
            try:
                from PIL import Image

                with Image.open(temporary) as image:
                    image.load()
                    if image.width < 64 or image.height < 64:
                        raise ImageRenderError("generated image is too small")
                    descriptor, normalized_name = tempfile.mkstemp(
                        prefix=f".{output.stem}.", suffix=".png", dir=output.parent
                    )
                    os.close(descriptor)
                    normalized = Path(normalized_name)
                    try:
                        image.convert("RGB").save(normalized, format="PNG", optimize=True)
                        os.replace(normalized, output)
                    finally:
                        normalized.unlink(missing_ok=True)
                    width, height = image.size
            except ImageRenderError:
                raise
            except Exception as exc:
                raise ImageRenderError("Gemini returned a corrupt or unsupported image") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return ImageRenderResult(output, _sha256(output), width, height, self._model)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
