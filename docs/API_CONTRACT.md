# PaperCraft AI Studio — Application API Contract

Version: **Application API 1**

Worker protocol: **1**

Status: frozen for MVP modules 1–13

This document is the public boundary between the desktop UI, application layer, and
background worker. Persistent entities and release invariants are defined in
[DATA_MODEL.md](DATA_MODEL.md); delivery scope is defined in [PLAN.md](../PLAN.md).

## Compatibility and common rules

- The only public in-process entry point is `DesktopApplication`. The UI must not access
  SQLite, project files, credentials, provider clients, or pipeline services directly.
- DTOs are versioned with `api_version: 1`. Unknown input fields are rejected; readers may
  ignore unknown output fields added compatibly within version 1.
- IDs are non-empty opaque strings. Timestamps are RFC 3339 UTC strings. Filesystem inputs
  are absolute Windows paths; returned artifact paths are read-only application data.
- Optional patch fields distinguish omission from explicit `null`: omitted means “leave
  unchanged”; `null` clears only fields documented as nullable.
- Mutations are atomic from the caller's point of view. A failed mutation does not return a
  partially updated DTO.
- User-visible messages are safe to display. Secrets, prompts, document text, and provider
  payloads never appear in logs, command-line arguments, worker events, or exception text.
- All methods may raise `ValidationError`, `NotFoundError`, `ConflictError`, or `StorageError`.
  Credential methods may also raise `ProviderUnavailableError`; release access may raise
  `ReleaseNotReadyError`. Errors contain `code`, `safe_message`, and optional non-secret
  `details`.

## Shared enums

`WorkType`: `coursework | scientific_article | practice_report | school_project`.

`SubmissionStatus`:

- `DRAFT` — editable project without a current passing release;
- `RUNNING` — worker is actively processing the current run;
- `WAITING_PROVIDER` — a retryable provider condition has a scheduled retry;
- `WAITING_INPUT` — safe continuation requires user data or a decision;
- `QUALITY_FAILED` — generation completed but release policy failed;
- `READY_TO_SUBMIT` — the current immutable release passed release policy 1;
- `SUPERSEDED` — a previously ready release was invalidated by a newer revision;
- `CANCELLED` — the latest run was cancelled and no current ready release exists.

`SourceRole`: `methodology | example | template | source_data | codebase | image |
reference | unknown`.

Sort values are stable strings: `updated_desc | updated_asc | created_desc | title_asc`.

## Credentials

```text
credential_status() -> CredentialStatus
configure_gemini(api_key: secret string) -> CredentialStatus
verify_gemini() -> ProviderCheck
delete_gemini_key() -> None
```

`CredentialStatus` fields: `api_version`, `configured: bool`, `verified: bool`,
`state: missing | valid | unverified | invalid`, `last_checked_at: timestamp | null`, and
`safe_message: string`. `configured=false` implies `state=missing` and `verified=false`.
The key is accepted only as an input and is never returned.

`ProviderCheck` fields: `api_version`, `provider="gemini"`, `ok: bool`,
`state: valid | invalid | unavailable`, `checked_at`, `retryable: bool`, and `safe_message`.

## Projects

```text
list_projects(filter: ProjectFilter | null, sort: ProjectSort) -> list[ProjectSummary]
get_project(project_id: str) -> ProjectWorkspaceView
create_project(draft: ProjectDraft) -> ProjectView
update_project(project_id: str, patch: ProjectPatch) -> ProjectView
archive_project(project_id: str) -> None
restore_project(project_id: str) -> ProjectView
```

`ProjectFilter`: `query: string | null`, `statuses: list[SubmissionStatus]`,
`work_types: list[WorkType]`, and `archived: bool=false`.

`ProjectDraft`:

- required: `topic: string`, `work_type: WorkType`;
- optional: `title`, `instructions`, `language="ru-RU"`, `profile_id`,
  `title_page: object`, `requirements_text`, and `options: AutopilotOptions`;
- `AutopilotOptions`: `maximum_cost: decimal string | null`, `currency="USD"`,
  `quality_mode: maximum | balanced | economy`, `maximum_revision_cycles: 1..10`,
  `allow_synthetic_data=false`, and `consent_to_remote_processing=false`.

`ProjectPatch` contains any mutable `ProjectDraft` field. `work_type` or `profile_id`
changes select a new profile. Changes affecting source, requirements, profile, outline,
manuscript, or model policy supersede the current release.

`ProjectView`: `api_version`, `id`, all normalized draft fields, `status`, `profile_id`,
`content_revision: integer >= 1`, `archived_at: timestamp | null`, `created_at`, `updated_at`,
and `current_release_id: string | null`.

`ProjectSummary`: `api_version`, `id`, `title`, `topic`, `work_type`, `status`, `updated_at`,
`archived: bool`, `progress: 0..1`, and `current_release_id`.

`ProjectWorkspaceView`: `api_version`, `project: ProjectView`, `sources: list[SourceView]`,
`active_run: RunSnapshot | null`, and `submission: SubmissionResult`.

Archiving hides a project from the default list and does not delete local data. Restoring is
idempotent. Mutating an archived project raises `ConflictError` until it is restored.

## Sources

```text
import_source(project_id: str, source: SourceInput) -> SourceView
remove_source(project_id: str, source_id: str) -> None
reclassify_source(project_id: str, source_id: str, role: SourceRole) -> SourceView
list_sources(project_id: str) -> list[SourceView]
```

