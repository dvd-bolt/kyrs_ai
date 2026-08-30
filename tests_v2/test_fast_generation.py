from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import Lock
from time import sleep
from types import SimpleNamespace

import pytest

from papercraft.application import (
    AutopilotService,
    PipelineStage,
    ProductionStageFactory,
    ProjectService,
    ProjectWorkspace,
    SourceService,
    StageContext,
    StageExecutionError,
    StageOutcome,
)
from papercraft.application.stages import _longest_provider_error, _ResearchClaimResult
from papercraft.application.worker_control import CancellationToken, RunCancelled
from papercraft.config import AppSettings
from papercraft.domain import (
    Artifact,
    ArtifactKind,
    AutopilotOptions,
    BibliographyEntry,
    Citation,
    Claim,
    ClaimStatus,
    Evidence,
    FigureBlock,
    GenerationRun,
    ImageSpec,
    Locator,
    Manuscript,
    Outline,
    ParagraphBlock,
    ProjectBlueprint,
    ProjectBrief,
    RequirementSet,
    RunStatus,
    SectionSpec,
    Source,
    SourceRole,
    StageRun,
)
from papercraft.infrastructure.gemini import (
    FakeGeminiGateway,
    GeminiUnavailableError,
    GroundedResult,
)
from papercraft.infrastructure.persistence import AtomicArtifactStore


def _context(project_workspace: ProjectWorkspace, settings: AppSettings) -> StageContext:
    run = GenerationRun(project_id=project_workspace.project.id, status=RunStatus.RUNNING)
    project_workspace.repository.save_run(run)
    stage = StageRun(
        run_id=run.id,
        name=PipelineStage.GENERATE_SECTIONS.value,
        order=7,
        status="running",
    )
    project_workspace.repository.save_stage(stage)
    return StageContext(
        settings=settings,
        project=project_workspace.project,
        run=run,
        stage=stage,
        paths=project_workspace.paths,
        repository=project_workspace.repository,
        artifact_store=AtomicArtifactStore(project_workspace.paths.artifacts),
        cancellation=CancellationToken(project_workspace.repository, run.id, stage.id),
    )


