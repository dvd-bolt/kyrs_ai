from __future__ import annotations

import pytest

from papercraft.application.requirements import build_requirement_coverage_report
from papercraft.domain import (
    Dataset,
    DatasetColumn,
    DataType,
    FactOrigin,
    FactRecord,
    HeadingBlock,
    Locator,
    Manuscript,
    ParagraphBlock,
    QAStatus,
    RequirementCoverage,
    RequirementCoverageAssessment,
    RequirementCoverageReport,
    RequirementPdfPageMapping,
    RequirementPriority,
    RequirementRule,
    RequirementSet,
    RuleProvenance,
    TableBlock,
    TableSpec,
)
from papercraft.infrastructure.qa import DeterministicQualityGate, QAGateContext
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


def _requirements() -> RequirementSet:
    return RequirementSet(
        id="requirements-1",
        project_id="project-1",
        rules=[
            RequirementRule(
                id="critical-rule",
                key="a.required_heading",
                statement="The work must include a title section.",
                mandatory=True,
                provenance=[
                    RuleProvenance(
                        source_id="methodology-1",
                        locator=Locator(
                            source_id="methodology-1", page=4, section="Structure"
                        ),
                        priority=RequirementPriority.METHODOLOGY,
                        extraction_method="fixture",
                    )
                ],
            ),
            RequirementRule(
                id="optional-rule",
                key="z.optional_layout",
                statement="Use the preferred layout where possible.",
                mandatory=False,
                provenance=[RuleProvenance(priority=RequirementPriority.PROFILE)],
            ),
        ],
    )


def test_coverage_report_is_complete_traceable_and_stably_ordered() -> None:
    requirements = _requirements()
    report = build_requirement_coverage_report(
        requirements,
        coverage=[
            RequirementCoverage(
                requirement_rule_id="optional-rule",
                status="SATISFIED",
                evidence="Verified by render configuration.",
                artifact_id="render-config",
            )
        ],
        assessments={
            "critical-rule": RequirementCoverageAssessment(
                status="covered",
                block_ids=["body-1", "heading-1"],
                pdf_page_mappings=[
                    RequirementPdfPageMapping(block_id="heading-1", pages=[3, 2, 3])
                ],
                evidence_summary="The heading appears in the rendered manuscript.",
            )
        },
    )

    assert [entry.requirement_rule_id for entry in report.entries] == [
        "critical-rule",
        "optional-rule",
    ]
    critical, optional = report.entries
    assert critical.status == "covered"
    assert critical.mandatory is True
    assert critical.criticality == "critical"
    assert critical.priority is RequirementPriority.METHODOLOGY
    assert critical.block_ids == ["body-1", "heading-1"]
    assert critical.pdf_page_mappings[0].pages == [2, 3]
    assert critical.source_locators == [Locator(source_id="methodology-1", page=4, section="Structure")]
    assert optional.status == "covered"
    assert optional.criticality == "standard"
    assert optional.evidence_summary == "Verified by render configuration."
    assert optional.artifact_id == "render-config"
    assert not report.has_blocking_gaps
    assert RequirementCoverageReport.model_validate_json(report.model_dump_json()) == report


def test_coverage_report_marks_unassessed_rules_missing_and_rejects_unknown_inputs() -> None:
    requirements = _requirements()
    report = build_requirement_coverage_report(requirements)
    assert [entry.status for entry in report.entries] == ["missing", "missing"]
    assert report.entries[0].reason == "No coverage assessment was recorded."
    assert report.has_blocking_gaps

    with pytest.raises(ValueError, match="unknown requirement rules"):
        build_requirement_coverage_report(
            requirements,
            assessments={"not-a-rule": RequirementCoverageAssessment(status="covered")},
        )