`SourceInput`: `kind: file | url | doi | table | code_directory`, `location: string`,
`role: SourceRole | null`, `display_name: string | null`, and `metadata: object={}`.
`location` is an absolute local path for `file`, `table`, and `code_directory`, a HTTPS URL
for `url`, and a normalized DOI for `doi`. Imports copy or snapshot input into the project;
later changes to the external location do not silently alter the stored source.

`SourceView`: `api_version`, `id`, `project_id`, `kind`, `role`, `display_name`, `mime_type`,
`size_bytes`, `sha256`, `origin`, `classification_confidence: 0..1 | null`, `state:
importing | ready | failed`, `safe_message`, and `created_at`. Removing or reclassifying a
source supersedes the current release. Removing an unknown source is idempotent.

Imported `codebase` files are inspected only through static parsers. Their internal analysis
records retain the immutable source hash and line locators; the application never imports or
executes attached code. Runtime claims require an independently attached log or test report.

## Generation

```text
start_generation(project_id: str) -> RunSnapshot
pause_generation(run_id: str) -> RunSnapshot
resume_generation(run_id: str) -> RunSnapshot
cancel_generation(run_id: str) -> RunSnapshot
retry_generation(run_id: str) -> RunSnapshot
get_run_snapshot(run_id: str) -> RunSnapshot
```

`RunSnapshot`: `api_version`, `id`, `project_id`, `status: SubmissionStatus`, `stage: string |
null`, `progress: 0..1`, `message`, `retry_at: timestamp | null`, `estimated_cost: Money |
null`, `actual_cost: Money`, `started_at: timestamp | null`, `finished_at: timestamp | null`,
`error_code: string | null`, and `can_pause`, `can_resume`, `can_cancel`, `can_retry` booleans.
`Money` is `{amount: non-negative decimal string, currency: three uppercase letters}`.

Only one non-terminal run is admitted per project. Control methods are idempotent for the
same target state. `retry_generation` creates or resumes work from the last safe durable
checkpoint without reusing a stale release. Provider backoff maps to `WAITING_PROVIDER`;
missing factual input maps to `WAITING_INPUT`.

## Editing, release, and backup

```text
get_submission_result(project_id: str) -> SubmissionResult
update_text_section(section_id: str, text: str) -> SectionRevision
regenerate_section(section_id: str, instruction: str) -> RunSnapshot
export_ready_docx(release_id: str, destination: absolute path) -> ExportResult
create_backup(project_id: str, destination: absolute path) -> BackupResult
restore_backup(archive: absolute path) -> ProjectView
```

`SubmissionResult`: `api_version`, `project_id`, `status`, `release_id: string | null`,
`docx_artifact_id: string | null`, `preview_page_ids: list[string]`, `qa_report_id: string |
null`, `ready_at: timestamp | null`, and `safe_message`. Artifact presence alone never sets
`READY_TO_SUBMIT`.

`SectionRevision`: `api_version`, `id`, `project_id`, `section_id`, `revision >= 1`,
`content_hash`, `created_at`, `release_superseded: bool`. Empty text is rejected.

`ExportResult`: `api_version`, `release_id`, `destination`, `sha256`, `size_bytes`, and
`exported_at`. Export and any “open document” action must use the same current-release gate:
the release is `READY_TO_SUBMIT`, belongs to the project, and its scope hashes still match.

`BackupResult`: `api_version`, `project_id`, `archive`, `sha256`, `size_bytes`, and
`created_at`. Restore verifies the archive and hash before replacing or creating project data;
on ID collision it raises `ConflictError` without changing either project.

## JSONL worker protocol v1

Transport is UTF-8 JSON Lines. The desktop writes exactly one `WorkerRequest` line to stdin;
stdout contains only `WorkerEvent` lines; diagnostics go to stderr. Every line is one JSON
object and is flushed immediately. Malformed or unsupported input exits non-zero after a safe
`request_failed` event when a request ID can be recovered.

`WorkerRequest` always contains these keys (nullable where stated):

```text
protocol_version: 1
request_id: non-empty opaque string
action: start_generation | pause_generation | resume_generation |
        cancel_generation | retry_generation | regenerate_section
project_id: non-empty string
run_id: string | null
stage_id: string | null
section_id: string | null
```

`run_id` is null only for `start_generation`; `section_id` is required only for
`regenerate_section`; `stage_id` may constrain retry to a failed stage and is otherwise null.
The worker persists the first terminal outcome by `request_id`. Repeating an identical request
replays that outcome; reusing the ID with different fields emits `REQUEST_ID_CONFLICT`.

`WorkerEvent` always contains these keys:

```text
protocol_version, request_id, project_id, run_id, sequence, timestamp,
event_type, stage, status, progress, message, error_code, retry_at, estimated_cost
```

- `protocol_version` is `1`; `run_id`, `stage`, `status`, `error_code`, `retry_at`, and
  `estimated_cost` may be null only before they are known or when not applicable.
- `sequence` is a positive, strictly increasing integer within a run and is persisted before
  emission. Duplicate replay preserves original sequence values.
- `event_type` is `request_accepted | progress | run_state | request_finished |
  request_failed | heartbeat`; `progress` is a number from 0 to 1; `estimated_cost` is `Money`.
- `message` is safe user-facing text. `request_failed` supplies a stable `error_code` and never
  carries a traceback. A broken stdout line is a protocol error, never an ignorable log line.
- Worker termination does not imply run failure. On restart, the application reconciles the
  durable run/stage state and continues only from an idempotent checkpoint.

## Contract change policy

Breaking changes require a new integer version plus updates to this file,
[DATA_MODEL.md](DATA_MODEL.md), and [PROJECT_STATE.md](../PROJECT_STATE.md) before code changes.
Compatible output-only additions may retain version 1 when existing meanings and required
fields do not change.
