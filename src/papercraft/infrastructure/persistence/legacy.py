"""Safe migration of the prototype ``.courseproject`` JSON format."""

from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue

from papercraft.domain import (
    DomainProfile,
    HeadingBlock,
    Manuscript,
    ManuscriptBlock,
    Outline,
    ParagraphBlock,
    Project,
    ProjectBlueprint,
    ProjectBrief,
    RequirementCategory,
    RequirementPriority,
    RequirementRule,
    RequirementSet,
    RuleProvenance,
    SectionSpec,
    Source,
    SourceRole,
    WorkType,
    new_id,
)

from .paths import ProjectPaths
from .repository import SQLiteRepository
from .storage import AtomicArtifactStore, ImmutableFileStorage

_LEGACY_LIMIT_BYTES = 50 * 1024 * 1024
_SAFE_LEGACY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(slots=True)
class LegacyImportResult:
    project: Project
    paths: ProjectPaths
    sources: list[Source] = field(default_factory=list)
    requirements: RequirementSet | None = None
    blueprint: ProjectBlueprint | None = None
    manuscript: Manuscript | None = None
    warnings: list[str] = field(default_factory=list)


def _legacy_type(value: object) -> tuple[WorkType, DomainProfile]:
    mapping = {
        "coursework_it": (WorkType.COURSEWORK, DomainProfile.IT),
        "coursework_programming": (WorkType.COURSEWORK, DomainProfile.PROGRAMMING),
        "coursework_finance": (WorkType.COURSEWORK, DomainProfile.FINANCE),
        "coursework_accounting": (WorkType.COURSEWORK, DomainProfile.ACCOUNTING),
        "scientific_article": (WorkType.SCIENTIFIC_ARTICLE, DomainProfile.SCIENCE),
        "school_project": (WorkType.SCHOOL_PROJECT, DomainProfile.SCHOOL),
        "practice_report": (WorkType.PRACTICE_REPORT, DomainProfile.GENERAL),
        "lab_report": (WorkType.LAB_REPORT, DomainProfile.GENERAL),
        "industrial_report": (WorkType.INDUSTRIAL_REPORT, DomainProfile.GENERAL),
    }
    return mapping.get(str(value), (WorkType.UNIVERSAL, DomainProfile.UNIVERSAL))


