from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from papercraft.domain import (
    BibliographyEntry,
    ClaimStatus,
    Locator,
    Source,
    SourceRole,
)
from papercraft.infrastructure.ingest import (
    CodeParser,
    CsvParser,
    DocxParser,
    ImageParser,
    PdfParser,
    SafeSourceImporter,
    SecretScanner,
    SourceClassifier,
    TextParser,
    XlsxParser,
)
from papercraft.infrastructure.research import (
    BibliographyDeduplicator,
    BibliographyValidator,
    EvidenceGraph,
    HTTPResponse,
    UnsafeURLError,
    URLVerifier,
    canonical_url,
    normalize_doi,
    valid_isbn,
)


def _source(path: Path, role: SourceRole = SourceRole.SOURCE_DATA) -> Source:
    data = path.read_bytes()
    return Source(
        project_id="project-1",
        role=role,
        original_name=path.name,
        stored_path=str(path),
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type="text/plain",
        size_bytes=len(data),
    )


def test_classification_uses_name_and_extension(tmp_path: Path) -> None:
    classifier = SourceClassifier()
    assert classifier.classify(tmp_path / "методичка.pdf").role == SourceRole.METHODOLOGY
    assert classifier.classify(tmp_path / "title_template.docx").role == SourceRole.TEMPLATE
    assert classifier.classify(tmp_path / "service.py").role == SourceRole.CODEBASE
    assert classifier.classify(tmp_path / "diagram.png").role == SourceRole.IMAGE
    assert classifier.classify(tmp_path / "data.csv").role == SourceRole.SOURCE_DATA


def test_secret_scanner_redacts_secret() -> None:
    scanner = SecretScanner()
    secret = "AIzaSyA123456789012345678901234567890123"
    findings = scanner.scan_text(f"GEMINI_API_KEY={secret}\n")
    assert findings
    assert findings[0].kind == "google_api_key"
    assert secret not in findings[0].redacted
    assert findings[0].line == 1


def test_safe_directory_import_excludes_vcs_env_secrets_and_binaries(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    (incoming / ".git").mkdir(parents=True)
    (incoming / "node_modules").mkdir()
    (incoming / "src").mkdir()
    (incoming / ".git" / "config").write_text("secret", encoding="utf-8")
    (incoming / "node_modules" / "dependency.js").write_text("ignored", encoding="utf-8")
    (incoming / ".env").write_text("TOKEN=abcdefghijklmnopqrstuvwxyz123456", encoding="utf-8")
    (incoming / "src" / "main.py").write_text("print('safe')\n", encoding="utf-8")
    (incoming / "src" / "secret.py").write_text(
        "api_key='abcdefghijklmnopqrstuvwxyz1234567890'", encoding="utf-8"
    )
    (incoming / "src" / "blob.bin").write_bytes(b"\x00\x01\x02")

    expected_hash = hashlib.sha256((incoming / "src" / "main.py").read_bytes()).hexdigest()
    result = SafeSourceImporter("project-1", tmp_path / "originals").import_path(incoming)

    assert [source.original_name.replace("\\", "/") for source in result.sources] == [
        "incoming/src/main.py"
    ]
    imported = result.sources[0]
    assert Path(imported.stored_path).read_text(encoding="utf-8") == "print('safe')\n"
    assert imported.sha256 == expected_hash
    reasons = {rejection.reason for rejection in result.rejected}
    assert any(reason.startswith("secret-detected") for reason in reasons)
    assert "binary-file" in reasons


def test_zip_import_blocks_zip_slip_and_keeps_safe_member(tmp_path: Path) -> None:
    archive = tmp_path / "project.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../outside.txt", "escape")
        package.writestr("src/app.py", "print('ok')")
        package.writestr(".env", "TOKEN=abcdefghijklmnopqrstuvwxyz")

    destination = tmp_path / "originals"
    result = SafeSourceImporter("project-1", destination).import_path(archive)

    assert len(result.sources) == 1
    assert result.sources[0].original_name == "src/app.py"
    assert not (tmp_path / "outside.txt").exists()
    assert any("escapes" in rejection.reason for rejection in result.rejected)
    assert any(rejection.reason == "excluded-path" for rejection in result.rejected)


