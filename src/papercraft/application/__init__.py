"""Application services and the resumable autopilot orchestrator."""

from .autopilot import (
    AutopilotService,
    PipelineStage,
    RunQuery,
    StageContext,
    StageOutcome,
)
from .context import ContextBuilder, SectionContext
from .documents import DocumentExportBlocked, DocumentService
from .projects import ProjectService, ProjectWorkspace, SourceService
from .requirements import (
    RequirementConflictError,
    RequirementResolver,
    build_requirement_coverage_report,
    coverage_for_rules,
)
from .revisions import (
    PlanRevision,
    PlanRevisionResult,
    SectionInvalidation,
    SectionRevision,
    SectionRevisionResult,
    SectionRevisionService,
)
from .runtime import AutopilotRuntime, prepare_autopilot
from .stages import ProductionStageFactory, StageExecutionError
from .usage import CostLimitExceeded, RunUsageTracker

__all__ = [
    "AutopilotRuntime",
    "AutopilotService",
    "ContextBuilder",
    "CostLimitExceeded",
    "DocumentExportBlocked",
    "DocumentService",
    "PipelineStage",
    "PlanRevision",
    "PlanRevisionResult",
    "ProductionStageFactory",
    "ProjectService",
    "ProjectWorkspace",
    "RequirementConflictError",
    "RequirementResolver",
    "RunQuery",
    "RunUsageTracker",
    "SectionContext",
    "SectionInvalidation",
    "SectionRevision",
    "SectionRevisionResult",
    "SectionRevisionService",
    "SourceService",
    "StageContext",
    "StageExecutionError",
    "StageOutcome",
    "build_requirement_coverage_report",
    "coverage_for_rules",
    "prepare_autopilot",
]
