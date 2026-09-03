# PaperCraft AI Studio — Data Model

Version: **database schema 5**, **profile schema 1**, **release policy 1**, **settings format 1**

Status: target persistence contract for MVP modules 1–13

This document owns persistent entities, storage, relations, and lifecycles. Public DTOs and
worker messages are defined in [API_CONTRACT.md](API_CONTRACT.md); scope is defined in
[PLAN.md](../PLAN.md).

## Conventions

- IDs are opaque non-empty strings; timestamps are UTC; hashes are lowercase SHA-256 hex;
  monetary values are decimal strings plus a three-letter currency.
- JSON objects use UTF-8, reject unknown fields on write, and retain their schema version.
- Mutable rows carry `updated_at`; immutable revisions are appended, not overwritten.
- A database transaction commits metadata only after referenced files have been written and
  hashed. File deletion happens after commit and is retryable.
- All project-owned foreign keys cascade on project deletion. Normal UI archive is soft and
  never deletes project data.

## Local storage

The default root is `%LOCALAPPDATA%/PaperCraftAI/projects`. Each safe project ID owns:

```text
<project_id>/
  project.db              SQLite schema 5, WAL mode, foreign_keys=ON
  inputs/originals/       immutable imported files and downloaded snapshots
  derived/                extracted text, indexes, and reproducible datasets
  runs/                   checkpoints and worker lease data
  artifacts/              manuscripts, charts, previews, QA, DOCX, internal PDF
  backups/                verified local backup archives
```

Only paths relative to the project root are persisted. External export and backup destinations
are returned as operation results, not stored as authoritative artifacts. Gemini credentials
live in Windows Credential Manager and never in project databases, settings files, or backups.

## Aggregate map

```text
Project
├─ Source ── SourceSnapshot / SourceFragment
├─ RequirementSet revision ── RequirementRule / Conflict
├─ ProjectBlueprint revision ── SectionSpec
├─ Dataset / FactLedger / Claim / Evidence / Calculation
├─ Manuscript revision ── SectionRevision
├─ GenerationRun ── StageRun ── RunEvent
│  ├─ Artifact
│  └─ QAReport ── QAIssue
└─ SubmissionRelease ── DOCX Artifact + QAReport + immutable release scope
```

## Core entities

Fields marked `?` are nullable. `metadata` is non-authoritative extension data and must not
override named fields.

### Project

`id`, `title`, `topic`, `instructions`, `work_type`, `language`, `title_page`, `profile_id`,
`options`, `submission_status`, `content_revision >= 1`, `current_release_id?`, `archived_at?`,
`created_at`, `updated_at`, `metadata`.

`options` contains cost/currency, quality mode, revision limit, synthetic-data permission, and
remote-processing consent. New projects start `DRAFT`. Any release-affecting mutation increments
`content_revision` and supersedes `current_release_id` in the same transaction.

### Source, SourceSnapshot, SourceFragment

- `Source`: `id`, `project_id`, `kind`, `role`, `original_name`, `stored_path`, `sha256`,
  `mime_type`, `size_bytes`, `classification_confidence?`, `state`, `origin`, `created_at`,
  `metadata`.
- `SourceSnapshot`: `id`, `project_id`, `source_id`, canonical/final URL, `stored_path`,
  `sha256`, content metadata, title/authors/organization, publication date, DOI/ISBN,
  `accessed_at`, and `locator`.
- `SourceFragment`: `id`, `source_id`, `content`, `locator`, `sha256`, `token_count?`, metadata.

Originals and snapshots are immutable. Reclassification changes only the source role but still
invalidates requirements and release scope. Removal cascades to fragments/snapshots and makes
dependent claims unsupported until rebuilt.

### Static code analysis

`CodeFileAnalysis` is a derived, typed payload retained in the source ingestion metadata and
fragment metadata for a `codebase` source. It records `source_id`, original relative file name,
exact SHA-256, language, parser, confidence, symbols, imports/dependencies, entrypoints, tests,
endpoints, and findings. Every result has a `Locator` with the original file and inclusive line
range plus the source hash. Python uses AST; JavaScript, TypeScript, Java, C, C++, and C# use
pinned Tree-sitter grammars. Unsupported or syntactically invalid files retain a fallback result
with reduced confidence. These records are static only: runtime behaviour is evidence only when
an imported log or test report independently supports it.

### RequirementSet and ProjectBlueprint

- `RequirementSet`: `id`, `project_id`, `revision`, `rules`, `conflicts`, `created_at`,
  `schema_version=1`. Each rule has category, key, statement, typed JSON value, mandatory flag,
  provenance, confidence, and metadata. Every conflict records all rule IDs and explicit
  resolution.
