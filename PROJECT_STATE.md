# PaperCraft AI Studio Project State

## Current snapshot
- Version: 0.2.0
- Current module: 5 — Static source-code analysis (`completed`)
- Last updated: 2026-09-03
- Application status: verified scholarly discovery retains explicit evidence lineage; imported codebases now receive static-only, hash-bound analysis with exact source locators
- Working tree: module 0 documents remain untracked; modules 1–5 have scoped production/test changes, reviewed with scoped `git diff --check`
- Known blockers: `uv` remains unavailable, so `uv lock --check` could not run; a full paid Gemini scientific-article run was not performed

## Fixed decisions
- Windows 10/11 x64 desktop
- Python 3.12–3.13 + PySide6
- Local per-project SQLite/WAL
- Gemini key in Windows Credential Manager
- One-click autopilot without plan approval
- DOCX-only user export
- Bundled LibreOffice for preview and QA
- Manual unsigned installer updates
- Work types: coursework, scientific article, practice report, school project
- Static code analysis only
- Accounting standard: RAS/РСБУ
- Internal originality checks
- Section editor + page preview

## Usage budget
- Window started: 2026-09-03 00:45:08 +03:00
- Initial observed used percent: 2% (rounded account meter, first reading during module 4)
- Planned cumulative percent: 35%
- Actual used percent: unavailable (Usage endpoint did not respond before completion; last observed value was 18% after module 4)
- Remaining reserve: last observed 82%
- Budget status: on_track (last observed; a fresh Usage reading is required at module 6 checkpoint)

## Module status
| № | Module | Status | Model | Effort | Budget | Tests | Notes |
|---:|---|---|---|---|---:|---|---|
| 0 | Documents and contracts | completed | GPT-5.6 Sol | high | 5% | contract checks | Actual delta ≈7 pp; API 1 / worker 1 / DB 5 frozen |
| 1 | Release model and critical QA | completed | GPT-5.6 Sol | high | 9% | 56 targeted + Ruff/mypy | Actual delta ≈58 pp; fail-closed release policy 1 |
| 2 | Application facade and worker | completed | GPT-5.6 Terra | medium | 8% | 13 targeted + Ruff/mypy | Actual delta ≈13 pp; medium effort applied after prior overrun |
| 3 | Gemini and Credential Manager | completed | GPT-5.6 Terra | medium | 6% | 59 targeted + Ruff/mypy | Actual delta ≈10 pp; Credential Manager-only policy |
| 4 | Sources and scholarly APIs | completed | GPT-5.6 Terra | high | 7% | 30 targeted + Ruff/mypy | Actual delta ≈16 pp; verified publication snapshots and fail-closed citation lineage |
| 5 | Static source-code analysis | completed | GPT-5.6 Terra | high | 7% | 29 targeted + Ruff/mypy | AST/Tree-sitter locators, no code execution |
| 6 | RAS finance and modelled data | pending | GPT-5.6 Sol | high | 11% | targeted finance/data | Requires 0–5 |
| 7 | Profiles and automatic writing | pending | GPT-5.6 Terra | high | 7% | targeted profiles/writing | Requires 0–6 |
| 8 | Charts, diagrams, and images | pending | GPT-5.6 Terra | medium | 6% | targeted visuals | Requires 0–7 |
| 9 | DOCX, LibreOffice, release QA | pending | GPT-5.6 Sol | high | 11% | targeted render/release | Requires 0–8 |
| 10 | New UI shell | pending | GPT-5.6 Terra | high | 7% | targeted UI | Requires 0–9 |
| 11 | User workflows and editor | pending | GPT-5.6 Terra | high | 8% | targeted UI/application | Requires 0–10 |
| 12 | Windows installer | pending | GPT-5.6 Luna | medium | 3% | targeted packaging | Requires 0–11 |
| 13 | Final integration and MVP release | pending | GPT-5.6 Sol | high | 5% | release gates | Requires 0–12 |

Statuses: pending / in_progress / completed / blocked

## Current interfaces
- Application API version: 1 — [contract](docs/API_CONTRACT.md)
- Worker protocol version: 1 — [contract](docs/API_CONTRACT.md)
- Database schema version: 5 — [model](docs/DATA_MODEL.md)
- Profile schema version: 1 — [model](docs/DATA_MODEL.md)
- Release policy version: 1 — [model](docs/DATA_MODEL.md)
- Settings format version: 1 — [model](docs/DATA_MODEL.md)
- Build version: 0.2.0

## Last completed module
- Module: 5 — Static source-code analysis
- Result: codebase imports are parsed without execution: Python through AST and JS/TS/Java/C/C++/C# through pinned Tree-sitter grammars. Findings, symbols, dependencies, entrypoints, tests, and endpoints retain exact line locators and immutable source hashes.
- Files changed: `pyproject.toml`, `docs/{API_CONTRACT.md,DATA_MODEL.md}`, `src/papercraft/{domain/{__init__.py,code.py},infrastructure/{code_analysis.py,ingest/parsers.py}}`, `tests_v2/test_static_code_analysis.py`, and `PROJECT_STATE.md`.
- Decisions: analysis remains an internal extension of the existing `code_directory` ingestion boundary, so Application API 1 and schema 5 do not change. Parser fallback is explicit and reduced-confidence; embedded-secret values are redacted and instruction-like comments are ignored.
- Tests: 29 targeted ingest/code tests passed; Ruff and strict mypy passed for module files; scoped `git diff --check` passed. The corpus covers each requested Tree-sitter language plus Python AST, syntax errors, invalid locators, missing symbols, fake-secret detection, and prompt-injection comments.
- Known limitations: `uv` is unavailable, so `uv.lock` was not regenerated or checked; Tree-sitter dependencies were verified with Python 3.13 imports. No full suite, live provider call, or DOCX manual navigation test was run under this module's rule. Usage endpoint timed out, so post-module account consumption is unavailable.

## Next module
- Module: 6 — RAS finance and modelled data
- Required inputs: [API contract](docs/API_CONTRACT.md), [data model](docs/DATA_MODEL.md), and completed modules 0–5
- Entry conditions: modules 1, 2, 4, and 5 completed; Usage checkpoint available
- Expected result: reproducible RAS calculations and disclosed deterministic modelled datasets

## Project history
- 2026-09-02 — Module 0 completed: Application API 1, worker protocol 1, database schema 5, and release policy 1 frozen without production-code changes.
- 2026-09-02 — Module 1 completed: immutable releases, atomic READY/SUCCEEDED, strict QA/review gates, edit invalidation, and guarded DOCX access implemented.
- 2026-09-02 — Module 2 completed: `DesktopApplication` facade, JSONL worker v1, durable request-id replay/conflict handling, and compatibility worker CLI implemented.
- 2026-09-02 — Module 3 completed: Credential Manager-only Gemini lifecycle, safe verification/status DTOs, and capability-bound fallback policy implemented.
- 2026-09-03 — Module 4 completed: scholarly discovery, immutable publication snapshots, fail-closed citation lineage, and bilingual scientific-article metadata implemented.
- 2026-09-03 — Module 5 completed: static AST/Tree-sitter code analysis with immutable source hashes and exact locators implemented.
