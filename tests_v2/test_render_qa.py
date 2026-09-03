from __future__ import annotations

import importlib.util
import json
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from docx import Document

from papercraft.domain import (
    BibliographyEntry,
    ChartSpec,
    ChartType,
    Citation,
    Dataset,
    DatasetColumn,
    DataType,
    DiagramSpec,
    FactOrigin,
    FactRecord,
    FigureBlock,
    HeadingBlock,
    Manuscript,
    ParagraphBlock,
    QAStatus,
    TableBlock,
    TableSpec,
)
from papercraft.infrastructure.calculations import (
    AccountKind,
    CalculationError,
    CalculationOperation,
    CalculationRequest,
    FactLedger,
    JournalEntry,
    SyntheticColumnSpec,
    SyntheticDatasetFactory,
    SyntheticDatasetSpec,
    validate_double_entry,
)
from papercraft.infrastructure.calculations.synthetic import Distribution
from papercraft.infrastructure.qa import (
    DeterministicQualityGate,
    QAGateContext,
    QAReportWriter,
)
from papercraft.infrastructure.render import DocxRenderer, RenderConfig
from papercraft.infrastructure.visuals import ChartRenderer, LocalDiagramRenderer
from papercraft.profiles.models import ProfilePolicy, ProfileSectionTemplate, WorkProfile


def _qa_profile() -> WorkProfile:
    return WorkProfile(
        id="qa-test",
        display_name="QA test",
        work_type="coursework",
        description="Neutral deterministic QA fixture",
        sections=[
            ProfileSectionTemplate(
                key="body", title="Body", target_words=100, purpose="Test"
            )
        ],
        policy=ProfilePolicy(voice="academic", minimum_sources=0),
    )


def test_fact_ledger_calculates_without_expression_evaluation() -> None:
    facts = [
        FactRecord(
            id="revenue",
            project_id="p1",
            name="Revenue",
            value=125,
            unit="RUB",
            origin=FactOrigin.VERIFIED_SOURCE,
            source_id="source-1",
        ),
        FactRecord(
            id="cost",
            project_id="p1",
            name="Cost",
            value=80,
            unit="RUB",
            origin=FactOrigin.VERIFIED_SOURCE,
            source_id="source-1",
        ),
    ]
    ledger = FactLedger("p1", facts)
    calculation, output = ledger.calculate(
        CalculationRequest(
            name="Profit",
            operation=CalculationOperation.SUBTRACT,
            input_fact_ids=("revenue", "cost"),
            output_name="Profit",
            output_unit="RUB",
        )
    )
    assert output.value == 45
    assert output.calculation_id == calculation.id
    assert output.origin == FactOrigin.CALCULATED

    with pytest.raises(CalculationError, match="division by zero"):
        zero = FactRecord(
            id="zero",
            project_id="p1",
            name="Zero",
            value=0,
            origin=FactOrigin.USER,
        )
        ledger.add(zero)
        ledger.calculate(
            CalculationRequest(
                name="Unsafe division",
                operation=CalculationOperation.DIVIDE,
                input_fact_ids=("revenue", "zero"),
                output_name="Bad",
            )
        )


def test_finance_validation_checks_account_balances() -> None:
    entries = [
        JournalEntry("e1", "51", "62", Decimal("1000"), occurred_on=date(2025, 1, 2)),
        JournalEntry("e2", "20", "10", Decimal("300"), occurred_on=date(2025, 1, 3)),
    ]
    result = validate_double_entry(
        entries,
        opening_balances={"10": "1000", "62": "-1000"},
        account_kinds={
            "10": AccountKind.ACTIVE,
            "20": AccountKind.ACTIVE,
            "51": AccountKind.ACTIVE,
            "62": AccountKind.PASSIVE,
        },
        expected_year=2025,
    )
    assert result.is_valid
    assert result.accounts["10"].closing_balance == Decimal("700.00")
    assert result.total_debit == result.total_credit == Decimal("1300.00")

    invalid = validate_double_entry([JournalEntry("bad", "51", "51", Decimal("10"))])
    assert not invalid.is_valid
    assert {issue.code for issue in invalid.issues} == {"same_account"}


