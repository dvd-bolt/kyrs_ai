"""Legacy adapter to the private, local diagram renderer."""

from __future__ import annotations

from papercraft.domain import DiagramSpec
from papercraft.infrastructure.visuals import LocalDiagramRenderer


def render_mermaid_to_png(mermaid_code: str, output_png_path: str) -> str:
    result = LocalDiagramRenderer().render(
        DiagramSpec(title="Диаграмма", language="mermaid", source=mermaid_code),
        output_png_path,
    )
    return str(result.path)


__all__ = ["render_mermaid_to_png"]
