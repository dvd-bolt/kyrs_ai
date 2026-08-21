from pathlib import Path

from papercraft.domain import Source, SourceRole
from papercraft.infrastructure.calculations import TabularDatasetImporter
from papercraft.infrastructure.persistence import sha256_file


def test_csv_becomes_typed_user_dataset(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("year;income\n2024;10.5\n2025;12.25\n", encoding="utf-8")
    source = Source(
        project_id="project",
        role=SourceRole.SOURCE_DATA,
        original_name=path.name,
        stored_path=str(path),
        sha256=sha256_file(path),
        mime_type="text/csv",
        size_bytes=path.stat().st_size,
    )
    dataset = TabularDatasetImporter().import_source("project", source)[0]
    assert dataset.origin.value == "user"
    assert [column.name for column in dataset.columns] == ["year", "income"]
    assert len(dataset.rows) == 2
