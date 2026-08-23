"""Twelve opt-in release runs: six real Gemini golden projects, twice each."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from papercraft.application import ProjectService, SourceService, prepare_autopilot
from papercraft.config import AppSettings
from papercraft.domain import (
    ArtifactKind,
    AutopilotOptions,
    DomainProfile,
    ProjectBrief,
    QASeverity,
    RunStatus,
    SourceRole,
    WorkType,
)
from papercraft.infrastructure.gemini import CredentialSecretStore
from papercraft.infrastructure.persistence import sha256_file
from papercraft.infrastructure.render import DocumentFinalizer
from papercraft.profiles import default_profile_registry

pytestmark = pytest.mark.live


@dataclass(frozen=True, slots=True)
class GoldenScenario:
    slug: str
    title: str
    topic: str
    prompt: str
    work_type: WorkType
    domain: DomainProfile
    inputs: tuple[tuple[str, SourceRole, str], ...]


_METHODOLOGY = """
Подготовить доказательную работу на русском языке по встроенному профилю PaperCraft.
Сохранить все обязательные разделы профиля, введение, заключение и список источников.
Каждое проверяемое утверждение связать с реальным источником; не выдумывать DOI, URL,
организации, эксперименты или числа. Числа использовать только из приложенных таблиц.
Добавить уместную проверяемую визуализацию. Соблюдать академический стиль.
""".strip()


SCENARIOS = (
    GoldenScenario(
        "it_coursework",
        "Воспроизводимая обработка научных документов",
        "Архитектура воспроизводимого конвейера обработки научных документов",
        "Разработать и оценить безопасную архитектуру локального конвейера обработки документов.",
        WorkType.COURSEWORK,
        DomainProfile.IT,
        (
            ("methodology.txt", SourceRole.METHODOLOGY, _METHODOLOGY),
            (
                "pipeline.py",
                SourceRole.CODEBASE,
                "def verify_digest(expected: str, actual: str) -> bool:\n    return expected == actual\n",
            ),
        ),
    ),
    GoldenScenario(
        "finance_coursework",
        "Анализ динамики выручки",
        "Финансовый анализ динамики выручки учебной организации",
        "Проанализировать приложенные показатели и дать проверяемые рекомендации.",
        WorkType.COURSEWORK,
        DomainProfile.FINANCE,
        (
            ("methodology.txt", SourceRole.METHODOLOGY, _METHODOLOGY),
            (
                "finance.csv",
                SourceRole.SOURCE_DATA,
                "year;revenue;cost\n2023;1200000;860000\n2024;1320000;910000\n2025;1450000;970000\n",
            ),
        ),
    ),
    GoldenScenario(
        "scientific_article",
        "Воспроизводимость цифровых исследований",
        "Методы обеспечения воспроизводимости цифровых исследований",
        "Подготовить обзорную научную статью без выдуманного эксперимента.",
        WorkType.SCIENTIFIC_ARTICLE,
        DomainProfile.SCIENCE,
        (("methodology.txt", SourceRole.METHODOLOGY, _METHODOLOGY),),
    ),
    GoldenScenario(
        "programming_practice_report",
        "Практика по контролю целостности данных",
        "Реализация проверки целостности артефактов в ходе практики",
        "Описать только фактически приложенный код и воспроизводимые проверки.",
        WorkType.PRACTICE_REPORT,
        DomainProfile.PROGRAMMING,
        (
            ("methodology.txt", SourceRole.METHODOLOGY, _METHODOLOGY),
            (
                "integrity.py",
                SourceRole.CODEBASE,
                "import hashlib\n\ndef digest(data: bytes) -> str:\n    return hashlib.sha256(data).hexdigest()\n",
            ),
            (
                "organisation.txt",
                SourceRole.SOURCE_DATA,
                "База практики: учебная лаборатория информационных систем. "
                "Выполнена локальная проверка целостности файлов.",
            ),
        ),
    ),
    GoldenScenario(
        "accounting_practice_report",
        "Практика по проверке бухгалтерского журнала",
        "Контроль корректности двойной записи в учебном бухгалтерском журнале",
        "Проверить приложенные проводки и описать результат без вымышленных операций.",
        WorkType.PRACTICE_REPORT,
        DomainProfile.ACCOUNTING,
        (
            ("methodology.txt", SourceRole.METHODOLOGY, _METHODOLOGY),
            (
                "journal.csv",
                SourceRole.SOURCE_DATA,
                "id;date;debit_account;credit_account;amount;description\n"
                "1;2025-02-03;10;60;125000;Поступление материалов\n"
                "2;2025-02-10;20;10;80000;Передача материалов\n"
                "3;2025-02-28;60;51;125000;Оплата поставщику\n",
            ),
        ),
    ),
    GoldenScenario(
        "school_project",
        "Школьный проект о цифровой гигиене",
        "Практические правила цифровой гигиены для школьников",
        "Объяснить тему для учащихся и проанализировать приложенный мини-опрос.",
        WorkType.SCHOOL_PROJECT,
        DomainProfile.SCHOOL,
        (
            ("methodology.txt", SourceRole.METHODOLOGY, _METHODOLOGY),
            (
                "survey.csv",
                SourceRole.SOURCE_DATA,
                "answer;students\nuses_unique_passwords;18\nuses_two_factor_auth;12\nchecks_links;21\n",
            ),
        ),
    ),
)


def _enabled() -> bool:
    return os.getenv("PAPERCRAFT_RUN_GOLDEN_TESTS") == "1"


@pytest.mark.parametrize("repeat", [1, 2])
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.slug)
def test_live_golden_pipeline_twice(scenario: GoldenScenario, repeat: int) -> None:
    if not _enabled():
        pytest.skip("set PAPERCRAFT_RUN_GOLDEN_TESTS=1 for twelve release golden runs")
    if not CredentialSecretStore().get_api_key():
        pytest.skip("Gemini is not configured in Credential Manager or GEMINI_API_KEY")
    finalizer = DocumentFinalizer()
    if not finalizer.word_available() and not finalizer.libreoffice_available():
        pytest.skip("a real Word or LibreOffice finalizer is required")

    output_root = Path(
        os.getenv(
            "PAPERCRAFT_GOLDEN_OUTPUT_DIR",
            str(Path("build") / "stage3" / "live-golden"),
        )
    ).resolve()
    run_root = output_root / scenario.slug / f"run-{repeat}"
    run_root.mkdir(parents=True, exist_ok=True)
    settings = AppSettings.from_environment().model_copy(
        update={"projects_root": run_root / "projects", "minimum_free_space_mb": 128}
    )
    workspace = ProjectService(settings).create(
        ProjectBrief(
            title=scenario.title,
            topic=scenario.topic,
            prompt=scenario.prompt,
            work_type=scenario.work_type,
            domain_profile=scenario.domain,
        ),
        AutopilotOptions(
            consent_to_remote_processing=True,
            generate_pdf=True,
            generate_qa_report=True,
            maximum_revision_cycles=3,
            preferred_finalizer="libreoffice",
        ),
    )
    source_root = run_root / "inputs"
    source_root.mkdir(parents=True, exist_ok=True)
    for filename, role, content in scenario.inputs:
        path = source_root / filename
        path.write_text(content, encoding="utf-8")
        imported = SourceService(workspace).import_files([path], role)
        assert len(imported.sources) == 1, imported.rejected

    runtime = prepare_autopilot(settings, workspace)
    run = runtime.service.execute(runtime.run.id)
    stages = workspace.repository.list_stages(run.id)
    record: dict[str, object] = {
        "scenario": scenario.slug,
        "repeat": repeat,
        "project_id": workspace.project.id,
        "run_id": run.id,
        "status": run.status.value,
        "cost_usd": float(run.cost),
        "stages": {item.name: item.status.value for item in stages},
        "failure_code": next(
            (item.failure_code for item in stages if item.failure_code),
            None,
        ),
    }
    (run_root / "acceptance.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    assert run.status == RunStatus.SUCCEEDED, record
    assert run.cost > 0
    assert run.metadata.get("remote_files", []) == []

    requirements = workspace.repository.get_latest_requirement_set(workspace.project.id)
    blueprint = workspace.repository.get_latest_blueprint(workspace.project.id)
    manuscript = workspace.repository.get_latest_manuscript(workspace.project.id)
    report = workspace.repository.get_latest_qa_report(run.id)
    assert requirements is not None and blueprint is not None and manuscript is not None
    assert report is not None
    assert not {
        item.severity
        for item in report.issues
        if not item.resolved
    } & {QASeverity.CRITICAL, QASeverity.BLOCKER}

    profile = default_profile_registry().resolve(scenario.work_type, scenario.domain)
    requirement_keys = {item.key for item in requirements.rules}
    assert {
        f"profile.structure.{section.key}" for section in profile.sections
    } <= requirement_keys
    planned_titles = {item.title.casefold() for item in blueprint.outline.sections}
    required_prose_titles = {
        item.title.casefold()
        for item in profile.sections
        if item.key != "bibliography"
    }
    assert required_prose_titles <= planned_titles
    assert manuscript.bibliography

    snapshots = workspace.repository.list_source_snapshots(workspace.project.id)
    evidence = workspace.repository.list_evidence(workspace.project.id)
    citations = workspace.repository.list_citations(workspace.project.id)
    bibliography_ids = {item.id for item in manuscript.bibliography}
    evidence_by_id = {item.id: item for item in evidence}
    assert snapshots and evidence and citations
    for snapshot in snapshots:
        data = Path(snapshot.stored_path).read_bytes()
        assert len(data) == snapshot.size_bytes
        assert hashlib.sha256(data).hexdigest() == snapshot.sha256
    assert all(item.verified and item.snapshot_id for item in evidence)
    assert all(
        item.evidence_id in evidence_by_id
        and item.bibliography_entry_id in bibliography_ids
        for item in citations
    )

    artifacts = workspace.repository.list_artifacts(workspace.project.id, run_id=run.id)
    kinds = {item.kind for item in artifacts}
    assert {ArtifactKind.DOCX, ArtifactKind.PDF, ArtifactKind.QA_JSON, ArtifactKind.QA_HTML} <= kinds
    for artifact in artifacts:
        path = Path(artifact.path)
        assert path.is_file()
        assert path.stat().st_size == artifact.size_bytes
        assert sha256_file(path) == artifact.sha256

    signature = [item.title.casefold() for item in blueprint.outline.sections]
    signature_path = output_root / scenario.slug / f"signature-{repeat}.json"
    signature_path.write_text(
        json.dumps(signature, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if repeat == 2:
        first_signature = json.loads(
            (output_root / scenario.slug / "signature-1.json").read_text(encoding="utf-8")
        )
        assert first_signature == signature
