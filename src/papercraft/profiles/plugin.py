"""Executable profile contract used by planning, facts and QA stages."""

from __future__ import annotations

from typing import Protocol

from papercraft.domain import (
    Dataset,
    FactRecord,
    HeadingBlock,
    Manuscript,
    Project,
    ProjectBlueprint,
)
from papercraft.infrastructure.calculations import validate_finance_dataset

from .models import WorkProfile


class ProfilePlugin(Protocol):
    def build_blueprint(self, project: Project) -> ProjectBlueprint: ...
    def required_artifacts(self) -> list[str]: ...
    def prepare_facts(self, facts: list[FactRecord]) -> list[FactRecord]: ...
    def validate_calculations(self, datasets: list[Dataset]) -> list[str]: ...
    def validate_manuscript(self, manuscript: Manuscript) -> list[str]: ...
    def final_requirements(self) -> list[str]: ...


class WorkProfilePlugin:
    """Default implementation for every declarative built-in work profile."""

    def __init__(self, profile: WorkProfile) -> None:
        self.profile = profile

    def build_blueprint(self, project: Project) -> ProjectBlueprint:
        from papercraft.domain import Outline, SectionSpec

        return ProjectBlueprint(
            project_id=project.id,
            topic=project.brief.topic or project.brief.title,
            outline=Outline(
                sections=[
                    SectionSpec(
                        id=section.key,
                        title=section.title,
                        level=section.level,
                        order=index,
                        target_words=section.target_words,
                        expected_conclusion=section.purpose,
                    )
                    for index, section in enumerate(self.profile.sections)
                ]
            ),
        )

    def required_artifacts(self) -> list[str]:
        return list(self.profile.policy.required_artifacts)

    def prepare_facts(self, facts: list[FactRecord]) -> list[FactRecord]:
        return facts

    def validate_calculations(self, datasets: list[Dataset]) -> list[str]:
        if "finance" not in self.profile.domain_tags and "accounting" not in self.profile.domain_tags:
            return []
        return [
            issue.message
            for dataset in datasets
            if {"debit_account", "credit_account", "amount"} <= {column.name for column in dataset.columns}
            for issue in validate_finance_dataset(dataset).issues
        ]

    def validate_manuscript(self, manuscript: Manuscript) -> list[str]:
        headings = {
            block.text.casefold() for block in manuscript.blocks if isinstance(block, HeadingBlock)
        }
        return [
            f"required profile section is missing: {section.title}"
            for section in self.profile.sections
            if section.required and section.title.casefold() not in headings
        ]

    def final_requirements(self) -> list[str]:
        return [f"minimum_sources={self.profile.policy.minimum_sources}", *self.required_artifacts()]


__all__ = ["ProfilePlugin", "WorkProfilePlugin"]
