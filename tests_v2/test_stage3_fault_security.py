from __future__ import annotations

import hashlib
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from papercraft.application import (
    AutopilotService,
    DocumentService,
    PipelineStage,
    ProductionStageFactory,
    ProjectService,
    StageOutcome,
)
from papercraft.config import AppSettings
from papercraft.domain import (
    Artifact,
    ArtifactKind,
    AutopilotOptions,
    DiagramSpec,
    GenerationRun,
    ProjectBrief,
    RemoteResource,
    RunStatus,
    StageStatus,
)
from papercraft.infrastructure.gemini import FakeGeminiGateway
from papercraft.infrastructure.ingest import ImportPolicy, SafeSourceImporter
from papercraft.infrastructure.persistence import AtomicArtifactStore
from papercraft.infrastructure.render import (
    DocumentFinalizer,
    FinalizationError,
    FinalizationUnavailableError,
)
from papercraft.infrastructure.visuals import DiagramRenderError, LocalDiagramRenderer


def _service(
    tmp_path: Path,
    handler,
    *,
    options: AutopilotOptions | None = None,
    terminal_hook=None,
) -> tuple[AutopilotService, object]:
    settings = AppSettings(projects_root=tmp_path / "projects")
    workspace = ProjectService(settings).create(
        ProjectBrief(topic="Stage 3 hardening"),
        options,
    )
    service = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        {stage: handler for stage in PipelineStage},
        terminal_hook=terminal_hook,
    )
    return service, workspace