def test_synthetic_dataset_is_seeded_and_has_provenance() -> None:
    spec = SyntheticDatasetSpec(
        project_id="p1",
        name="Survey",
        row_count=8,
        seed=20250813,
        purpose="Demonstration survey explicitly marked as synthetic",
        columns=(
            SyntheticColumnSpec("respondent", DataType.INTEGER, Distribution.SEQUENCE),
            SyntheticColumnSpec(
                "score",
                DataType.INTEGER,
                Distribution.INTEGER,
                parameters={"minimum": 1, "maximum": 5},
            ),
        ),
    )
    factory = SyntheticDatasetFactory()
    first = factory.generate(spec)
    second = factory.generate(spec)
    assert first.rows == second.rows
    assert first.origin == FactOrigin.SYNTHETIC
    assert first.synthetic_seed == 20250813
    assert first.generation_method
    assert first.metadata["synthetic"] is True


@pytest.mark.skipif(importlib.util.find_spec("matplotlib") is None, reason="matplotlib not installed")
def test_chart_renderer_uses_declarative_spec(tmp_path: Path) -> None:
    dataset = Dataset(
        id="sales",
        project_id="p1",
        name="Sales",
        columns=[
            DatasetColumn(name="year", data_type=DataType.INTEGER, nullable=False),
            DatasetColumn(name="value", data_type=DataType.NUMBER, nullable=False),
        ],
        rows=[{"year": 2023, "value": 10}, {"year": 2024, "value": 15}],
        origin=FactOrigin.VERIFIED_SOURCE,
        source_ids=["source-1"],
        repository="zenodo",
        stable_id="10.5281/zenodo.12345",
        version="1.0",
        license="CC-BY-4.0",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        snapshot_sha256="a" * 64,
    )
    spec = ChartSpec(
        chart_type=ChartType.BAR,
        title="Sales",
        dataset_id="sales",
        x_column="year",
        y_columns=["value"],
        y_label="RUB",
        options={"show_values": True, "dpi": 100},
    )
    result = ChartRenderer().render(spec, dataset, tmp_path / "chart.png")
    assert result.path.is_file()
    assert result.width_pixels > 0
    assert len(result.sha256) == 64


def test_local_diagram_fallback_never_needs_remote_service(tmp_path: Path) -> None:
    renderer = LocalDiagramRenderer(mermaid_cli=str(tmp_path / "missing-mmdc"))
    result = renderer.render(
        DiagramSpec(
            title="Pipeline",
            source="flowchart TD\nA[Sources] --> B[Analysis]\nB --> C[Document]",
        ),
        tmp_path / "diagram.png",
    )
    assert result.path.is_file()
    assert result.renderer == "pillow-fallback"

    with pytest.raises(Exception, match="unsafe"):
        renderer.render(
            DiagramSpec(title="Unsafe", source="flowchart TD\nclick A href \"file:///secret\""),
            tmp_path / "unsafe.png",
        )


def _complete_manuscript(image_artifact_id: str) -> Manuscript:
    return Manuscript(
        project_id="p1",
        title="Evidence-backed work",
        metadata={
            "title_page": {
                "university": "Test University",
                "work_type": "COURSEWORK",
                "student": "Student A.",
                "supervisor": "Supervisor B.",
                "city": "Moscow",
                "year": 2026,
            }
        },
        blocks=[
            HeadingBlock(text="INTRODUCTION", level=1),
            ParagraphBlock(text="The source data were verified and used consistently throughout the work."),
            TableBlock(
                spec=TableSpec(
                    caption="Core indicators",
                    dataset_id="core-indicators",
                    headers=["Indicator", "Value"],
                    rows=[["Revenue", 125], ["Cost", 80]],
                )
            ),
            FigureBlock(caption="Verified process diagram", artifact_id=image_artifact_id),
        ],
        bibliography=[
            BibliographyEntry(
                title="Official documentation",
                authors=["A. Author"],
                year=2025,
                publisher="Publisher",
                url="https://example.org/source",
                accessed_on=date(2026, 8, 13),
            )
        ],
    )


