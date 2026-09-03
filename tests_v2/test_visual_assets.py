from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from papercraft.domain import (
    ChartSpec,
    ChartType,
    Dataset,
    DatasetColumn,
    DiagramEdge,
    DiagramNode,
    DiagramSpec,
    FactOrigin,
)
from papercraft.infrastructure.gemini import FakeGeminiGateway
from papercraft.infrastructure.visuals import (
    ChartRenderError,
    GeminiImageAdapter,
    ImageRenderError,
    LocalDiagramRenderer,
    accessible_chart_table,
    render_chart,
)


def _dataset() -> Dataset:
    return Dataset(
        id="dataset-1",
        project_id="project-1",
        name="Выручка",
        origin=FactOrigin.CALCULATED,
        columns=[
            DatasetColumn(name="period", nullable=False),
            DatasetColumn(name="revenue", data_type="number", unit="тыс. руб.", nullable=False),
        ],
        rows=[{"period": "2024", "revenue": 10}, {"period": "2025", "revenue": 15}],
    )


def test_chart_keeps_source_points_labels_units_and_print_dpi(tmp_path: Path) -> None:
    dataset = _dataset()
    spec = ChartSpec(
        chart_type=ChartType.LINE,
        title="Выручка",
        dataset_id=dataset.id,
        x_column="period",
        y_columns=["revenue"],
        x_label="Год",
        y_label="тыс. руб.",
        options={"dpi": 300},
    )
    png = tmp_path / "chart.png"
    result = render_chart(spec, dataset, png)
    assert result.width_pixels >= 900
    assert result.height_pixels >= 500
    with Image.open(png) as image:
        assert image.format == "PNG"
        assert image.info["dpi"][0] == pytest.approx(300, abs=1)
    assert accessible_chart_table(spec, dataset) == (
        ["Год", "revenue"],
        [["2024", "10"], ["2025", "15"]],
    )
    svg = tmp_path / "chart.svg"
    render_chart(spec, dataset, svg)
    content = svg.read_text(encoding="utf-8")
    assert "Год" in content and "тыс. руб." in content


def test_chart_rejects_non_numeric_points(tmp_path: Path) -> None:
    dataset = _dataset().model_copy(update={"rows": [{"period": "2024", "revenue": "bad"}]})
    spec = ChartSpec(
        chart_type=ChartType.BAR,
        dataset_id=dataset.id,
        x_column="period",
        y_columns=["revenue"],
    )
    with pytest.raises(ChartRenderError, match="not numeric"):
        render_chart(spec, dataset, tmp_path / "chart.png")


def test_typed_diagram_writes_safe_svg_and_png(tmp_path: Path) -> None:
    spec = DiagramSpec(
        title="Процесс",
        nodes=[DiagramNode(id="start", label="Начало"), DiagramNode(id="end", label="Конец")],
        edges=[DiagramEdge(source="start", target="end", label="далее")],
    )
    renderer = LocalDiagramRenderer()
    svg = tmp_path / "diagram.svg"
    renderer.render(spec, svg)
    content = svg.read_text(encoding="utf-8")
    assert "Начало" in content and "Конец" in content and "script" not in content.casefold()
    png = tmp_path / "diagram.png"
    renderer.render(spec, png)
    with Image.open(png) as image:
        assert image.format == "PNG"


def test_image_adapter_rejects_corrupt_provider_payload(tmp_path: Path) -> None:
    gateway = FakeGeminiGateway()
    gateway.enqueue("generate_image", b"not an image")
    with pytest.raises(ImageRenderError, match="corrupt"):
        GeminiImageAdapter(gateway, model="gemini-image-test").generate(
            prompt="Academic illustration", destination=tmp_path / "image.png"
        )
