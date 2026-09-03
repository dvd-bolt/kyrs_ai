from __future__ import annotations

import pytest
from pydantic import ValidationError

from papercraft.application import AutopilotService, PipelineStage, ProjectService, StageOutcome
from papercraft.application.context import ContextBuilder
from papercraft.application.schemas import SectionDraft
from papercraft.application.stages import _ensure_profile_sections
from papercraft.config import AppSettings
from papercraft.domain import (
    AutopilotOptions,
    Claim,
    ClaimStatus,
    Outline,
    ProjectBlueprint,
    ProjectBrief,
    SectionSpec,
)
from papercraft.profiles import default_profile_registry


def test_profile_snapshots_are_fixed_to_the_four_mvp_work_types() -> None:
    profiles = {profile.id: profile for profile in default_profile_registry().all()}

    assert {profile_id: tuple(section.key for section in profile.sections) for profile_id, profile in profiles.items()} == {
        "coursework": ("introduction", "theory", "analysis", "practice", "conclusion", "bibliography", "appendix"),
        "scientific_article": ("abstract", "introduction", "theory", "practical", "conclusion", "bibliography"),
        "practice_report": ("introduction", "organisation", "work", "results", "conclusion", "bibliography", "appendix"),
        "school_project": ("introduction", "theory", "practice", "conclusion", "bibliography", "appendix"),
    }


def test_planning_appends_required_profile_sections_before_writing() -> None:
    profile = default_profile_registry().get("school_project")
    blueprint = ProjectBlueprint(
        project_id="project-1",
        topic="Тема",
        outline=Outline(sections=[SectionSpec(id="custom", title="Пользовательский раздел")]),
    )

    completed = _ensure_profile_sections(blueprint, profile)

    assert [section.title for section in completed.outline.sections] == [
        "Пользовательский раздел",
        *[section.title for section in profile.sections],
    ]


def test_section_context_never_expands_to_unrelated_evidence() -> None:
    selected = Claim(id="selected", project_id="project-1", text="Selected", status=ClaimStatus.SUPPORTED)
    unrelated = Claim(id="unrelated", project_id="project-1", text="Unrelated", status=ClaimStatus.SUPPORTED)
    section = SectionSpec(id="section-1", title="Раздел", required_claim_ids=[selected.id])
    blueprint = ProjectBlueprint(project_id="project-1", topic="Тема", outline=Outline(sections=[section]))

    context = ContextBuilder().build(
        section,
        blueprint,
        [selected, unrelated],
        [],
        [],
        [],
        [],
    )

    assert context.claims == [selected]
    assert context.evidence == []
    assert context.bibliography == []
    assert context.datasets == []


@pytest.mark.parametrize("payload", ["plain text", {"section_id": "s", "blocks": []}, {"section_id": "s", "blocks": [{"type": "unknown"}]}])
def test_section_draft_rejects_unstructured_or_invalid_provider_output(payload: object) -> None:
    with pytest.raises(ValidationError):
        SectionDraft.model_validate(payload)


def test_legacy_checkpoint_flags_cannot_pause_autopilot(tmp_path) -> None:
    settings = AppSettings(projects_root=tmp_path / "projects")
    workspace = ProjectService(settings).create(
        ProjectBrief(topic="Автопилот"),
        AutopilotOptions(
            checkpoint_requirements=True,
            checkpoint_outline=True,
            checkpoint_final_review=True,
        ),
    )
    service = AutopilotService(
        settings,
        workspace.project,
        workspace.repository,
        workspace.paths,
        {stage: lambda _context: StageOutcome(skipped=True) for stage in PipelineStage},
    )

    run = service.start()
    assert run.status.value == "failed"  # package is intentionally not produced by this stub
    plan_stage = next(stage for stage in workspace.repository.list_stages(run.id) if stage.name == "plan")
    assert plan_stage.status.value == "skipped"
