"""Durable user-authored section revisions and their minimal rebuild plans.

The generation pipeline owns its generated manuscript snapshots.  This module
adds a small application boundary for a user edit: it never mutates that
snapshot in place, records the source and every later override separately,
and returns the smallest downstream pipeline suffix that needs to run.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections import deque
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter, ValidationError

from papercraft.domain import (
    AppendixBlock,
    ChartBlock,
    DiagramBlock,
    FigureBlock,
    GenerationRun,
    HeadingBlock,
    Manuscript,
    ManuscriptBlock,
    ParagraphBlock,
    ProjectBlueprint,
    RevisionRecord,
    TableBlock,
    new_id,
    utc_now,
)

from .autopilot import PIPELINE_ORDER, PipelineStage
from .ports import RepositoryPort

_SECTION_BLOCKS = TypeAdapter(list[ManuscriptBlock])
_RevisionSource = Literal["generated", "user_override", "restore"]


@dataclass(frozen=True, slots=True)
class SectionInvalidation:
    """The exact pipeline suffix made stale by a section-level change."""

    start_stage: PipelineStage
    stages: tuple[PipelineStage, ...]
    section_ids: tuple[str, ...]
    plan_edit: bool = False

    @property
    def stage_names(self) -> tuple[str, ...]:
        """Stable string form for a worker command or UI adapter."""

        return tuple(stage.value for stage in self.stages)


@dataclass(frozen=True, slots=True)
class SectionRevision:
    """One immutable generated, user-authored, or restored section version."""

    record: RevisionRecord
    section_id: str
    blocks: tuple[ManuscriptBlock, ...]
    source: _RevisionSource
    restored_from_id: str | None = None


@dataclass(frozen=True, slots=True)
class SectionRevisionResult:
    """Persisted manuscript snapshot plus the follow-up work it requires."""

    manuscript: Manuscript
    revision: SectionRevision
    invalidation: SectionInvalidation

    @property
    def record(self) -> RevisionRecord:
        """Convenient access to the durable revision record."""

        return self.revision.record


@dataclass(frozen=True, slots=True)
class PlanRevision:
    """One immutable generated, user-authored, or restored plan version."""

    record: RevisionRecord
    blueprint: ProjectBlueprint
    source: _RevisionSource
    restored_from_id: str | None = None


@dataclass(frozen=True, slots=True)
class PlanRevisionResult:
    """Persisted plan snapshot plus its targeted downstream rebuild suffix."""

    blueprint: ProjectBlueprint
    revision: PlanRevision
    invalidation: SectionInvalidation

    @property
    def record(self) -> RevisionRecord:
        """Convenient access to the durable revision record."""

        return self.revision.record


class SectionRevisionService:
    """Apply a user edit without overwriting the generated manuscript version.

    A normal content edit preserves the generated plan and only needs a new
    citation audit, consistency QA, and document/export suffix.  A saved plan
    revision is already the durable output of the planning decision, so its
    safe rebuild begins with section generation rather than invoking Gemini's
    planner again and overwriting the user's edit.  ``section_ids`` identifies
    the current affected dependency closure for a targeted worker
    implementation.
    """

    def __init__(self, project_id: str, repository: RepositoryPort) -> None:
        normalized_project_id = project_id.strip()
        if not normalized_project_id:
            raise ValueError("project_id must not be blank")
        self.project_id = normalized_project_id
        self.repository = repository

    def revise_section(
        self,
        section_id: str,
        blocks: Sequence[ManuscriptBlock] | str,
        *,
        plan_edit: bool = False,
    ) -> SectionRevisionResult:
        """Replace one section while keeping the prior generated text restorable.

        ``blocks`` may contain the complete section (including its heading),
        or only the body.  In the latter case the original section heading is
        retained.  A string is a convenience shorthand for a single paragraph.
        """

        return self._commit(
            section_id,
            blocks,
            source="user_override",
            plan_edit=plan_edit,
            restored_from_id=None,
        )

    def revise(
        self,
        section_id: str,
        blocks: Sequence[ManuscriptBlock] | str,
        *,
        plan_edit: bool = False,
    ) -> SectionRevisionResult:
        """Alias for :meth:`revise_section` used by lightweight callers."""

        return self.revise_section(section_id, blocks, plan_edit=plan_edit)

    def restore_revision(
        self,
        section_id: str,
        revision: RevisionRecord | int | str,
        *,
        plan_edit: bool = False,
    ) -> SectionRevisionResult:
        """Restore a historical section version as a *new* user revision."""

        target = self._find_revision(self._section_id(section_id), revision)
        return self._commit(
            target.section_id,
            target.blocks,
            source="restore",
            plan_edit=plan_edit,
            restored_from_id=target.record.id,
        )

    def restore_previous_revision(
        self,
        section_id: str,
        *,
        plan_edit: bool = False,
    ) -> SectionRevisionResult:
        """Restore the version immediately preceding the active section version."""

        normalized_section_id = self._section_id(section_id)
        history = self.list_revisions(normalized_section_id)
        if len(history) < 2:
            raise RuntimeError(f"Section {normalized_section_id!r} has no previous revision")
        return self.restore_revision(normalized_section_id, history[1].record, plan_edit=plan_edit)

    def list_revisions(self, section_id: str) -> list[SectionRevision]:
        """Return immutable section history from newest to oldest."""

        normalized_section_id = self._section_id(section_id)
        records = self.repository.list_section_revisions(self.project_id, normalized_section_id)
        result: list[SectionRevision] = []
        for record in records:
            payload = self.repository.get_section_revision_payload(self.project_id, record.id)
            if payload is None:
                raise RuntimeError(f"Missing payload for section revision {record.id}")
            result.append(self._decode_revision(record, normalized_section_id, payload))
        return result

    def revision_history(self, section_id: str) -> list[SectionRevision]:
        """Alias retained for callers that expose history in their UI."""

        return self.list_revisions(section_id)

    def revise_plan(self, blueprint: ProjectBlueprint) -> PlanRevisionResult:
        """Save an edited blueprint as a new, restorable user plan revision."""

        return self._commit_plan(blueprint, source="user_override", restored_from_id=None)

    def restore_plan_revision(self, revision: RevisionRecord | int | str) -> PlanRevisionResult:
        """Restore a historical plan version as a new user plan revision."""

        target = self._find_plan_revision(revision)
        return self._commit_plan(target.blueprint, source="restore", restored_from_id=target.record.id)

    def restore_previous_plan_revision(self) -> PlanRevisionResult:
        """Restore the plan version immediately preceding the active version."""

        history = self.list_plan_revisions()
        if len(history) < 2:
            raise RuntimeError("The project plan has no previous revision")
        return self.restore_plan_revision(history[1].record)

    def list_plan_revisions(self) -> list[PlanRevision]:
        """Return immutable plan history from newest to oldest."""

        records = self.repository.list_plan_revisions(self.project_id)
        result: list[PlanRevision] = []
        for record in records:
            payload = self.repository.get_plan_revision_payload(self.project_id, record.id)
            if payload is None:
                raise RuntimeError(f"Missing payload for plan revision {record.id}")
            result.append(self._decode_plan_revision(record, payload))
        return result

    def plan_revision_history(self) -> list[PlanRevision]:
        """Alias for callers that use the explicit history vocabulary."""

        return self.list_plan_revisions()

    def invalidation_for(
        self,
        section_id: str,
        *,
        plan_edit: bool = False,
        replacement_blocks: Sequence[ManuscriptBlock] | None = None,
    ) -> SectionInvalidation:
        """Calculate the minimal safe pipeline suffix without mutating a run."""

        normalized_section_id = self._section_id(section_id)
        # ``plan_edit`` means the user has just persisted the authoritative
        # blueprint through this service.  Re-running PLAN would replace that
        # snapshot with a fresh model response, so start at the first consumer
        # of a blueprint instead.
        if plan_edit:
            start_stage = PipelineStage.GENERATE_SECTIONS
        elif replacement_blocks is not None and self._requires_visual_regeneration(
            replacement_blocks
        ):
            # A typed client may add a chart, diagram, or generated image.
            # Rendering it directly would produce a missing-artifact
            # placeholder, so regenerate only the visual-and-downstream
            # suffix.  Plain-text edits remain at citation QA.
            start_stage = PipelineStage.GENERATE_VISUALS
        else:
            start_stage = PipelineStage.CITATION_AUDIT
        start_index = PIPELINE_ORDER.index(start_stage)
        return SectionInvalidation(
            start_stage=start_stage,
            stages=PIPELINE_ORDER[start_index:],
            section_ids=self._affected_section_ids(normalized_section_id, plan_edit=plan_edit),
            plan_edit=plan_edit,
        )

    def plan_invalidation(
        self,
        blueprint: ProjectBlueprint | None = None,
        *,
        previous: ProjectBlueprint | None = None,
    ) -> SectionInvalidation:
        """Return the safe section-generation suffix for a saved plan revision.

        Callers that only have a blueprint retain the historic conservative
        behaviour and receive every section.  A saved revision supplies its
        preceding blueprint, which lets us regenerate only sections whose
        outline definition changed and the sections that depend on them.
        """

        effective_blueprint = blueprint or self.repository.get_latest_blueprint(self.project_id)
        if effective_blueprint is None:
            section_ids: tuple[str, ...] = ()
        elif previous is None:
            section_ids = self._ordered_section_ids(effective_blueprint)
        else:
            section_ids = self._changed_plan_section_ids(previous, effective_blueprint)
        start_index = PIPELINE_ORDER.index(PipelineStage.GENERATE_SECTIONS)
        return SectionInvalidation(
            start_stage=PipelineStage.GENERATE_SECTIONS,
            stages=PIPELINE_ORDER[start_index:],
            section_ids=section_ids,
            plan_edit=True,
        )

    def prepare_plan_rebuild(
        self,
        run_id: str,
        revision: PlanRevisionResult,
    ) -> GenerationRun:
        """Persist a targeted plan-rebuild selection for an existing run.

        This keeps UI code out of run storage.  The section-generation stage
        consumes these two metadata keys to preserve independent drafts and to
        distinguish new targeted drafts from artifacts of an earlier rebuild.
        The active blueprint check prevents a stale UI action from applying an
        old revision's targets after a newer plan revision has been saved.
        """

        normalized_run_id = run_id.strip()
        if not normalized_run_id:
            raise ValueError("run_id must not be blank")
        if revision.blueprint.project_id != self.project_id:
            raise ValueError("plan revision belongs to a different project")
        if (
            not revision.invalidation.plan_edit
            or revision.invalidation.start_stage is not PipelineStage.GENERATE_SECTIONS
        ):
            raise ValueError("plan revision does not describe a section-generation rebuild")

        active_blueprint = self.repository.get_latest_blueprint(self.project_id)
        if active_blueprint is None or active_blueprint.id != revision.blueprint.id:
            raise RuntimeError("plan revision is no longer the active project blueprint")

        section_ids = list(revision.invalidation.section_ids)
        if not section_ids:
            # Passing an empty selection makes older workers interpret the
            # request as a full regeneration, which is the opposite of a
            # no-op plan save.  Refuse it rather than losing reusable drafts.
            raise RuntimeError("plan revision does not change any sections to rebuild")
        known_section_ids = {section.id for section in revision.blueprint.outline.sections}
        if len(section_ids) != len(set(section_ids)) or set(section_ids) - known_section_ids:
            raise ValueError("plan rebuild selection contains unknown or duplicate section IDs")

        run = self.repository.get_run(normalized_run_id)
        if run is None:
            raise KeyError(normalized_run_id)
        if run.project_id != self.project_id:
            raise ValueError("run belongs to a different project")

        rebuild_token = uuid4().hex
        metadata_section_ids: list[JsonValue] = []
        for section_id in section_ids:
            metadata_section_ids.append(section_id)
        run.metadata["rebuild_section_ids"] = metadata_section_ids
        run.metadata["rebuild_section_token"] = rebuild_token
        persisted = self.repository.save_run_preserving_control(
            run,
            replace_metadata_keys={"rebuild_section_ids", "rebuild_section_token"},
        )
        if (
            persisted.metadata.get("rebuild_section_ids") != section_ids
            or persisted.metadata.get("rebuild_section_token") != rebuild_token
        ):
            raise RuntimeError("run changed while preparing the targeted plan rebuild")
        return persisted

    def _commit(
        self,
        section_id: str,
        blocks: Sequence[ManuscriptBlock] | str,
        *,
        source: Literal["user_override", "restore"],
        plan_edit: bool,
        restored_from_id: str | None,
    ) -> SectionRevisionResult:
        normalized_section_id = self._section_id(section_id)
        current = self.repository.get_latest_manuscript(self.project_id)
        if current is None:
            raise RuntimeError("A generated manuscript is required before revising a section")
        start, end = self._section_span(current, normalized_section_id)
        original_blocks = current.blocks[start:end]
        replacement = self._normalise_replacement(normalized_section_id, original_blocks[0], blocks)
        if source == "user_override":
            replacement = self._mark_user_authored_blocks(replacement)

        history = self.list_revisions(normalized_section_id)
        baseline_payload = (
            self._encode_payload(normalized_section_id, original_blocks, source="generated", restored_from_id=None)
            if not history
            else None
        )
        override_payload = self._encode_payload(
            normalized_section_id,
            replacement,
            source=source,
            restored_from_id=restored_from_id,
        )

        manuscript = current.model_copy(deep=True)
        manuscript.id = new_id()
        manuscript.revision = current.revision + 1
        manuscript.updated_at = utc_now()
        manuscript.blocks = [
            *deepcopy(current.blocks[:start]),
            *replacement,
            *deepcopy(current.blocks[end:]),
        ]
        record = self.repository.commit_section_override(
            manuscript,
            normalized_section_id,
            override_payload,
            baseline_payload=baseline_payload,
        )
        revision = self._decode_revision(record, normalized_section_id, override_payload)
        return SectionRevisionResult(
            manuscript=manuscript,
            revision=revision,
            invalidation=self.invalidation_for(
                normalized_section_id,
                plan_edit=plan_edit,
                replacement_blocks=replacement,
            ),
        )

    def _commit_plan(
        self,
        blueprint: ProjectBlueprint,
        *,
        source: Literal["user_override", "restore"],
        restored_from_id: str | None,
    ) -> PlanRevisionResult:
        if blueprint.project_id != self.project_id:
            raise ValueError("blueprint belongs to a different project")
        current = self.repository.get_latest_blueprint(self.project_id)
        if current is None:
            raise RuntimeError("A generated project blueprint is required before revising the plan")
        history = self.list_plan_revisions()
        baseline_payload = self._encode_plan_payload(current, source="generated", restored_from_id=None) if not history else None
        persisted = blueprint.model_copy(deep=True)
        persisted.id = new_id()
        persisted.created_at = utc_now()
        payload = self._encode_plan_payload(persisted, source=source, restored_from_id=restored_from_id)
        record = self.repository.commit_plan_override(
            persisted,
            payload,
            baseline_payload=baseline_payload,
            baseline_object_id=current.id,
        )
        revision = self._decode_plan_revision(record, payload)
        return PlanRevisionResult(
            blueprint=persisted,
            revision=revision,
            invalidation=self.plan_invalidation(persisted, previous=current),
        )

    @staticmethod
    def _section_id(section_id: str) -> str:
        normalized = section_id.strip()
        if not normalized:
            raise ValueError("section_id must not be blank")
        return normalized

    @staticmethod
    def _section_span(manuscript: Manuscript, section_id: str) -> tuple[int, int]:
        starts = [
            index
            for index, block in enumerate(manuscript.blocks)
            if isinstance(block, HeadingBlock) and block.section_id == section_id
        ]
        if not starts:
            raise KeyError(f"Unknown manuscript section: {section_id}")
        if len(starts) != 1:
            raise ValueError(f"Manuscript contains duplicate section headings for {section_id!r}")
        start = starts[0]
        end = len(manuscript.blocks)
        for index in range(start + 1, len(manuscript.blocks)):
            block = manuscript.blocks[index]
            if isinstance(block, HeadingBlock) and block.section_id is not None:
                end = index
                break
        return start, end

    @staticmethod
    def _normalise_replacement(
        section_id: str,
        original_heading: ManuscriptBlock,
        blocks: Sequence[ManuscriptBlock] | str,
    ) -> list[ManuscriptBlock]:
        if not isinstance(original_heading, HeadingBlock):  # Defensive: _section_span guarantees it.
            raise RuntimeError("section does not begin with a heading")
        if isinstance(blocks, str):
            replacement: list[ManuscriptBlock] = [ParagraphBlock(text=blocks)]
        else:
            replacement = deepcopy(list(blocks))

        if replacement and isinstance(replacement[0], HeadingBlock):
            heading = replacement[0]
            if heading.section_id not in {None, section_id}:
                raise ValueError("replacement heading belongs to a different section")
            replacement[0] = heading.model_copy(update={"section_id": section_id})
        else:
            replacement.insert(0, original_heading.model_copy(deep=True))

        for block in replacement[1:]:
            if isinstance(block, HeadingBlock) and block.section_id is not None:
                raise ValueError("replacement may not contain another identified section heading")
        return replacement

    @staticmethod
    def _mark_user_authored_blocks(
        blocks: Sequence[ManuscriptBlock],
    ) -> list[ManuscriptBlock]:
        """Make evidence/provenance review explicit for authored content.

        The simple UI editor supplies a string, not invisible claim bindings.
        Rather than silently treating that text as evidence-backed, preserve a
        durable marker.  Advanced callers can provide both claim and
        bibliography IDs in the paragraph metadata; citation audit verifies
        those bindings before the marker can pass release QA.  A numeric inline
        table is likewise marked so deterministic QA can require either a
        verified dataset or FactLedger identifiers, while purely textual tables
        remain valid user edits.
        """

        marked: list[ManuscriptBlock] = []
        for block in blocks:
            if isinstance(block, AppendixBlock):
                marked.append(
                    block.model_copy(
                        update={"blocks": SectionRevisionService._mark_user_authored_blocks(block.blocks)}
                    )
                )
                continue
            if isinstance(block, TableBlock):
                metadata = dict(block.metadata)
                metadata["user_override"] = True
                marked.append(block.model_copy(update={"metadata": metadata}))
                continue
            if not isinstance(block, ParagraphBlock):
                marked.append(block)
                continue
            metadata = dict(block.metadata)
            metadata["user_override"] = True
            raw_claim_ids = metadata.get("claim_ids")
            raw_entry_ids = metadata.get("bibliography_entry_ids")
            claim_ids = raw_claim_ids if isinstance(raw_claim_ids, list) else []
            entry_ids = raw_entry_ids if isinstance(raw_entry_ids, list) else []
            if not claim_ids or not entry_ids:
                metadata["evidence_review_required"] = True
            marked.append(block.model_copy(update={"metadata": metadata}))
        return marked

    @staticmethod
    def _requires_visual_regeneration(blocks: Sequence[ManuscriptBlock]) -> bool:
        """Return whether a replacement needs the local visual producer.

        Existing artifact-only figures are already renderable.  In contrast,
        charts, diagrams, and figures with an ``image_spec`` require the
        ``GENERATE_VISUALS`` stage to create or validate an artifact.  The
        check intentionally walks appendix contents too: they are rendered
        recursively and must not bypass the same dependency rule.
        """

        for block in blocks:
            if isinstance(block, (ChartBlock, DiagramBlock)):
                return True
            if isinstance(block, FigureBlock) and block.image_spec is not None:
                return True
            if isinstance(block, AppendixBlock) and SectionRevisionService._requires_visual_regeneration(
                block.blocks
            ):
                return True
        return False

    def _affected_section_ids(self, section_id: str, *, plan_edit: bool) -> tuple[str, ...]:
        if not plan_edit:
            return (section_id,)
        blueprint = self.repository.get_latest_blueprint(self.project_id)
        if blueprint is None:
            return (section_id,)
        sections = {section.id: section for section in blueprint.outline.sections}
        if section_id not in sections:
            return (section_id,)
        return self._downstream_section_ids(blueprint, {section_id})

    @staticmethod
    def _ordered_section_ids(blueprint: ProjectBlueprint) -> tuple[str, ...]:
        return tuple(
            section.id
            for section in sorted(blueprint.outline.sections, key=lambda item: item.order)
        )

    @classmethod
    def _changed_plan_section_ids(
        cls,
        previous: ProjectBlueprint,
        updated: ProjectBlueprint,
    ) -> tuple[str, ...]:
        """Find changed outline definitions and their new downstream closure."""

        updated_sections = {section.id: section for section in updated.outline.sections}
        previous_sections = {section.id: section for section in previous.outline.sections}
        if cls._plan_wide_inputs_changed(previous, updated):
            changed_ids = set(updated_sections)
        else:
            changed_ids = {
                section_id
                for section_id, section in updated_sections.items()
                if previous_sections.get(section_id) is None
                or section.model_dump(mode="json")
                != previous_sections[section_id].model_dump(mode="json")
            }
            # Removed sections cannot appear in the new selection, but their
            # former descendants can still need a fresh context.  Include the
            # shared portion of that old closure before expanding the new DAG.
            removed_ids = set(previous_sections) - set(updated_sections)
            changed_ids.update(
                section_id
                for section_id in cls._downstream_section_ids(previous, removed_ids)
                if section_id in updated_sections
            )
        return cls._downstream_section_ids(updated, changed_ids)

    @staticmethod
    def _plan_wide_inputs_changed(
        previous: ProjectBlueprint,
        updated: ProjectBlueprint,
    ) -> bool:
        """Detect plan fields whose change can affect every section prompt."""

        excluded = {"id", "created_at", "outline"}
        return previous.model_dump(mode="json", exclude=excluded) != updated.model_dump(
            mode="json", exclude=excluded
        )

    @staticmethod
    def _downstream_section_ids(
        blueprint: ProjectBlueprint,
        seed_ids: set[str],
    ) -> tuple[str, ...]:
        """Return a stable dependency closure in outline order."""

        sections = {section.id: section for section in blueprint.outline.sections}
        dependents: dict[str, set[str]] = {identifier: set() for identifier in sections}
        for section in sections.values():
            for dependency_id in section.depends_on:
                dependents[dependency_id].add(section.id)
        affected = set(seed_ids) & set(sections)
        pending: deque[str] = deque(affected)
        while pending:
            parent = pending.popleft()
            for child in dependents[parent]:
                if child not in affected:
                    affected.add(child)
                    pending.append(child)
        return tuple(
            section.id
            for section in sorted(sections.values(), key=lambda item: item.order)
            if section.id in affected
        )

    def _find_revision(self, section_id: str, reference: RevisionRecord | int | str) -> SectionRevision:
        for revision in self.list_revisions(section_id):
            if isinstance(reference, RevisionRecord) and revision.record.id == reference.id:
                return revision
            if isinstance(reference, int) and not isinstance(reference, bool) and revision.record.revision == reference:
                return revision
            if isinstance(reference, str) and revision.record.id == reference:
                return revision
        raise KeyError(f"Unknown revision for section {section_id!r}")

    def _find_plan_revision(self, reference: RevisionRecord | int | str) -> PlanRevision:
        for revision in self.list_plan_revisions():
            if isinstance(reference, RevisionRecord) and revision.record.id == reference.id:
                return revision
            if isinstance(reference, int) and not isinstance(reference, bool) and revision.record.revision == reference:
                return revision
            if isinstance(reference, str) and revision.record.id == reference:
                return revision
        raise KeyError("Unknown project plan revision")

    @staticmethod
    def _encode_payload(
        section_id: str,
        blocks: Sequence[ManuscriptBlock],
        *,
        source: _RevisionSource,
        restored_from_id: str | None,
    ) -> str:
        value = {
            "version": 1,
            "section_id": section_id,
            "source": source,
            "restored_from_id": restored_from_id,
            "blocks": [block.model_dump(mode="json") for block in blocks],
        }
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode_revision(record: RevisionRecord, section_id: str, payload: str) -> SectionRevision:
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(payload_hash, record.sha256):
            raise ValueError(f"Stored section revision {record.id} failed integrity verification")
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError(f"Stored section revision {record.id} is not valid JSON") from error
        if not isinstance(decoded, dict):
            raise ValueError(f"Stored section revision {record.id} is not an object")
        if decoded.get("version") != 1 or decoded.get("section_id") != section_id:
            raise ValueError(f"Stored section revision {record.id} does not match its section")
        source = decoded.get("source")
        if source not in {"generated", "user_override", "restore"}:
            raise ValueError(f"Stored section revision {record.id} has an unknown source")
        raw_blocks = decoded.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError(f"Stored section revision {record.id} has no block list")
        try:
            blocks = _SECTION_BLOCKS.validate_python(raw_blocks)
        except ValidationError as error:
            raise ValueError(f"Stored section revision {record.id} has invalid blocks") from error
        restored_from_id = decoded.get("restored_from_id")
        if restored_from_id is not None and not isinstance(restored_from_id, str):
            raise ValueError(f"Stored section revision {record.id} has an invalid restore reference")
        return SectionRevision(
            record=record,
            section_id=section_id,
            blocks=tuple(blocks),
            source=source,
            restored_from_id=restored_from_id,
        )

    @staticmethod
    def _encode_plan_payload(
        blueprint: ProjectBlueprint,
        *,
        source: _RevisionSource,
        restored_from_id: str | None,
    ) -> str:
        value = {
            "version": 1,
            "source": source,
            "restored_from_id": restored_from_id,
            "blueprint": blueprint.model_dump(mode="json"),
        }
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode_plan_revision(record: RevisionRecord, payload: str) -> PlanRevision:
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(payload_hash, record.sha256):
            raise ValueError(f"Stored plan revision {record.id} failed integrity verification")
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError(f"Stored plan revision {record.id} is not valid JSON") from error
        if not isinstance(decoded, dict) or decoded.get("version") != 1:
            raise ValueError(f"Stored plan revision {record.id} is invalid")
        source = decoded.get("source")
        if source not in {"generated", "user_override", "restore"}:
            raise ValueError(f"Stored plan revision {record.id} has an unknown source")
        restored_from_id = decoded.get("restored_from_id")
        if restored_from_id is not None and not isinstance(restored_from_id, str):
            raise ValueError(f"Stored plan revision {record.id} has an invalid restore reference")
        raw_blueprint = decoded.get("blueprint")
        if not isinstance(raw_blueprint, dict):
            raise ValueError(f"Stored plan revision {record.id} has no blueprint")
        try:
            blueprint = ProjectBlueprint.model_validate(raw_blueprint)
        except ValidationError as error:
            raise ValueError(f"Stored plan revision {record.id} has an invalid blueprint") from error
        if blueprint.project_id != record.project_id:
            raise ValueError(f"Stored plan revision {record.id} belongs to a different project")
        return PlanRevision(
            record=record,
            blueprint=blueprint,
            source=source,
            restored_from_id=restored_from_id,
        )


__all__ = [
    "PlanRevision",
    "PlanRevisionResult",
    "SectionInvalidation",
    "SectionRevision",
    "SectionRevisionResult",
    "SectionRevisionService",
]