def test_repeated_import_reuses_identical_copy_without_overwrite(tmp_path: Path) -> None:
    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    first_directory.mkdir()
    second_directory.mkdir()
    first = first_directory / "notes.txt"
    second = second_directory / "notes.txt"
    first.write_text("original", encoding="utf-8")
    second.write_text("different", encoding="utf-8")
    importer = SafeSourceImporter("project-1", tmp_path / "originals")

    first_result = importer.import_path(first)
    repeated_result = importer.import_path(first)
    collision_result = importer.import_path(second)

    first_path = Path(first_result.sources[0].stored_path)
    assert Path(repeated_result.sources[0].stored_path) == first_path
    assert first_path.read_text(encoding="utf-8") == "original"
    collision_path = Path(collision_result.sources[0].stored_path)
    assert collision_path != first_path
    assert collision_path.read_text(encoding="utf-8") == "different"


def test_text_csv_and_code_parsers_keep_precise_locators(tmp_path: Path) -> None:
    markdown = tmp_path / "notes.md"
    markdown.write_text("# Intro\nFirst\nSecond\n", encoding="utf-8")
    text_result = TextParser(lines_per_fragment=2).parse(_source(markdown))
    assert len(text_result.fragments) == 2
    assert text_result.fragments[0].locator.line_start == 1
    assert text_result.fragments[0].locator.line_end == 2
    assert text_result.fragments[0].locator.section == "Intro"
    assert text_result.fragments[0].locator.details["path"].endswith("notes.md")

    csv_path = tmp_path / "data.csv"
    csv_path.write_text("year;value\n2025;10\n2026;12\n", encoding="utf-8")
    csv_result = CsvParser(rows_per_fragment=2).parse(_source(csv_path))
    assert csv_result.fragments[0].locator.cell_range == "A1:B2"
    assert csv_result.fragments[-1].locator.details["row"] == 3

    code_path = tmp_path / "service.py"
    code_path.write_text("class Service:\n    def run(self):\n        return 1\n", encoding="utf-8")
    code_result = CodeParser().parse(_source(code_path, SourceRole.CODEBASE))
    symbol_names = {item["name"] for item in code_result.metadata["symbols"]}
    assert symbol_names == {"Service", "run"}