- `ProjectBlueprint`: `id`, `project_id`, `revision`, topic, goal, tasks, object, subject,
  hypothesis, methods, glossary, target words/pages, outline, required claims, planned visuals,
  `created_at`. Outline section IDs are unique and dependencies form a DAG.

Revisions are immutable. Only the latest selected revision is current. Replacing either entity
supersedes the current release and invalidates downstream stage hashes.

### Evidence and calculations

`Claim`, `Evidence`, `BibliographyEntry`, `Citation`, `Dataset`, `FactLedger`, `Calculation`,
`TableSpec`, `ChartSpec`, `DiagramSpec`, `FormulaSpec`, and `ImageSpec` are stored as typed
`domain_objects` with `kind`, `id`, `project_id`, optional `parent_id`, JSON payload, and
`updated_at`.

`ChartSpec` pins the Dataset ID, selected columns, labels, caption and alt text; every rendered
chart artifact also carries an accessible source-value table. `DiagramSpec` uses bounded typed
nodes and directed edges (legacy Mermaid/Graphviz source is read-only compatibility input) and
may render only sanitized SVG or PNG. `ImageSpec` carries the local prompt, caption, alt text and
optional flag. Image artifacts record the pinned model, prompt hash, output SHA-256 and generation
attempt. AI images are illustrations only and never create or support factual claims.

Every factual claim links to evidence and a stable locator. Every numeric manuscript value links
to a fact ledger entry whose origin is `user | verified_source | calculated | synthetic`.
Synthetic datasets store seed, algorithm/version, assumptions, and disclosure text; they never
masquerade as observations. A scientific empirical article without real observations changes to
a theoretical/review format or enters `WAITING_INPUT`.

### Manuscript and revisions

`Manuscript`: `id`, `project_id`, `revision`, title, ordered typed blocks, bibliography,
claim bindings, numeric fact bindings, quality status, `created_at`, `updated_at`, metadata.
Blocks have stable IDs and include paragraphs, headings, tables, charts, diagrams, formulas,
code listings, figures, citations, page breaks, and appendices.

`SectionRevision`: `id`, `project_id`, `section_id`, `revision`, before/after content hashes,
payload, instruction/source (`user | regeneration`), `created_at`. Plan revisions use the same
append-only rule. A text or plan revision supersedes a ready release immediately.

## Execution entities

### GenerationRun

`id`, `project_id`, internal `status`, `pipeline_version`, pinned model policy, current stage,
`input_hash`, actual cost/currency, timestamps, safe error, and metadata. Internal status is
`queued | running | retrying | paused | waiting_input | succeeded | failed | cancelled` and is
mapped to the public submission status by the Application API.

A project has at most one non-terminal run. `succeeded` means the pipeline finished; under
release policy 1 it may be set only in the same completion path that creates a current
`READY_TO_SUBMIT` release. A quality rejection ends as `failed` plus public `QUALITY_FAILED`.

### StageRun and RunEvent

- `StageRun`: `id`, `run_id`, name/order, status, attempts, input/dependency/output hashes,
  heartbeat, progress current/total, failure code/details, remote resource IDs, output artifact
  IDs, cost, timestamps, safe error, and durable checkpoint.
- `RunEvent`: `id`, `run_id`, `stage_id?`, monotonically increasing `sequence`, event type,
  safe message, timestamp, and non-secret data.

Stage writes use a stable `(run_id, stage name, item id)` idempotency key. Stale `running` stages
are recovered to a resumable state after lease expiry; completed output is reused only when all
input and dependency hashes match.

### Artifact and remote resources

`Artifact`: `id`, `project_id`, `run_id?`, `stage_id?`, kind, relative path, SHA-256, MIME type,
size, `created_at`, metadata. Supported kinds include source copy, extracted text, requirements,
blueprint, dataset, image/chart/diagram, manuscript, DOCX, internal PDF, page preview, and QA.

DOCX artifacts carry `phase: draft | repair_draft | final`. Drafts are immutable inputs to
LibreOffice finalization; only a `final` DOCX with `finalizer=libreoffice` and
`fields_updated=true` may be referenced by a release. PDF artifacts are internal release-QA
inputs (`user_exportable=false`) and are never a user export format.

`RemoteResource`: `id`, `project_id`, `run_id`, `stage_id?`, provider, remote ID/URI, local hash,
MIME type, `created_at`, `deleted_at?`, cleanup state. Provider resources are registered before
use and deletion is retried until durable `deleted_at` is set.

### QAReport

`QAReport`: `id`, `project_id`, `run_id`, `status: PASS | WARNING | FAIL`, issues, metrics,
requirement coverage, summary, `release_scope`, and `created_at`.

