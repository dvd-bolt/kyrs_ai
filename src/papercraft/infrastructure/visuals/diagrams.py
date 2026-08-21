"""Offline diagram rendering with a dependency-free visual fallback."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from papercraft.domain import DiagramSpec


class DiagramRenderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DiagramRenderResult:
    path: Path
    sha256: str
    renderer: str
    warnings: tuple[str, ...] = ()


class LocalDiagramRenderer:
    """Render Mermaid locally, falling back to a deterministic Pillow diagram."""

    _BLOCKED = re.compile(
        r"(?i)(%%\s*\{\s*init|\bclick\b|javascript\s*:|file\s*:|<\s*script|<\s*iframe|\bhref\b)"
    )

    def __init__(self, *, mermaid_cli: str | None = None, timeout_seconds: float = 45) -> None:
        self.mermaid_cli = mermaid_cli
        self.timeout_seconds = timeout_seconds

    def render(
        self, spec: DiagramSpec, output_path: str | os.PathLike[str]
    ) -> DiagramRenderResult:
        output = Path(output_path).expanduser().resolve()
        if output.suffix.lower() != ".png":
            raise DiagramRenderError("diagrams must be rendered to a .png file")
        self._validate(spec)
        output.parent.mkdir(parents=True, exist_ok=True)

        executable = self.mermaid_cli or shutil.which("mmdc")
        warnings: list[str] = []
        if spec.language == "mermaid" and executable:
            try:
                self._render_mermaid_cli(spec.source, output, executable)
                return DiagramRenderResult(output, _sha256(output), "mermaid-cli")
            except (OSError, subprocess.SubprocessError, DiagramRenderError) as exc:
                warnings.append(f"Mermaid CLI unavailable or failed: {exc}")

        try:
            self._render_fallback(spec, output)
        except ImportError as exc:
            raise DiagramRenderError("Pillow is required for local diagram fallback") from exc
        return DiagramRenderResult(output, _sha256(output), "pillow-fallback", tuple(warnings))

    def _validate(self, spec: DiagramSpec) -> None:
        source = spec.source
        if len(source) > 100_000:
            raise DiagramRenderError("diagram source exceeds 100,000 characters")
        if "\x00" in source or self._BLOCKED.search(source):
            raise DiagramRenderError("diagram source contains an unsafe directive")

    def _render_mermaid_cli(self, source: str, output: Path, executable: str) -> None:
        with tempfile.TemporaryDirectory(prefix="papercraft-diagram-") as temporary_dir:
            temporary = Path(temporary_dir)
            source_path = temporary / "diagram.mmd"
            rendered = temporary / "diagram.png"
            source_path.write_text(source, encoding="utf-8")
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            completed = subprocess.run(
                [
                    executable,
                    "-i",
                    str(source_path),
                    "-o",
                    str(rendered),
                    "-b",
                    "white",
                    "-s",
                    "2",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                creationflags=creationflags,
            )
            if completed.returncode != 0 or not rendered.exists() or rendered.stat().st_size == 0:
                details = (completed.stderr or completed.stdout or "unknown error").strip()[-500:]
                raise DiagramRenderError(details)
            temporary_output = output.with_name(f".{output.name}.tmp")
            shutil.copyfile(rendered, temporary_output)
            os.replace(temporary_output, output)

    def _render_fallback(self, spec: DiagramSpec, output: Path) -> None:
        from PIL import Image, ImageDraw

        nodes, edges = _parse_nodes_and_edges(spec.source, spec.language)
        if not nodes:
            nodes = [(f"node_{index}", line.strip()) for index, line in enumerate(spec.source.splitlines()) if line.strip()]
        if not nodes:
            nodes = [("empty", "Диаграмма")]
        nodes = nodes[:60]
        known_ids = {node_id for node_id, _ in nodes}
        edges = [(left, right) for left, right in edges if left in known_ids and right in known_ids][:120]

        width = 1600
        box_width = 430
        box_height = 125
        gap_y = 65
        columns = 2 if len(nodes) > 5 else 1
        rows = (len(nodes) + columns - 1) // columns
        height = max(450, 130 + rows * (box_height + gap_y))
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        font = _load_font(27)
        title_font = _load_font(34)
        positions: dict[str, tuple[int, int, int, int]] = {}
        draw.text((width // 2, 35), spec.title or "Диаграмма", font=title_font, fill="#111827", anchor="ma")

        column_centers = [width // 2] if columns == 1 else [width // 3, width * 2 // 3]
        for index, (node_id, label) in enumerate(nodes):
            column = index % columns
            row = index // columns
            center_x = column_centers[column]
            top = 105 + row * (box_height + gap_y)
            left = center_x - box_width // 2
            bounds = (left, top, left + box_width, top + box_height)
            positions[node_id] = bounds
            draw.rounded_rectangle(bounds, radius=18, fill="#EEF4FF", outline="#315EA8", width=4)
            wrapped = "\n".join(textwrap.wrap(_clean_label(label), width=28)[:3])
            draw.multiline_text(
                (center_x, top + box_height // 2),
                wrapped,
                font=font,
                fill="#111827",
                anchor="mm",
                align="center",
                spacing=5,
            )

        for left_id, right_id in edges:
            left_box, right_box = positions[left_id], positions[right_id]
            start = ((left_box[0] + left_box[2]) // 2, left_box[3])
            end = ((right_box[0] + right_box[2]) // 2, right_box[1])
            if end[1] <= start[1]:
                start = (left_box[2], (left_box[1] + left_box[3]) // 2)
                end = (right_box[0], (right_box[1] + right_box[3]) // 2)
            draw.line((start, end), fill="#526276", width=4)
            _arrow_head(draw, start, end)

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.stem}.", suffix=".png", dir=output.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            image.save(temporary, "PNG", optimize=True)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)


def render_diagram(
    spec: DiagramSpec, output_path: str | os.PathLike[str]
) -> DiagramRenderResult:
    return LocalDiagramRenderer().render(spec, output_path)


def _parse_nodes_and_edges(source: str, language: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    nodes: dict[str, str] = {}
    edges: list[tuple[str, str]] = []
    if language == "mermaid":
        node_pattern = re.compile(
            r"(?m)(?<![\w-])([A-Za-z_][\w-]*)\s*(?:\[([^\]]+)\]|\(([^)]+)\)|\{([^}]+)\})"
        )
        for match in node_pattern.finditer(source):
            nodes.setdefault(match.group(1), next(group for group in match.groups()[1:] if group is not None))
        edge_pattern = re.compile(r"(?m)([A-Za-z_][\w-]*)\s*(?:-->|---|-.->|==>)\s*(?:\|[^|]*\|\s*)?([A-Za-z_][\w-]*)")
        for left, right in edge_pattern.findall(source):
            nodes.setdefault(left, left)
            nodes.setdefault(right, right)
            edges.append((left, right))
    else:
        edge_pattern = re.compile(r'(?m)"?([A-Za-z_][\w-]*)"?\s*->\s*"?([A-Za-z_][\w-]*)"?')
        for left, right in edge_pattern.findall(source):
            nodes.setdefault(left, left)
            nodes.setdefault(right, right)
            edges.append((left, right))
        label_pattern = re.compile(r'(?m)([A-Za-z_][\w-]*)\s*\[\s*label\s*=\s*"([^"]+)"')
        for node_id, label in label_pattern.findall(source):
            nodes[node_id] = label
    return list(nodes.items()), edges


def _clean_label(label: str) -> str:
    label = re.sub(r"<[^>]+>", " ", label)
    return re.sub(r"\s+", " ", label).strip().strip('"')[:180]


def _load_font(size: int) -> Any:
    from PIL import ImageFont

    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _arrow_head(draw: Any, start: tuple[int, int], end: tuple[int, int]) -> None:
    import math

    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    length = 18
    spread = math.pi / 7
    points: list[tuple[float, float]] = [(float(end[0]), float(end[1]))]
    for direction in (angle + math.pi - spread, angle + math.pi + spread):
        points.append((end[0] + length * math.cos(direction), end[1] + length * math.sin(direction)))
    draw.polygon(points, fill="#526276")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