def _load_legacy(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() != ".courseproject":
        raise ValueError("legacy project must have a .courseproject extension")
    if path.stat().st_size > _LEGACY_LIMIT_BYTES:
        raise ValueError("legacy project is too large")
    with path.open("r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("legacy project root must be a JSON object")
    return value


class LegacyCourseProjectImporter:
    """Convert a prototype project into the v1 project layout.

    Migration is additive: the original file and all readable knowledge-base
    files are copied into immutable storage.  The importer never modifies the
    legacy file in place.
    """

    def __init__(self, projects_root: str | Path) -> None:
        self.projects_root = Path(projects_root).expanduser().resolve()

    def import_file(self, legacy_path: str | Path) -> LegacyImportResult:
        path = Path(legacy_path).expanduser().resolve()
        data = _load_legacy(path)
        requested_id = str(data.get("project_id", ""))
        project_id = requested_id if _SAFE_LEGACY_ID.fullmatch(requested_id) else new_id()
        if (self.projects_root / project_id).exists():
            project_id = new_id()

        paths = ProjectPaths.for_project(project_id, self.projects_root, create=True)
        work_type, profile = _legacy_type(data.get("project_type"))
        topic = str(data.get("topic") or "")
        raw_title_page = data.get("title_page_data")
        title_page = cast(dict[str, JsonValue], raw_title_page) if isinstance(raw_title_page, dict) else {}
        project = Project(
            id=project_id,
            brief=ProjectBrief(
                topic=topic,
                title=topic,
                work_type=work_type,
                domain_profile=profile,
                title_page=title_page,
            ),
            metadata={
                "imported_from": path.name,
                "legacy_project_type": str(data.get("project_type") or ""),
                "legacy_current_step": int(data.get("current_step") or 1),
            },
        )
        repository = SQLiteRepository(paths.database)
        repository.save_project(project)
        result = LegacyImportResult(project=project, paths=paths)

        immutable = ImmutableFileStorage(paths.originals)
        immutable.store(path)
        knowledge_files = data.get("knowledge_base_files", [])
        if not isinstance(knowledge_files, list):
            result.warnings.append("knowledge_base_files was not a list and was ignored")
            knowledge_files = []
        for raw_file in knowledge_files:
            candidate = Path(str(raw_file)).expanduser()
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            try:
                stored = immutable.store(candidate.resolve())
            except (FileNotFoundError, OSError, ValueError) as error:
                result.warnings.append(f"could not import {raw_file!s}: {error}")
                continue
            source = Source(
                project_id=project.id,
                role=SourceRole.SOURCE_DATA,
                original_name=stored.original_name,
                stored_path=stored.path.relative_to(paths.root).as_posix(),
                sha256=stored.sha256,
                mime_type=mimetypes.guess_type(stored.original_name)[0] or "application/octet-stream",
                size_bytes=stored.size_bytes,
                metadata={"legacy_path": str(raw_file)},
            )
            repository.save_source(source)
            result.sources.append(source)

        formatting = data.get("formatting_rules")
        if isinstance(formatting, dict) and formatting:
            rules = [
                RequirementRule(
                    category=RequirementCategory.PAGE_LAYOUT
                    if str(key).startswith("margin_")
                    else RequirementCategory.TYPOGRAPHY,
                    key=str(key),
                    statement=f"Legacy formatting setting: {key}",
                    value=value,
                    provenance=[RuleProvenance(priority=RequirementPriority.USER, extraction_method="legacy_import")],
                )
                for key, value in formatting.items()
            ]
            result.requirements = RequirementSet(project_id=project.id, rules=rules)
            repository.save_requirement_set(result.requirements)

        outline = self._convert_outline(data.get("plan_structure"))
        if topic or outline.sections:
            result.blueprint = ProjectBlueprint(
                project_id=project.id,
                topic=topic or "Untitled imported project",
                outline=outline,
            )
            repository.save_blueprint(result.blueprint)

        result.manuscript = self._convert_manuscript(project, outline, data.get("sections_content"))
        if result.manuscript is not None:
            repository.save_manuscript(result.manuscript)

        # A sanitized snapshot aids audits without requiring the old application.
        AtomicArtifactStore(paths.derived).write_json("legacy_import.json", data)
        return result

    @staticmethod
    def _convert_outline(raw_plan: object) -> Outline:
        if not isinstance(raw_plan, list):
            return Outline()
        sections: list[SectionSpec] = []
        used_ids: set[str] = set()
        for order, item in enumerate(raw_plan):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            candidate_id = str(item.get("id") or f"section-{order + 1}")
            section_id = candidate_id
            counter = 2
            while section_id in used_ids:
                section_id = f"{candidate_id}-{counter}"
                counter += 1
            used_ids.add(section_id)
            try:
                target_words = max(0, int(item.get("target_words") or 0))
            except (TypeError, ValueError):
                target_words = 0
            sections.append(
                SectionSpec(
                    id=section_id,
                    title=title,
                    order=order,
                    target_words=target_words,
                )
            )
        return Outline(sections=sections)

    @staticmethod
    def _convert_manuscript(project: Project, outline: Outline, raw_sections: object) -> Manuscript | None:
        content = raw_sections if isinstance(raw_sections, dict) else {}
        blocks: list[ManuscriptBlock] = []
        seen: set[str] = set()
        for section in outline.sections:
            blocks.append(HeadingBlock(text=section.title, level=section.level, section_id=section.id))
            text = content.get(section.id)
            if text is not None and str(text).strip():
                blocks.append(ParagraphBlock(text=str(text)))
            seen.add(section.id)
        for section_id, text in content.items():
            if str(section_id) in seen or not str(text).strip():
                continue
            blocks.extend(
                [
                    HeadingBlock(text=str(section_id), level=1, section_id=str(section_id)),
                    ParagraphBlock(text=str(text)),
                ]
            )
        if not blocks:
            return None
        return Manuscript(project_id=project.id, title=project.brief.title, blocks=blocks)