Release QA records the exact `input_hash`, canonical manuscript hash, final DOCX hash, and
internal PDF hash. A missing or mismatched input/manuscript/DOCX hash blocks release creation.

Each `QAIssue` has ID, severity (`info | warning | error | critical | blocker`), category,
message, optional requirement/artifact/locator, auto-fixable flag, resolved flag/resolution, and
metadata. Status is derived from unresolved issues: warning produces `WARNING`; error, critical,
or blocker produces `FAIL`. The report is immutable once referenced by a release.

## SubmissionRelease and release policy 1

`SubmissionRelease` is the sole durable proof that a project is ready. Fields:

- identity: `id`, `project_id`, `run_id`, `created_at`;
- exact outputs: `manuscript_id`, `manuscript_revision`, `docx_artifact_id`, `qa_report_id`;
- immutable scope: `project_content_revision`, `input_hash`, `requirements_revision`,
  `blueprint_revision`, `profile_id`, `profile_version`, `model_policy_hash`,
  `manuscript_hash`, `docx_hash`, `qa_scope_hash`;
- state: `status: READY_TO_SUBMIT | SUPERSEDED`, `superseded_at?`, `superseded_reason?`.

A release is created directly as `READY_TO_SUBMIT` only when all conditions hold atomically:

1. the final model review explicitly contains `accepted=true`; absent or false is rejection;
2. the review has no factual issues and all repair loops completed successfully;
3. deterministic QA status is exactly `PASS`, every mandatory requirement is satisfied, the
   active profile's `policy.minimum_sources` is met, and no unresolved warning-or-higher issue
   exists;
4. the final DOCX exists, hashes correctly, and was produced by the same run and manuscript;
5. every release-scope revision and hash equals the current project state.

DOCX existence, pipeline completion, or a model-generated “accepted” phrase outside the typed
field never implies readiness. Export, preview-to-open, and external-open actions all require the
same current release check. Changes to sources, requirements, profile, outline, manuscript,
section text, or model policy set the release to `SUPERSEDED`, clear `current_release_id`, and
return the project to `DRAFT` in one transaction. Releases are never edited back to ready.

## SQLite schema 5

Schema 5 retains the normalized indexes plus JSON payload approach of schema 4 and adds the
release boundary. Required tables:

`projects`, `sources`, `source_snapshots`, `fragments`, `requirements`, `blueprints`,
`manuscripts`, `runs`, `stages`, `artifacts`, `qa_reports`, `run_events`, `domain_objects`,
`remote_resources`, `backup_records`, `migration_records`, `revisions`,
`section_revision_payloads`, `plan_revision_payloads`, `worker_requests`, and
`submission_releases`.

Required uniqueness/integrity constraints include `(project_id, revision)` for revisioned
objects, `(run_id, name)` for stages, `(run_id, sequence)` for events, `request_id` for worker
requests, and at most one ready release per project. Foreign keys are enabled, journal mode is
WAL, busy timeout is 30 seconds, synchronous mode is NORMAL, and `PRAGMA user_version=5`.

Migration 4→5 must create missing v5 tables/indexes and preserve existing rows. Existing DOCX
and QA data do not receive a fabricated ready release; affected projects open as `DRAFT` and
must pass release QA. A verified backup precedes migration; migration failure leaves schema 4
and its files usable.

## Profile schema 1

`WorkProfile`: `schema_version=1`, `id`, `version`, display name, work type, domain tags,
description, ordered sections, policy, and prompt rules. A section has key/title/level/target
words/purpose/required. Policy has voice, required artifacts, source priorities,
`allow_synthetic_data`, fixed disclosure mode, real-organisation requirement,
`minimum_sources`, and section tolerance. Profile ID/version are pinned into each run and release.

## Settings format 1

Settings contain `format_version=1`, project root, log level, model/thinking/retry/performance/
pricing policies, request timeout, minimum disk space, and remote-file consent policy. Model IDs
and thinking levels are explicit per role. Unknown keys are rejected; missing compatible keys
receive documented defaults. Secrets and per-project content are forbidden.

## Backup and deletion lifecycle

A backup contains the database plus referenced project files, a manifest of relative paths,
sizes and hashes, application/build versions, and schema version. Restore verifies the complete
manifest before mutation. Database migrations create a verified automatic backup record.

Project archive is reversible. Permanent deletion is outside the MVP UI; if invoked by a future
maintenance tool it must first stop workers, attempt remote cleanup, remove the validated single
project root, and record failures without broad filesystem operations.

## Version change policy

A breaking field, invariant, storage, or lifecycle change increments the owning integer version
and is recorded in [PROJECT_STATE.md](../PROJECT_STATE.md) before implementation. Public API
changes also update [API_CONTRACT.md](API_CONTRACT.md).