def test_checkpoint_acknowledgement_continues_without_replaying_stage(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(context):
        calls.append(context.stage.name)
        return StageOutcome()

    service, _workspace = _service(
        tmp_path,
        handler,
        options=AutopilotOptions(checkpoint_requirements=True),
    )
    waiting = service.start()
    assert waiting.status == RunStatus.WAITING_INPUT
    assert calls[-1] == PipelineStage.EXTRACT_REQUIREMENTS.value

    completed = service.acknowledge_checkpoint(
        waiting.id, PipelineStage.EXTRACT_REQUIREMENTS
    )
    assert completed.status == RunStatus.SUCCEEDED
    assert calls.count(PipelineStage.EXTRACT_REQUIREMENTS.value) == 1


def test_pause_is_not_converted_to_stage_failure_and_resume_continues(tmp_path: Path) -> None:
    paused_once = False

    def handler(context):
        nonlocal paused_once
        if context.stage.name == PipelineStage.PREFLIGHT.value and not paused_once:
            paused_once = True
            run = context.repository.get_run(context.run.id)
            assert run is not None
            run.status = RunStatus.PAUSED
            context.repository.save_run(run)
            context.cancellation.checkpoint()
        return StageOutcome()

    service, workspace = _service(tmp_path, handler)
    paused = service.start()
    assert paused.status == RunStatus.PAUSED
    first = workspace.repository.list_stages(paused.id)[0]
    assert first.status == StageStatus.QUEUED
    assert first.error is None

    completed = service.resume(paused.id)
    assert completed.status == RunStatus.SUCCEEDED


def test_terminal_cleanup_failure_blocks_success_and_is_retried(tmp_path: Path) -> None:
    cleanup_calls = 0

    def handler(_context):
        return StageOutcome()

    def cleanup(_run):
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 1:
            raise TimeoutError("provider cleanup timeout")

    service, workspace = _service(tmp_path, handler, terminal_hook=cleanup)
    failed = service.start()
    assert failed.status == RunStatus.FAILED
    assert failed.metadata["terminal_cleanup_pending"] is True
    events = [event for _, event in workspace.repository.list_events(failed.id)]
    cleanup_event = next(event for event in events if event.event_type == "terminal_cleanup_failed")
    assert cleanup_event.message == "TimeoutError"
    assert "provider cleanup timeout" not in cleanup_event.message

    completed = service.resume(failed.id)
    assert completed.status == RunStatus.SUCCEEDED
    assert cleanup_calls == 2
    assert completed.metadata["terminal_hook_done"] is True
    assert "terminal_cleanup_pending" not in completed.metadata


def test_remote_cleanup_reconciles_sqlite_when_run_metadata_is_lost(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects")
    workspace = ProjectService(settings).create(ProjectBrief(topic="Remote cleanup"))
    run = GenerationRun(project_id=workspace.project.id, metadata={"remote_files": []})
    workspace.repository.save_run(run)
    resource = RemoteResource(
        project_id=workspace.project.id,
        run_id=run.id,
        provider="gemini",
        remote_id="files/recover-me",
        uri="fake://recover-me",
    )
    workspace.repository.save_remote_resource(resource)
    gateway = FakeGeminiGateway()

    ProductionStageFactory(
        gateway,
        repository=workspace.repository,
    ).cleanup_remote_files(run)

    assert gateway.deleted_files == ["files/recover-me"]
    stored = workspace.repository.list_remote_resources(run.id)
    assert len(stored) == 1
    assert stored[0].deleted_at is not None
    assert run.metadata["remote_files"] == []


def test_corrupt_completed_artifact_is_rebuilt_with_downstream_stages(tmp_path: Path) -> None:
    generation = 0
    artifact_path: Path | None = None

    def handler(context):
        nonlocal artifact_path, generation
        if context.stage.name != PipelineStage.PREFLIGHT.value:
            return StageOutcome()
        generation += 1
        artifact_path = context.paths.artifacts / "preflight" / "probe.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        content = f'{{"generation":{generation}}}\n'.encode()
        artifact_path.write_bytes(content)
        return StageOutcome(
            artifacts=[
                Artifact(
                    project_id=context.project.id,
                    run_id=context.run.id,
                    stage_id=context.stage.id,
                    kind=ArtifactKind.QA_JSON,
                    path=str(artifact_path),
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                    mime_type="application/json",
                )
            ]
        )

    service, workspace = _service(tmp_path, handler)
    first = service.start()
    assert first.status == RunStatus.SUCCEEDED
    assert artifact_path is not None
    artifact_path.write_text("corrupt", encoding="utf-8")

    recovered = service.execute(first.id)
    assert recovered.status == RunStatus.SUCCEEDED
    assert generation == 2
    assert '"generation":2' in artifact_path.read_text(encoding="utf-8")
    events = [event.event_type for _, event in workspace.repository.list_events(first.id)]
    assert "artifact_corruption_recovered" in events


def test_stale_worker_lease_is_recovered(tmp_path: Path) -> None:
    def handler(_context):
        return StageOutcome()

    service, workspace = _service(tmp_path, handler)
    run = service.create_run()
    run.status = RunStatus.RETRYING
    workspace.repository.save_run(run)
    stage = workspace.repository.list_stages(run.id)[0]
    stage.status = StageStatus.RUNNING
    stage.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
    workspace.repository.save_stage(stage)

    completed = service.execute(run.id)
    assert completed.status == RunStatus.SUCCEEDED
    events = [event.event_type for _, event in workspace.repository.list_events(run.id)]
    assert "stale_lease_recovered" in events


def test_document_service_rejects_tampered_artifact(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects")
    workspace = ProjectService(settings).create(ProjectBrief(topic="Integrity"))
    path = workspace.paths.artifacts / "output.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"valid fixture")
    artifact = Artifact(
        project_id=workspace.project.id,
        kind=ArtifactKind.DOCX,
        path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size_bytes=path.stat().st_size,
    )
    workspace.repository.save_artifact(artifact)
    documents = DocumentService(workspace.project.id, workspace.repository)
    assert documents.latest(ArtifactKind.DOCX) == path

    path.write_bytes(b"tampered")
    with pytest.raises(OSError, match="integrity"):
        documents.latest(ArtifactKind.DOCX)


def test_rejected_zip_members_consume_aggregate_limits(tmp_path: Path) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    with zipfile.ZipFile(first, "w") as package:
        package.writestr(".env", "TOKEN=not-imported")
    with zipfile.ZipFile(second, "w") as package:
        package.writestr("one.txt", "one")
        package.writestr("two.txt", "two")

    importer = SafeSourceImporter(
        "project",
        tmp_path / "originals",
        policy=ImportPolicy(max_files=2),
    )
    assert importer.import_path(first).rejected[0].reason == "excluded-path"
    result = importer.import_path(second)
    assert result.rejected[0].reason == "zip-too-many-members"


def test_secret_beyond_old_sample_boundary_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "late-secret.txt"
    fake_google_key = "AI" + "zaSyA" + ("1234567890" * 3) + "123"
    source.write_text(
        "x" * (256 * 1024) + f"\nGEMINI_API_KEY={fake_google_key}\n",
        encoding="utf-8",
    )
    result = SafeSourceImporter("project", tmp_path / "originals").import_path(source)
    assert not result.sources
    assert result.rejected[0].reason.startswith("secret-detected")


@pytest.mark.parametrize(
    "payload",
    [
        "flowchart TD\nA-->B\nstyle A fill:url(https://attacker.invalid/x)",
        "flowchart TD\nA-->B\n@import 'https://attacker.invalid/a.css'",
        "flowchart TD\nA[data:text/html,unsafe]",
        "%%{config: {'themeCSS': 'unsafe'}}%%\nflowchart TD\nA-->B",
    ],
)
def test_diagram_renderer_blocks_network_and_active_directives(
    tmp_path: Path, payload: str
) -> None:
    with pytest.raises(DiagramRenderError, match="unsafe directive"):
        LocalDiagramRenderer(mermaid_cli="unused").render(
            DiagramSpec(source=payload),
            tmp_path / "diagram.png",
        )


def test_atomic_artifact_write_cleans_partial_on_disk_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AtomicArtifactStore(tmp_path / "artifacts")

    def fail_replace(_source, _target):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated disk failure"):
        store.write_json("qa/result.json", {"ok": True})
    assert not (tmp_path / "artifacts" / "qa" / "result.json").exists()
    assert not list((tmp_path / "artifacts" / "qa").glob("*.tmp"))


def test_word_failure_falls_back_to_libreoffice_without_silent_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docx = tmp_path / "document.docx"
    docx.write_bytes(b"PK\x03\x04fixture")
    pdf = tmp_path / "document.pdf"
    finalizer = DocumentFinalizer()

    def word_fails(_docx: Path, _pdf: Path | None) -> None:
        raise FinalizationError("simulated Word COM failure")

    def libreoffice_succeeds(_docx: Path, output: Path | None) -> bool:
        assert output is not None
        output.write_bytes(b"%PDF-1.7\nfixture")
        return True

    monkeypatch.setattr(finalizer, "_finalize_with_word", word_fails)
    monkeypatch.setattr(finalizer, "_convert_with_libreoffice", libreoffice_succeeds)
    result = finalizer.finalize(docx, pdf_path=pdf, preferred="word")
    assert result.engine == "libreoffice"
    assert result.fields_updated is True
    assert "simulated Word COM failure" in result.warnings[0]


def test_default_beta_finalizer_does_not_fall_back_to_word_on_libreoffice_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docx = tmp_path / "document.docx"
    docx.write_bytes(b"PK\x03\x04fixture")
    finalizer = DocumentFinalizer()

    attempts: list[str] = []

    def word_must_not_run(*_args, **_kwargs):
        attempts.append("word")
        raise AssertionError("the LibreOffice beta path must not invoke Word")

    def fail(*_args, **_kwargs):
        raise FinalizationError("simulated finalizer failure")

    monkeypatch.setattr(finalizer, "_finalize_with_word", word_must_not_run)
    monkeypatch.setattr(finalizer, "_convert_with_libreoffice", fail)
    with pytest.raises(FinalizationUnavailableError, match=r"LibreOffice"):
        finalizer.finalize(docx, pdf_path=tmp_path / "document.pdf")
    assert attempts == []