def test_sections_run_in_parallel_respect_dependencies_and_reuse_cache(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    settings.performance_policy.parallel_generation_enabled = True
    workspace = ProjectService(settings).create(ProjectBrief(topic="Parallel work"))
    sections = [
        SectionSpec(id="intro", title="Introduction", order=0, target_words=100),
        SectionSpec(id="method", title="Method", order=1, target_words=100),
        SectionSpec(id="results", title="Results", order=2, target_words=100),
        SectionSpec(
            id="conclusion",
            title="Conclusion",
            order=3,
            target_words=100,
            depends_on=["intro"],
        ),
    ]
    workspace.repository.save_blueprint(
        ProjectBlueprint(project_id=workspace.project.id, topic="Parallel work", goal="Test", tasks=["Run"], outline=Outline(sections=sections))
    )
    workspace.repository.save_requirement_set(RequirementSet(project_id=workspace.project.id))
    dependency_conclusions: dict[str, dict[str, str]] = {}
    active_writers = 0
    max_active_writers = 0
    lock = Lock()

    fake = FakeGeminiGateway()

    for section in sections:
        source = Source(
            project_id=workspace.project.id,
            role=SourceRole.REFERENCE,
            original_name=f"{section.id}.html",
            stored_path="",
            sha256="a" * 64,
            size_bytes=0,
        )
        workspace.repository.save_source(source)
        bibliography = BibliographyEntry(title=section.title, source_id=source.id)
        workspace.repository.save_bibliography_entry(workspace.project.id, bibliography)
        claim = Claim(
            project_id=workspace.project.id,
            text=f"Claim for {section.id}",
            section_id=section.id,
            status=ClaimStatus.SUPPORTED,
        )
        evidence = Evidence(
            claim_id=claim.id,
            source_id=source.id,
            locator=Locator(source_id=source.id),
            verified=True,
        )
        claim.evidence_ids = [evidence.id]
        workspace.repository.save_claim(claim)
        workspace.repository.save_evidence(workspace.project.id, evidence)

    def response(*, schema: type[object], prompt: str, **_kwargs: object) -> dict[str, object]:
        nonlocal active_writers, max_active_writers
        if getattr(schema, "__name__", "") == "SectionCritique":
            return {"accepted": True}
        payload = json.loads(prompt.rsplit("\n", 1)[-1])
        section = payload["section"]
        with lock:
            active_writers += 1
            max_active_writers = max(max_active_writers, active_writers)
        try:
            sleep(0.04)
        finally:
            with lock:
                active_writers -= 1
        dependency_conclusions[section["id"]] = payload["dependency_conclusions"]
        return {
            "section_id": section["id"],
            "blocks": [
                {
                    "type": "paragraph",
                    "text": "Evidence supports this academic section.",
                    "claim_ids": [payload["claims"][0]["id"]],
                    "bibliography_entry_ids": [payload["bibliography"][0]["id"]],
                }
            ],
            "conclusion": f"Conclusion for {section['id']}",
            "word_count": 100,
        }

    for _ in range(12):
        fake.enqueue("generate_structured", response)
    context = _context(workspace, settings)
    outcome = ProductionStageFactory(fake).generate_sections(context)

    assert len(outcome.artifacts) == 4
    assert max_active_writers == 3
    assert dependency_conclusions["conclusion"] == {"intro": "Conclusion for intro"}
    manuscript = workspace.repository.get_latest_manuscript(workspace.project.id)
    assert manuscript is not None
    headings = [block.section_id for block in manuscript.blocks if getattr(block, "type", "") == "heading"]
    assert headings == ["intro", "method", "results", "conclusion"]

    cached_context = _context(workspace, settings)
    cached_gateway = FakeGeminiGateway()
    cached_outcome = ProductionStageFactory(cached_gateway).generate_sections(cached_context)

    assert cached_outcome.artifacts == []
    assert cached_gateway.calls == []
    assert cached_context.stage.checkpoint["cache_hits"] == 4


def test_citation_audit_failure_preserves_previous_citations_and_manuscript(tmp_path: Path) -> None:
    """Validation happens before the derived citation graph is published."""

    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Atomic citations"))
    source = Source(
        project_id=workspace.project.id,
        role=SourceRole.REFERENCE,
        original_name="source.html",
        stored_path="",
        sha256="a" * 64,
        size_bytes=0,
    )
    workspace.repository.save_source(source)
    bibliography = BibliographyEntry(title="Source", source_id=source.id)
    workspace.repository.save_bibliography_entry(workspace.project.id, bibliography)
    supported = Claim(
        project_id=workspace.project.id,
        text="A supported claim",
        status=ClaimStatus.SUPPORTED,
    )
    evidence = Evidence(
        claim_id=supported.id,
        source_id=source.id,
        locator=Locator(source_id=source.id),
        verified=True,
        supports=True,
        metadata={"bibliography_entry_id": bibliography.id},
    )
    supported.evidence_ids = [evidence.id]
    unsupported = Claim(
        project_id=workspace.project.id,
        text="An unsupported claim",
        status=ClaimStatus.PENDING,
    )
    workspace.repository.save_claim(supported)
    workspace.repository.save_claim(unsupported)
    workspace.repository.save_evidence(workspace.project.id, evidence)
    previous = Citation(
        claim_id=supported.id,
        evidence_id=evidence.id,
        bibliography_entry_id=bibliography.id,
        marker="[1]",
    )
    workspace.repository.save_citation(workspace.project.id, previous)
    manuscript = Manuscript(
        project_id=workspace.project.id,
        title="Citation rollback",
        bibliography=[bibliography],
        blocks=[
            ParagraphBlock(
                id="supported-paragraph",
                text="A supported assertion.",
                citation_ids=[previous.id],
                metadata={
                    "claim_ids": [supported.id],
                    "bibliography_entry_ids": [bibliography.id],
                },
            ),
            ParagraphBlock(
                id="unsupported-paragraph",
                text="An unsupported assertion.",
                metadata={"claim_ids": [unsupported.id]},
            ),
        ],
    )
    workspace.repository.save_manuscript(manuscript)

    with pytest.raises(StageExecutionError, match="unsupported claim"):
        ProductionStageFactory(FakeGeminiGateway()).citation_audit(_context(workspace, settings))

    assert workspace.repository.list_citations(workspace.project.id) == [previous]
    persisted = workspace.repository.get_latest_manuscript(workspace.project.id)
    assert persisted is not None
    assert persisted.bibliography == [bibliography]
    first = next(block for block in persisted.blocks if block.id == "supported-paragraph")
    assert isinstance(first, ParagraphBlock)
    assert first.citation_ids == [previous.id]


def test_fast_preflight_waits_for_quota_without_repeated_health_requests(tmp_path: Path) -> None:
    class QuotaUnavailableGateway(FakeGeminiGateway):
        def __init__(self) -> None:
            super().__init__()
            self.health_checks = 0

        def health_check(self, *, fail_fast: bool = False) -> None:
            assert fail_fast is True
            self.health_checks += 1
            raise GeminiUnavailableError(
                "quota exhausted; retry in 42s", retry_after_seconds=42
            )

    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(
        ProjectBrief(topic="Quota test"),
        AutopilotOptions(consent_to_remote_processing=True, generate_pdf=False),
    )
    gateway = QuotaUnavailableGateway()
    factory = ProductionStageFactory(gateway, repository=workspace.repository)
    service = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        factory.build(),
    )

    run = service.start()

    assert run.status is RunStatus.WAITING_INPUT
    preflight = workspace.repository.list_stages(run.id)[0]
    assert preflight.checkpoint["progress_message"] == "Gemini временно ограничил запросы"
    assert preflight.checkpoint["retry_after_seconds"] == 42
    resumed = service.resume(run.id)
    assert resumed.status is RunStatus.WAITING_INPUT
    assert gateway.health_checks == 1