def test_docx_xlsx_pdf_and_image_parsers(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    openpyxl = pytest.importorskip("openpyxl")
    image_module = pytest.importorskip("PIL.Image")
    pypdf = pytest.importorskip("pypdf")

    docx_path = tmp_path / "method.docx"
    document = docx.Document()
    document.add_heading("Requirements", level=1)
    document.add_paragraph("Use Times New Roman.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Rule"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Margin"
    table.cell(1, 1).text = "3 cm"
    document.save(docx_path)
    docx_result = DocxParser().parse(_source(docx_path, SourceRole.METHODOLOGY))
    assert any(item.locator.section == "Requirements" for item in docx_result.fragments)
    assert any(item.metadata["kind"] == "table" for item in docx_result.fragments)

    xlsx_path = tmp_path / "metrics.xlsx"
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Metrics"
    worksheet.append(["Year", "Revenue"])
    worksheet.append([2025, 100])
    workbook.save(xlsx_path)
    workbook.close()
    xlsx_result = XlsxParser(rows_per_fragment=2).parse(_source(xlsx_path))
    assert xlsx_result.fragments[0].locator.sheet == "Metrics"
    assert xlsx_result.fragments[0].locator.cell_range == "A1:B2"

    image_path = tmp_path / "diagram.png"
    image_module.new("RGB", (40, 20), color="white").save(image_path)
    image_result = ImageParser().parse(_source(image_path, SourceRole.IMAGE))
    assert image_result.metadata["width"] == 40
    assert image_result.fragments[0].locator.details["path"].endswith("diagram.png")
    assert image_result.metadata["vision_required"] is True

    pdf_path = tmp_path / "scan.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with pdf_path.open("wb") as stream:
        writer.write(stream)
    pdf_result = PdfParser().parse(_source(pdf_path))
    assert pdf_result.metadata["page_count"] == 1
    assert "ocr-required:page-1" in pdf_result.warnings


class _FakeTransport:
    def __init__(self, responses: dict[str, HTTPResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def request(self, url, *, approved_ips, timeout, max_bytes, headers):
        self.calls.append((url, tuple(approved_ips)))
        return self.responses[url]


def _public_resolver(host: str, port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/",
        "http://user:password@example.com/",
        "file:///etc/passwd",
        "http://example.com:8080/",
    ],
)
def test_url_verifier_rejects_ssrf_targets(url: str) -> None:
    verifier = URLVerifier(resolver=_public_resolver)
    with pytest.raises(UnsafeURLError):
        verifier.check_safety(url)


def test_url_verifier_checks_redirect_target_and_pins_peer() -> None:
    start = "https://example.com/"
    final = "https://example.org/paper"
    transport = _FakeTransport(
        {
            start: HTTPResponse(302, {"location": final}, peer_ip="93.184.216.34"),
            final: HTTPResponse(
                200,
                {"content-type": "text/html; charset=utf-8"},
                b"<html><title>Verified paper</title></html>",
                "93.184.216.34",
            ),
        }
    )
    result = URLVerifier(resolver=_public_resolver, transport=transport).verify(start)
    assert result.verified
    assert result.final_url == final
    assert result.title == "Verified paper"
    assert result.redirects == (final,)
    assert all(addresses == ("93.184.216.34",) for _, addresses in transport.calls)


def test_url_verifier_rejects_redirect_to_private_network() -> None:
    start = "https://example.com/"
    transport = _FakeTransport(
        {
            start: HTTPResponse(
                302,
                {"location": "http://127.0.0.1/admin"},
                peer_ip="93.184.216.34",
            )
        }
    )
    with pytest.raises(UnsafeURLError):
        URLVerifier(resolver=_public_resolver, transport=transport).verify(start)
    assert len(transport.calls) == 1


def test_url_verifier_rejects_mixed_dns_and_unpinned_peer() -> None:
    mixed = URLVerifier(resolver=lambda host, port: ("93.184.216.34", "10.0.0.2"))
    with pytest.raises(UnsafeURLError):
        mixed.check_safety("https://example.com/")

    transport = _FakeTransport(
        {
            "https://example.com/": HTTPResponse(
                200,
                {"content-type": "text/plain"},
                b"content",
                peer_ip="8.8.8.8",
            )
        }
    )
    with pytest.raises(UnsafeURLError):
        URLVerifier(resolver=_public_resolver, transport=transport).verify(
            "https://example.com/"
        )


def test_bibliography_normalizes_validates_and_deduplicates() -> None:
    first = BibliographyEntry(
        id="first",
        title="A Reliable Research Method",
        authors=["Ada Lovelace"],
        year=2024,
        doi="https://doi.org/10.1234/ABC.42",
        url="https://example.org/paper?utm_source=test",
    )
    duplicate = BibliographyEntry(
        id="duplicate",
        title="A reliable research method.",
        authors=["Ada Lovelace", "Grace Hopper"],
        year=2024,
        doi="doi:10.1234/abc.42",
        publisher="University Press",
    )
    validation = BibliographyValidator().validate(first)
    assert validation.valid
    assert validation.entry.doi == "10.1234/abc.42"
    assert canonical_url(first.url) == "https://example.org/paper"
    assert normalize_doi(duplicate.doi) == validation.entry.doi
    assert valid_isbn("978-0-306-40615-7")
    result = BibliographyDeduplicator().deduplicate([first, duplicate])
    assert len(result.entries) == 1
    assert result.merged_ids == {"duplicate": "first"}
    assert result.entries[0].publisher == "University Press"
    assert set(result.entries[0].authors) == {"Ada Lovelace", "Grace Hopper"}


def test_bibliography_rejects_invalid_identifiers() -> None:
    invalid = BibliographyEntry(
        title="Unverified item",
        year=2025,
        doi="not-a-doi",
        isbn="978-0-306-40615-8",
        url="https://user:password@example.org/private",
    )
    validation = BibliographyValidator().validate(invalid)
    assert not validation.valid
    assert set(validation.errors) >= {"invalid-doi", "invalid-isbn", "invalid-url"}


def test_claim_evidence_graph_updates_status_and_coverage() -> None:
    graph = EvidenceGraph("project-1")
    claim = graph.create_claim("Revenue increased by 12 percent", section_id="results")
    evidence = graph.add_evidence(
        claim.id,
        "source-1",
        Locator(source_id="source-1", page=4),
        excerpt="Revenue increased by 12%.",
        verified=False,
    )
    assert graph.claims[claim.id].status == ClaimStatus.PENDING
    graph.set_verified(evidence.id)
    assert graph.claims[claim.id].status == ClaimStatus.SUPPORTED
    assert graph.coverage().ratio == 1.0

    graph.add_evidence(
        claim.id,
        "source-2",
        Locator(source_id="source-2", page=8),
        excerpt="The reported change was 8%.",
        supports=False,
        verified=True,
    )
    assert graph.claims[claim.id].status == ClaimStatus.DISPUTED
    assert graph.coverage().disputed == 1
    assert graph.unresolved_claims()[0].id == claim.id
