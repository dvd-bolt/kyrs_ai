"""Matplotlib adapter for validated :class:`~papercraft.domain.ChartSpec` objects."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from papercraft.domain import ChartSpec, ChartType, Dataset


class ChartRenderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ChartRenderResult:
    path: Path
    sha256: str
    width_pixels: int
    height_pixels: int
    renderer: str = "matplotlib"


class ChartRenderer:
    """Render charts without evaluating model-generated Python code."""

    _ALLOWED_OPTIONS: ClassVar[set[str]] = {
        "width_inches",
        "height_inches",
        "dpi",
        "grid",
        "legend",
        "stacked",
        "colors",
        "marker",
        "bins",
        "rotation",
        "show_values",
        "alpha",
    }

    def render(
        self,
        spec: ChartSpec,
        dataset: Dataset,
        output_path: str | os.PathLike[str],
    ) -> ChartRenderResult:
        if spec.dataset_id != dataset.id:
            raise ChartRenderError("chart dataset_id does not match the supplied dataset")
        output = Path(output_path).expanduser().resolve()
        if output.suffix.lower() not in {".png", ".svg"}:
            raise ChartRenderError("charts must be rendered to a .png or .svg file")
        unknown_options = set(spec.options) - self._ALLOWED_OPTIONS
        if unknown_options:
            raise ChartRenderError(f"unsupported chart options: {sorted(unknown_options)}")
        if not dataset.rows:
            raise ChartRenderError("cannot render an empty dataset")

        columns = {column.name for column in dataset.columns}
        required = {spec.x_column, *spec.y_columns}
        missing = required - columns
        if missing:
            raise ChartRenderError(f"dataset lacks chart columns: {sorted(missing)}")
        if not spec.y_columns:
            raise ChartRenderError("at least one y column is required")

        try:
            import matplotlib

            matplotlib.use("Agg", force=False)
            from matplotlib import pyplot as plt
        except ImportError as exc:
            raise ChartRenderError(
                "matplotlib is not installed; install the 'visuals' optional dependency"
            ) from exc

        options = spec.options
        width = _bounded_float(options.get("width_inches", 8.0), 3.0, 20.0, "width_inches")
        height = _bounded_float(
            options.get("height_inches", 5.0), 2.0, 16.0, "height_inches"
        )
        dpi = int(_bounded_float(options.get("dpi", 300), 72, 600, "dpi"))
        alpha = _bounded_float(options.get("alpha", 0.9), 0.05, 1.0, "alpha")
        rotation = _bounded_float(options.get("rotation", 0), -90, 90, "rotation")
        marker = str(options.get("marker", "o"))
        if marker not in {"o", "s", "^", "v", "D", "x", "+", ".", "none", ""}:
            raise ChartRenderError("unsupported marker")
        colors = _validated_colors(options.get("colors"))

        x_values = [row.get(spec.x_column) for row in dataset.rows]
        y_values = {
            column: [_numeric(row.get(column), f"{column}[{index}]") for index, row in enumerate(dataset.rows)]
            for column in spec.y_columns
        }
        figure, axis = plt.subplots(figsize=(width, height), dpi=dpi)
        try:
            self._plot(axis, spec, x_values, y_values, options, colors, marker, alpha)
            axis.set_title(spec.title)
            axis.set_xlabel(spec.x_label or spec.x_column)
            if spec.chart_type != ChartType.PIE:
                axis.set_ylabel(spec.y_label)
                axis.tick_params(axis="x", labelrotation=rotation)
                axis.grid(bool(options.get("grid", True)), axis="y", alpha=0.25)
                if bool(options.get("legend", len(spec.y_columns) > 1)):
                    axis.legend()
            figure.tight_layout()

            output.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{output.stem}.", suffix=output.suffix, dir=output.parent
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                figure.savefig(temporary, format=output.suffix[1:], dpi=dpi, bbox_inches="tight")
                if temporary.stat().st_size == 0:
                    raise ChartRenderError("matplotlib produced an empty file")
                os.replace(temporary, output)
            finally:
                temporary.unlink(missing_ok=True)
        finally:
            plt.close(figure)

        if output.suffix.lower() == ".svg":
            return ChartRenderResult(
                path=output,
                sha256=_sha256(output),
                width_pixels=0,
                height_pixels=0,
            )
        try:
            from PIL import Image

            with Image.open(output) as image:
                pixel_width, pixel_height = image.size
        except ImportError:
            pixel_width, pixel_height = int(width * dpi), int(height * dpi)
        return ChartRenderResult(
            path=output,
            sha256=_sha256(output),
            width_pixels=pixel_width,
            height_pixels=pixel_height,
        )

    def _plot(
        self,
        axis: Any,
        spec: ChartSpec,
        x_values: list[Any],
        y_values: dict[str, list[float]],
        options: dict[str, Any],
        colors: list[str] | None,
        marker: str,
        alpha: float,
    ) -> None:
        if spec.chart_type == ChartType.BAR:
            self._bar(axis, x_values, y_values, bool(options.get("stacked", False)), colors, alpha)
        elif spec.chart_type == ChartType.LINE:
            for index, (label, values) in enumerate(y_values.items()):
                axis.plot(
                    x_values,
                    values,
                    label=label,
                    marker=None if marker in {"", "none"} else marker,
                    color=_color(colors, index),
                )
        elif spec.chart_type == ChartType.AREA:
            axis.stackplot(
                x_values,
                *y_values.values(),
                labels=list(y_values),
                colors=colors,
                alpha=alpha,
            )
        elif spec.chart_type == ChartType.PIE:
            if len(y_values) != 1:
                raise ChartRenderError("pie charts require exactly one y column")
            values = next(iter(y_values.values()))
            if any(value < 0 for value in values) or sum(values) <= 0:
                raise ChartRenderError("pie values must be non-negative with a positive total")
            axis.pie(values, labels=[str(value) for value in x_values], autopct="%1.1f%%", colors=colors)
        elif spec.chart_type == ChartType.SCATTER:
            for index, (label, values) in enumerate(y_values.items()):
                numeric_x = [_numeric(value, f"{spec.x_column}[{i}]") for i, value in enumerate(x_values)]
                axis.scatter(numeric_x, values, label=label, color=_color(colors, index), alpha=alpha)
        elif spec.chart_type == ChartType.HISTOGRAM:
            bins = int(_bounded_float(options.get("bins", 10), 2, 100, "bins"))
            for index, (label, values) in enumerate(y_values.items()):
                axis.hist(values, bins=bins, label=label, color=_color(colors, index), alpha=alpha)
        else:
            raise ChartRenderError(f"unsupported chart type: {spec.chart_type}")

        if bool(options.get("show_values", False)) and spec.chart_type == ChartType.BAR:
            for container in axis.containers:
                axis.bar_label(container, fmt="%g", padding=2, fontsize=8)

    @staticmethod
    def _bar(
        axis: Any,
        x_values: list[Any],
        y_values: dict[str, list[float]],
        stacked: bool,
        colors: list[str] | None,
        alpha: float,
    ) -> None:
        labels = list(y_values)
        if stacked:
            bottom = [0.0] * len(x_values)
            for index, label in enumerate(labels):
                values = y_values[label]
                axis.bar(
                    x_values,
                    values,
                    bottom=bottom,
                    label=label,
                    color=_color(colors, index),
                    alpha=alpha,
                )
                bottom = [left + right for left, right in zip(bottom, values, strict=True)]
            return
        series_count = len(labels)
        width = 0.8 / series_count
        positions = list(range(len(x_values)))
        for index, label in enumerate(labels):
            offset = (index - (series_count - 1) / 2) * width
            axis.bar(
                [position + offset for position in positions],
                y_values[label],
                width=width,
                label=label,
                color=_color(colors, index),
                alpha=alpha,
            )
        axis.set_xticks(positions, [str(value) for value in x_values])


def render_chart(
    spec: ChartSpec, dataset: Dataset, output_path: str | os.PathLike[str]
) -> ChartRenderResult:
    return ChartRenderer().render(spec, dataset, output_path)


def accessible_chart_table(spec: ChartSpec, dataset: Dataset) -> tuple[list[str], list[list[str]]]:
    """Return the exact source values used by a chart for a future accessible renderer."""
    if spec.dataset_id != dataset.id:
        raise ChartRenderError("chart dataset_id does not match the supplied dataset")
    headers = [spec.x_label or spec.x_column, *spec.y_columns]
    columns = {column.name for column in dataset.columns}
    if set([spec.x_column, *spec.y_columns]) - columns:
        raise ChartRenderError("dataset lacks chart columns")
    return headers, [
        [str(row.get(column, "")) for column in [spec.x_column, *spec.y_columns]]
        for row in dataset.rows
    ]


def _numeric(value: Any, label: str) -> float:
    if isinstance(value, bool) or value is None:
        raise ChartRenderError(f"{label} is not numeric")
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ChartRenderError(f"{label} is not numeric") from exc
    if not math.isfinite(converted):
        raise ChartRenderError(f"{label} must be finite")
    return converted


def _bounded_float(value: Any, minimum: float, maximum: float, label: str) -> float:
    converted = _numeric(value, label)
    if not minimum <= converted <= maximum:
        raise ChartRenderError(f"{label} must be between {minimum} and {maximum}")
    return converted


def _validated_colors(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw or len(raw) > 20:
        raise ChartRenderError("colors must be a non-empty list of at most 20 strings")
    colors = [str(color) for color in raw]
    if any(len(color) > 32 or any(character in color for character in "();{}<>") for color in colors):
        raise ChartRenderError("invalid color value")
    return colors


def _color(colors: list[str] | None, index: int) -> str | None:
    return colors[index % len(colors)] if colors else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