def test_mandatory_profile_defaults_are_visible_but_not_formal_export_blockers() -> None:
    requirements = RequirementSet(
        id="requirements-profile",
        project_id="project-1",
        rules=[
            RequirementRule(
                id="profile-rule",
                key="profile.structure.introduction",
                statement="Include an introduction.",
                mandatory=True,
                provenance=[RuleProvenance(priority=RequirementPriority.PROFILE)],
            )
        ],
    )

    report = build_requirement_coverage_report(requirements)

    assert report.entries[0].status == "missing"
    assert report.entries[0].criticality == "standard"
    assert report.entries[0].mandatory is True
    assert not report.has_blocking_gaps


def test_qa_gate_exports_blockers_for_critical_coverage_and_evidence_gaps() -> None:
    requirements = _requirements()
    coverage = build_requirement_coverage_report(
        requirements,
        assessments={
            "critical-rule": RequirementCoverageAssessment(
                status="partial",
                block_ids=["heading-1"],
                pdf_page_mappings=[RequirementPdfPageMapping(block_id="heading-1", pages=[1])],
            ),
            "optional-rule": RequirementCoverageAssessment(
                status="covered",
                evidence_gaps=["A source locator was not retained."],
            ),
        },
    )
    manuscript = Manuscript(
        project_id="project-1",
        title="Coverage QA",
        blocks=[
            HeadingBlock(id="heading-1", text="Introduction", level=1),
            ParagraphBlock(id="body-1", text="Evidence-backed content."),
        ],
    )

    report = DeterministicQualityGate().run(
        QAGateContext(
            project_id="project-1",
            run_id="run-1",
            profile=_qa_profile(),
            manuscript=manuscript,
            requirements=requirements,
            requirement_coverage=coverage,
        )
    )

    assert report.requirement_coverage == coverage
    assert report.status is QAStatus.FAIL
    blockers = [issue for issue in report.issues if issue.category == "requirement_coverage"]
    evidence_gaps = [issue for issue in report.issues if issue.category == "requirement_evidence_gap"]
    assert [issue.requirement_rule_id for issue in blockers] == ["critical-rule"]
    assert [issue.requirement_rule_id for issue in evidence_gaps] == ["optional-rule"]
    assert blockers[0].metadata["pdf_page_mappings"] == [{"block_id": "heading-1", "pages": [1]}]


def test_qa_gate_rejects_a_coverage_report_that_omits_a_requirement() -> None:
    requirements = _requirements()
    complete = build_requirement_coverage_report(
        requirements,
        assessments={
            "critical-rule": RequirementCoverageAssessment(status="covered"),
            "optional-rule": RequirementCoverageAssessment(status="covered"),
        },
    )
    incomplete = RequirementCoverageReport(
        project_id=complete.project_id,
        requirement_set_id=complete.requirement_set_id,
        entries=[complete.entries[0]],
    )
    report = DeterministicQualityGate().run(
        QAGateContext(
            project_id="project-1",
            run_id="run-1",
            profile=_qa_profile(),
            manuscript=Manuscript(
                project_id="project-1",
                title="Coverage QA",
                blocks=[ParagraphBlock(text="Evidence-backed content.")],
            ),
            requirements=requirements,
            requirement_coverage=incomplete,
        )
    )

    incomplete_issues = [
        issue for issue in report.issues if issue.category == "requirement_coverage_incomplete"
    ]
    assert len(incomplete_issues) == 1
    assert incomplete_issues[0].metadata == {"requirement_rule_ids": ["optional-rule"]}


def test_qa_gate_blocks_critical_covered_requirement_without_a_location() -> None:
    requirements = _requirements()
    coverage = build_requirement_coverage_report(
        requirements,
        assessments={
            "critical-rule": RequirementCoverageAssessment(status="covered"),
            "optional-rule": RequirementCoverageAssessment(status="covered"),
        },
    )
    report = DeterministicQualityGate().run(
        QAGateContext(
            project_id="project-1",
            run_id="run-1",
            profile=_qa_profile(),
            manuscript=Manuscript(
                project_id="project-1",
                title="Coverage QA",
                blocks=[ParagraphBlock(text="Evidence-backed content.")],
            ),
            requirements=requirements,
            requirement_coverage=coverage,
        )
    )

    assert coverage.has_blocking_gaps
    traceability = [
        issue
        for issue in report.issues
        if issue.category == "requirement_coverage_traceability"
    ]
    assert len(traceability) == 1
    assert traceability[0].severity.value == "blocker"
    assert traceability[0].requirement_rule_id == "critical-rule"