def test_docx_renderer_emits_word_fields_and_blocks(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "figure.png"
    Image.new("RGB", (800, 450), "white").save(image_path)
    manuscript = _complete_manuscript("figure-1")
    output = tmp_path / "work.docx"
    result = DocxRenderer(RenderConfig()).render(
        manuscript,
        output,
        artifact_paths={"figure-1": image_path},
    )
    assert result.path == output.resolve()
    assert not result.unresolved_artifact_ids
    with zipfile.ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml")
        page_number_parts = [
            archive.read(name)
            for name in archive.namelist()
            if name.startswith(("word/header", "word/footer")) and name.endswith(".xml")
        ]
        settings_xml = archive.read("word/settings.xml")
    assert b"TOC " in document_xml
    assert b"SEQ Table" in document_xml
    assert b"SEQ Figure" in document_xml
    assert any(b"PAGE" in part for part in page_number_parts)
    assert b"updateFields" in settings_xml

    loaded = Document(output)
    all_text = "\n".join(paragraph.text for paragraph in loaded.paragraphs)
    assert "Evidence-backed work" in all_text
    assert "Official documentation" in all_text


def test_qa_gate_and_reports_are_deterministic_and_escape_html(tmp_path: Path) -> None:
    manuscript = Manuscript(
        project_id="p1",
        title="QA",
        blocks=[
            HeadingBlock(text="INTRODUCTION", level=1),
            ParagraphBlock(text="TODO <script>alert(1)</script>"),
            FigureBlock(caption="Missing", artifact_id="missing-artifact"),
        ],
    )
    report = DeterministicQualityGate().run(
        QAGateContext(
            project_id="p1",
            run_id="run-1",
            manuscript=manuscript,
            profile=_qa_profile(),
        )
    )
    assert report.status == QAStatus.FAIL
    categories = {issue.category for issue in report.issues}
    assert {"placeholder", "missing_artifact"} <= categories

    artifacts = QAReportWriter().write(
        report,
        json_path=tmp_path / "qa.json",
        html_path=tmp_path / "qa.html",
    )
    payload = json.loads(artifacts.json_path.read_text(encoding="utf-8"))
    html_text = artifacts.html_path.read_text(encoding="utf-8")
    assert payload["status"] == "fail"
    assert "&lt;script&gt;" in html_text
    assert "<script>alert(1)</script>" not in html_text


def test_qa_accepts_rendered_docx_and_complete_provenance(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "figure.png"
    Image.new("RGB", (400, 240), "white").save(image_path)
    manuscript = _complete_manuscript("figure-1")
    docx_path = tmp_path / "complete.docx"
    DocxRenderer().render(manuscript, docx_path, artifact_paths={"figure-1": image_path})
    fact = FactRecord(
        project_id="p1",
        name="Revenue",
        value=125,
        origin=FactOrigin.VERIFIED_SOURCE,
        source_id="source-1",
    )
    dataset = Dataset(
        id="core-indicators",
        project_id="p1",
        name="Core indicators",
        columns=[
            DatasetColumn(name="indicator", data_type=DataType.STRING, nullable=False),
            DatasetColumn(name="value", data_type=DataType.INTEGER, nullable=False),
        ],
        rows=[
            {"indicator": "Revenue", "value": 125},
            {"indicator": "Cost", "value": 80},
        ],
        origin=FactOrigin.VERIFIED_SOURCE,
        source_ids=["source-1"],
        repository="zenodo",
        stable_id="10.5281/zenodo.12345",
        version="1.0",
        license="CC-BY-4.0",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        snapshot_sha256="a" * 64,
    )
    report = DeterministicQualityGate().run(
        QAGateContext(
            project_id="p1",
            run_id="run-2",
            manuscript=manuscript,
            profile=_qa_profile(),
            facts=[fact],
            datasets=[dataset],
            citations=[
                Citation(bibliography_entry_id=manuscript.bibliography[0].id)
            ],
            artifact_paths={"figure-1": image_path},
            docx_path=docx_path,
        )
    )
    assert report.status == QAStatus.PASS, [issue.message for issue in report.issues]