def test_transient_research_unavailability_is_not_cached_as_unsupported(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Research outage"))
    claim = Claim(project_id=workspace.project.id, text="A required research claim")
    workspace.repository.save_claim(claim)
    gateway = FakeGeminiGateway()
    gateway.enqueue("search_grounded", GeminiUnavailableError("temporary provider outage"))

    context = _context(workspace, settings)
    factory = ProductionStageFactory(gateway)
    try:
        factory.verified_research(context)
    except RuntimeError as error:
        assert getattr(error, "waiting_input", False) is True
    else:
        raise AssertionError("A transient research outage must pause the stage")

    persisted = workspace.repository.list_claims(workspace.project.id)
    assert persisted[0].status is ClaimStatus.PENDING
    assert "research_verified_at" not in persisted[0].metadata


def test_completed_item_is_checkpointed_when_pause_arrives_during_callback(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Pause checkpoint"))
    context = _context(workspace, settings)
    context.run.status = RunStatus.PAUSED
    workspace.repository.save_run(context.run)

    ProductionStageFactory(FakeGeminiGateway())._record_work_item(
        context,
        item_id="completed-item",
        fingerprint="a" * 64,
        duration_ms=12,
        cache_hit=False,
        current=1,
        total=2,
        message="Готовый элемент сохранён",
    )

    persisted = workspace.repository.get_stage(context.stage.id)
    assert persisted is not None
    assert persisted.checkpoint["completed_items"]["completed-item"]["fingerprint"] == "a" * 64


def test_paid_section_response_is_checkpointed_after_cancel_and_resumed_without_replay(
    tmp_path: Path,
) -> None:
    """A cancel after Gemini replies keeps that response and starts no new request."""

    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Paid response checkpoint"))
    section = SectionSpec(id="intro", title="Introduction", order=0)
    workspace.repository.save_blueprint(
        ProjectBlueprint(
            project_id=workspace.project.id,
            topic="Paid response checkpoint",
            goal="Test",
            tasks=["Persist"],
            outline=Outline(sections=[section]),
        )
    )
    workspace.repository.save_requirement_set(RequirementSet(project_id=workspace.project.id))
    context = _context(workspace, settings)
    first_gateway = FakeGeminiGateway()

    def paid_writer(*, schema: type[object], **_kwargs: object) -> dict[str, object]:
        assert getattr(schema, "__name__", "") == "SectionDraft"
        # Model the provider completing (and charging) the request immediately
        # before the desktop writes its durable cancellation state.
        context.run.status = RunStatus.CANCELLED
        workspace.repository.save_run(context.run)
        return {
            "section_id": section.id,
            "blocks": [{"type": "paragraph", "text": "Saved paid response."}],
            "conclusion": "Saved conclusion",
            "word_count": 0,
        }

    first_gateway.enqueue("generate_structured", paid_writer)
    with pytest.raises(RunCancelled):
        ProductionStageFactory(first_gateway).generate_sections(context)

    assert [call["schema"] for call in first_gateway.calls] == ["SectionDraft"]
    stage = workspace.repository.get_stage(context.stage.id)
    assert stage is not None
    assert stage.checkpoint["completed_items"][section.id]["cache_hit"] is False
    artifacts = workspace.repository.list_artifacts(workspace.project.id)
    assert len(artifacts) == 1
    assert artifacts[0].metadata["quality_complete"] is False
    checkpoint = artifacts[0].metadata["quality_checkpoint"]
    assert isinstance(checkpoint, dict)
    assert checkpoint["phase"] == "critique"

    # A new run can continue the exact durable draft. It must pay only for the
    # next quality step, not replay the completed writer response.
    resumed_context = _context(workspace, settings)
    resumed_gateway = FakeGeminiGateway()
    resumed_gateway.enqueue("generate_structured", {"accepted": True})
    outcome = ProductionStageFactory(resumed_gateway).generate_sections(resumed_context)

    assert [call["schema"] for call in resumed_gateway.calls] == ["SectionCritique"]
    assert outcome.artifacts[0].metadata["quality_complete"] is True
    assert workspace.repository.get_latest_manuscript(workspace.project.id) is not None


def test_paid_grounded_response_is_checkpointed_after_cancel_without_replaying_search(
    tmp_path: Path,
) -> None:
    """Research resumes from the charged grounding response, not a new search."""

    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Grounding checkpoint"))
    claim = Claim(project_id=workspace.project.id, text="A checkable claim")
    workspace.repository.save_claim(claim)
    context = _context(workspace, settings)
    class CancelAfterGroundingGateway(FakeGeminiGateway):
        def search_grounded(
            self,
            *,
            prompt: str,
            role: str = "research",
            system_instruction: str | None = None,
        ) -> GroundedResult:
            response = super().search_grounded(
                prompt=prompt,
                role=role,
                system_instruction=system_instruction,
            )
            context.run.status = RunStatus.CANCELLED
            workspace.repository.save_run(context.run)
            return response

    first_gateway = CancelAfterGroundingGateway()
    first_gateway.enqueue(
        "search_grounded",
        GroundedResult(text="Already charged grounding", model="fake"),
    )
    with pytest.raises(RunCancelled):
        ProductionStageFactory(first_gateway).verified_research(context)

    assert [call["operation"] for call in first_gateway.calls] == ["search_grounded"]
    persisted = workspace.repository.list_claims(workspace.project.id)[0]
    checkpoint = persisted.metadata["research_grounded_checkpoint"]
    assert isinstance(checkpoint, dict)
    assert checkpoint["text"] == "Already charged grounding"

    class EmptyDiscovery:
        def search(self, _query: str, *, limit: int) -> list[object]:
            assert limit == 4
            return []

    resumed_context = _context(workspace, settings)
    resumed_gateway = FakeGeminiGateway()
    ProductionStageFactory(
        resumed_gateway,
        scholarly_discovery=EmptyDiscovery(),  # type: ignore[arg-type]
    ).verified_research(resumed_context)

    assert resumed_gateway.calls == []
    completed = workspace.repository.list_claims(workspace.project.id)[0]
    assert completed.status is ClaimStatus.UNSUPPORTED
    assert "research_grounded_checkpoint" not in completed.metadata


def test_force_refresh_does_not_repeat_completed_claim_after_provider_pause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    settings.performance_policy.max_research_requests = 1
    workspace = ProjectService(settings).create(ProjectBrief(topic="Refresh resume"))
    first = Claim(project_id=workspace.project.id, text="First claim", status=ClaimStatus.SUPPORTED)
    second = Claim(project_id=workspace.project.id, text="Second claim", status=ClaimStatus.SUPPORTED)
    workspace.repository.save_claim(first)
    workspace.repository.save_claim(second)
    context = _context(workspace, settings)
    context.run.metadata["force_research_refresh"] = True
    workspace.repository.save_run(context.run)
    factory = ProductionStageFactory(FakeGeminiGateway())
    calls: list[str] = []
    second_failed_once = False

    def verify(
        _factory: ProductionStageFactory,
        _context: StageContext,
        claim: Claim,
        fingerprint: str,
        _verifier: object,
        _discovery: object,
        _snapshot_root: Path,
        _check_cancelled: object,
    ) -> _ResearchClaimResult:
        nonlocal second_failed_once
        calls.append(claim.id)
        if claim.id == second.id and not second_failed_once:
            second_failed_once = True
            raise GeminiUnavailableError("temporary outage")
        return _ResearchClaimResult(
            claim_id=claim.id,
            fingerprint=fingerprint,
            supported=False,
        )

    monkeypatch.setattr(ProductionStageFactory, "_verify_research_claim", verify)
    try:
        factory.verified_research(context)
    except RuntimeError as error:
        assert getattr(error, "waiting_input", False) is True
    else:
        raise AssertionError("The first provider outage must interrupt the refresh")

    persisted_run = workspace.repository.get_run(context.run.id)
    assert persisted_run is not None
    assert "force_research_refresh" not in persisted_run.metadata
    persisted_claims = {
        claim.id: claim for claim in workspace.repository.list_claims(workspace.project.id)
    }
    # The claim whose fresh provider request was throttled still keeps its
    # previously verified result until a replacement is durably committed.
    assert persisted_claims[second.id].status is ClaimStatus.SUPPORTED
    assert persisted_claims[second.id].metadata.get("research_refresh_fingerprint")

    factory.verified_research(context)

    assert calls.count(first.id) == 1
    assert calls.count(second.id) == 2


def test_resume_transitions_a_paused_run_before_executing_handlers(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(
        ProjectBrief(topic="Paused run"),
        AutopilotOptions(generate_pdf=False),
    )

    def skip_stage(_context: StageContext) -> StageOutcome:
        return StageOutcome(skipped=True)

    service = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        {stage: skip_stage for stage in PipelineStage},
    )
    run = service.create_run()
    run.status = RunStatus.PAUSED
    workspace.repository.save_run(run)

    resumed = service.resume(run.id)

    assert resumed.status is RunStatus.SUCCEEDED


def test_direct_gemini_stage_uses_global_cooldown_before_retry(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Global cooldown"))
    attempts = 0

    def unavailable(_context: StageContext) -> StageOutcome:
        nonlocal attempts
        attempts += 1
        raise GeminiUnavailableError("quota exhausted", retry_after_seconds=30)

    service = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        {PipelineStage.PREFLIGHT: unavailable},
    )
    run = service.start()
    assert run.status is RunStatus.WAITING_INPUT
    stage = workspace.repository.list_stages(run.id)[0]
    assert stage.checkpoint["retry_after_seconds"] == 30

    resumed = service.resume(run.id)
    assert resumed.status is RunStatus.WAITING_INPUT
    assert attempts == 1


def test_provider_cooldown_preserves_uploaded_inputs_for_resume(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Keep remote sources"))
    seen_remote_files: list[list[object]] = []
    cleanup_calls: list[list[object]] = []
    attempts = 0

    def handler(context: StageContext) -> StageOutcome:
        nonlocal attempts
        if context.stage.name == PipelineStage.EXTRACT_REQUIREMENTS.value:
            attempts += 1
            remote_files = context.run.metadata.get("remote_files", [])
            seen_remote_files.append(list(remote_files) if isinstance(remote_files, list) else [])
            if attempts == 1:
                raise GeminiUnavailableError("quota exhausted")
        return StageOutcome(skipped=True)

    def cleanup(run: GenerationRun) -> None:
        remote_files = run.metadata.get("remote_files", [])
        cleanup_calls.append(list(remote_files) if isinstance(remote_files, list) else [])
        run.metadata["remote_files"] = []

    service = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        {stage: handler for stage in PipelineStage},
        terminal_hook=cleanup,
    )
    run = service.create_run()
    uploaded = {
        "source_id": "methodology",
        "name": "files/original.pdf",
        "uri": "fake://files/original.pdf",
        "mime_type": "application/pdf",
    }
    run.metadata["remote_files"] = [uploaded]
    workspace.repository.save_run(run)

    waiting = service.execute(run.id)
    assert waiting.status is RunStatus.WAITING_INPUT
    assert cleanup_calls == []
    assert waiting.metadata["remote_files"] == [uploaded]

    completed = service.resume(run.id)
    assert completed.status is RunStatus.SUCCEEDED
    assert seen_remote_files == [[uploaded], [uploaded]]
    assert cleanup_calls == [[uploaded]]


def test_visual_cache_uses_stable_render_inputs_not_random_block_id(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Visual cache"))
    context = _context(workspace, settings)
    factory = ProductionStageFactory(FakeGeminiGateway())
    first = FigureBlock(caption="Architecture", image_spec=ImageSpec(prompt="clean architecture"))
    second = FigureBlock(caption="Architecture", image_spec=ImageSpec(prompt="clean architecture"))

    fingerprint = factory._visual_fingerprint(context, first, None)
    assert fingerprint == factory._visual_fingerprint(context, second, None)

    path = workspace.paths.artifacts / "cached-image.png"
    path.write_bytes(b"cached image bytes")
    cached = Artifact(
        project_id=workspace.project.id,
        run_id=context.run.id,
        stage_id=context.stage.id,
        kind=ArtifactKind.IMAGE,
        path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size_bytes=path.stat().st_size,
        mime_type="image/png",
        metadata={"block_id": first.id, "fingerprint": fingerprint},
    )
    workspace.repository.save_artifact(cached)

    assert factory._cached_visual_artifact(
        context,
        fingerprint=fingerprint,
        kind=ArtifactKind.IMAGE,
    ) == cached


def test_input_fingerprint_ignores_save_timestamp_but_tracks_model_and_quality_inputs(
    tmp_path: Path,
) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    projects = ProjectService(settings)
    workspace = projects.create(ProjectBrief(topic="Stable inputs"))
    first = AutopilotService(settings, workspace.project, workspace.repository, workspace.paths, {})
    initial = first._input_hash()

    saved = projects.update(
        workspace.project.id,
        brief=workspace.project.brief,
        options=workspace.project.options,
    )
    after_save = AutopilotService(settings, saved.project, saved.repository, saved.paths, {})._input_hash()
    assert after_save == initial

    legacy_quality = saved.project.options.model_copy(update={"quality_mode": "economy"})
    legacy = projects.update(saved.project.id, options=legacy_quality)
    assert AutopilotService(settings, legacy.project, legacy.repository, legacy.paths, {})._input_hash() == initial

    settings.model_policy.critic = "gemini-3.7-flash-revision"
    changed_model = AutopilotService(settings, legacy.project, legacy.repository, legacy.paths, {})._input_hash()
    assert changed_model != initial


def test_user_reference_is_an_input_and_is_not_skipped_by_ingest(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    settings.remote_file_consent_required = False
    workspace = ProjectService(settings).create(ProjectBrief(topic="Reference input"))
    service = AutopilotService(settings, workspace.project, workspace.repository, workspace.paths, {})
    before_import = service._input_hash()
    reference = tmp_path / "user-reference.txt"
    reference.write_text("A user supplied reference document.", encoding="utf-8")
    SourceService(workspace).import_files([reference], SourceRole.REFERENCE)

    current_project = workspace.repository.get_project(workspace.project.id)
    assert current_project is not None
    current = AutopilotService(
        settings,
        current_project,
        workspace.repository,
        workspace.paths,
        {},
    )
    assert current._input_hash() != before_import

    context = _context(workspace, settings)
    factory = ProductionStageFactory(FakeGeminiGateway())
    assert factory.preflight(context).checkpoint["sources"] == 1
    assert factory.ingest(context).checkpoint["uploaded"] == 0


def test_new_research_plan_does_not_mix_stale_claims_into_active_set(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Active research plan"))
    stale = Claim(
        project_id=workspace.project.id,
        text="Stale claim from an older topic",
        metadata={
            "research_plan_fingerprint": "obsolete",
            "research_plan_digest": "obsolete",
            "research_plan_count": 1,
            "research_plan_index": 0,
        },
    )
    workspace.repository.save_claim(stale)
    gateway = FakeGeminiGateway()
    gateway.enqueue(
        "generate_structured",
        {
            "claims": [
                {
                    "text": "Current claim for this run",
                    "search_query": "current query",
                    "importance": "critical",
                }
            ]
        },
    )
    context = _context(workspace, settings)
    factory = ProductionStageFactory(gateway)
    factory.build_evidence_index(context)

    all_claims = workspace.repository.list_claims(workspace.project.id)
    active = factory._active_research_claims(context)
    assert {claim.id for claim in all_claims} == {stale.id, active[0].id}
    assert [claim.text for claim in active] == ["Current claim for this run"]
    assert context.run.metadata["active_research_plan_claim_ids"] == [active[0].id]


def test_parallel_provider_errors_use_the_longest_retry_after() -> None:
    short = GeminiUnavailableError("quota", retry_after_seconds=10)
    long = GeminiUnavailableError("quota", retry_after_seconds=60)
    selected = _longest_provider_error(
        [SimpleNamespace(error=short), SimpleNamespace(error=long)]
    )

    assert selected is long


def test_research_claim_fingerprint_includes_critic_configuration(tmp_path: Path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects", minimum_free_space_mb=128)
    workspace = ProjectService(settings).create(ProjectBrief(topic="Critic fingerprint"))
    context = _context(workspace, settings)
    claim = Claim(project_id=workspace.project.id, text="Claim")
    factory = ProductionStageFactory(FakeGeminiGateway())

    initial = factory._claim_fingerprint(context, claim)
    settings.thinking_policy.critic = "low"

    assert factory._claim_fingerprint(context, claim) != initial