def test_qa_gate_blocks_unbound_user_authored_paragraph() -> None:
    report = DeterministicQualityGate().run(
        QAGateContext(
            project_id="project-1",
            run_id="run-1",
            profile=_qa_profile(),
            manuscript=Manuscript(
                project_id="project-1",
                title="User edit evidence",
                blocks=[
                    ParagraphBlock(
                        text="A substantial user-authored assertion.",
                        metadata={"user_override": True, "evidence_review_required": True},
                    )
                ],
            ),
        )
    )

    assert report.status is QAStatus.FAIL
    assert [issue.category for issue in report.issues] == ["user_edit_evidence"]


def test_qa_gate_blocks_generated_inline_numeric_table_without_provenance() -> None:
    report = DeterministicQualityGate().run(
        QAGateContext(
            project_id="project-1",
            run_id="run-1",
            profile=_qa_profile(),
            manuscript=Manuscript(
                project_id="project-1",
                title="Generated table evidence",
                blocks=[
                    TableBlock(
                        spec=TableSpec(headers=["Year", "Value"], rows=[[2025, 1_000_000]]),
                    )
                ],
            ),
        )
    )

    assert report.status is QAStatus.FAIL
    assert [issue.category for issue in report.issues] == ["numeric_provenance"]


def test_qa_gate_accepts_inline_numeric_table_bound_to_known_facts() -> None:
    fact = FactRecord(
        id="verified-total",
        project_id="project-1",
        name="Verified total",
        value=1_000_000,
        origin=FactOrigin.VERIFIED_SOURCE,
        source_id="methodology-source",
    )
    report = DeterministicQualityGate().run(
        QAGateContext(
            project_id="project-1",
            run_id="run-1",
            profile=_qa_profile(),
            manuscript=Manuscript(
                project_id="project-1",
                title="User table facts",
                blocks=[
                    TableBlock(
                        spec=TableSpec(headers=["Year", "Value"], rows=[[2025, 1_000_000]]),
                        numeric_fact_ids=[fact.id],
                    )
                ],
            ),
            facts=[fact],
        )
    )

    assert report.status is QAStatus.PASS


def test_qa_gate_accepts_inline_numeric_table_bound_to_known_dataset() -> None:
    dataset = Dataset(
        id="dataset-1",
        project_id="project-1",
        name="Verified dataset",
        columns=[DatasetColumn(name="year", data_type=DataType.INTEGER)],
        rows=[{"year": 2025}],
        origin=FactOrigin.SYNTHETIC,
        synthetic_seed=1,
        generation_method="fixture",
    )
    report = DeterministicQualityGate().run(
        QAGateContext(
            project_id="project-1",
            run_id="run-1",
            profile=_qa_profile(),
            manuscript=Manuscript(
                project_id="project-1",
                title="Dataset table",
                blocks=[
                    ParagraphBlock(text="Synthetic demonstration dataset."),
                    TableBlock(
                        spec=TableSpec(
                            dataset_id=dataset.id,
                            headers=["Year"],
                            rows=[[2025]],
                        ),
                    )
                ],
            ),
            datasets=[dataset],
        )
    )

    assert report.status is QAStatus.PASS


def test_qa_gate_allows_text_only_inline_table_without_numeric_provenance() -> None:
    report = DeterministicQualityGate().run(
        QAGateContext(
            project_id="project-1",
            run_id="run-1",
            profile=_qa_profile(),
            manuscript=Manuscript(
                project_id="project-1",
                title="Text table",
                blocks=[
                    TableBlock(
                        spec=TableSpec(
                            headers=["Status", "Comment"],
                            rows=[["approved", "manual review"]],
                        ),
                    )
                ],
            ),
        )
    )

    assert report.status is QAStatus.PASS
