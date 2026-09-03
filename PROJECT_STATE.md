# PaperCraft AI Studio Project State

## Current snapshot
- Version: 0.2.0
- Current module: 8 — Charts, diagrams, and images (`completed`)
- Last updated: 2026-09-03
- Application status: evidence-bound manuscripts now produce reproducible local chart/diagram assets and verified Gemini illustrations with accessible metadata
- Working tree: modules 0–5 and the initial module 6 implementation are consolidated in commit `80a1fc9`; scoped module 7–8 changes are uncommitted and reviewed with `git diff --check`
- Known blockers: none for module 8; `uv` remains unavailable, but the installed Python 3.13 environment provides Matplotlib and Pillow

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
- Planned cumulative percent: 66%
- Actual used percent: 47% (rounded account meter; module 8 observed from 37% to 47%)
- Remaining reserve: 53%
- Budget status: on_track (module 8 delta ≈10 pp versus 6 pp allocation; cumulative observed use remains below the 66% plan checkpoint)

## Module status
| № | Module | Status | Model | Effort | Budget | Tests | Notes |
|---:|---|---|---|---|---:|---|---|
| 0 | Documents and contracts | completed | GPT-5.6 Sol | high | 5% | contract checks | Actual delta ≈7 pp; API 1 / worker 1 / DB 5 frozen |
| 1 | Release model and critical QA | completed | GPT-5.6 Sol | high | 9% | 56 targeted + Ruff/mypy | Actual delta ≈58 pp; fail-closed release policy 1 |
| 2 | Application facade and worker | completed | GPT-5.6 Terra | medium | 8% | 13 targeted + Ruff/mypy | Actual delta ≈13 pp; medium effort applied after prior overrun |
| 3 | Gemini and Credential Manager | completed | GPT-5.6 Terra | medium | 6% | 59 targeted + Ruff/mypy | Actual delta ≈10 pp; Credential Manager-only policy |
| 4 | Sources and scholarly APIs | completed | GPT-5.6 Terra | high | 7% | 30 targeted + Ruff/mypy | Actual delta ≈16 pp; verified publication snapshots and fail-closed citation lineage |
| 5 | Static source-code analysis | completed | GPT-5.6 Terra | high | 7% | 29 targeted + Ruff/mypy | AST/Tree-sitter locators, no code execution |
| 6 | RAS finance and modelled data | completed | GPT-5.6 Sol | high | 11% | 21 targeted + Ruff/mypy | Decimal calculations, RAS catalog and disclosed seeded data |
| 7 | Profiles and automatic writing | completed | GPT-5.6 Terra | high | 7% | 10 targeted + Ruff/mypy | Four active profiles, strict blocks, no approval pause |
| 8 | Charts, diagrams, and images | completed | GPT-5.6 Terra | medium | 6% | 12 targeted + Ruff/mypy | Local data-bound visuals, safe SVG/PNG and verified Gemini assets |
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
- Module: 8 — Charts, diagrams, and images
- Result: chart artifacts derive only from their pinned Dataset and retain an accessible source-value table; typed diagrams render safe SVG/PNG; Gemini illustrations are normalized, hash-verified and retain prompt/model/caption/alt text.
- Files changed: `docs/DATA_MODEL.md`, `src/papercraft/{domain/{models.py,__init__.py},infrastructure/visuals/{__init__.py,charts.py,diagrams.py,images.py},application/{schemas.py,stages.py}}`, `tests_v2/test_visual_assets.py`, and `PROJECT_STATE.md`.
- Decisions: Application API 1 and database schema 5 remain unchanged because visual specifications use existing typed blocks/domain objects and artifacts. Legacy diagram source is compatibility input only; typed nodes/edges never invoke an external renderer. An optional failed Gemini illustration is retried once, then removed without blocking manuscript text.
- Tests: Python 3.13 — 12 targeted visual/render tests passed; scoped Ruff and strict MyPy passed; `git diff --check` passed. The existing DOCX visual-block render test provides the mixed-page smoke check.
- Known limitations: no live Gemini request or full suite was run; the DOCX renderer consumes image artifacts today, while finalized chart/diagram placement and visual page QA remain module 9 work. The default `python` command is 3.11, so checks used Python 3.13 explicitly.

## Next module
- Module: 9 — DOCX, LibreOffice, release QA
- Required inputs: completed modules 1 and 4–8, including persisted visual artifacts and accessible metadata
- Entry conditions: targeted visual/render invariants remain green
- Expected result: one QA-passed, current-revision DOCX with finalized visual placement

## Project history
- 2026-09-02 — Module 0 completed: Application API 1, worker protocol 1, database schema 5, and release policy 1 frozen without production-code changes.
- 2026-09-02 — Module 1 completed: immutable releases, atomic READY/SUCCEEDED, strict QA/review gates, edit invalidation, and guarded DOCX access implemented.
- 2026-09-02 — Module 2 completed: `DesktopApplication` facade, JSONL worker v1, durable request-id replay/conflict handling, and compatibility worker CLI implemented.
- 2026-09-02 — Module 3 completed: Credential Manager-only Gemini lifecycle, safe verification/status DTOs, and capability-bound fallback policy implemented.
- 2026-09-03 — Module 4 completed: scholarly discovery, immutable publication snapshots, fail-closed citation lineage, and bilingual scientific-article metadata implemented.
- 2026-09-03 — Module 5 completed: static AST/Tree-sitter code analysis with immutable source hashes and exact locators implemented.
- 2026-09-03 — Module 6 completed: reproducible RAS accounting, financial analysis, investment/credit calculations, and disclosed seeded modelled data implemented.
- 2026-09-03 — Module 7 completed: four active profiles, least-privilege section contexts, strict typed drafts, bounded repair, and no approval pause implemented.
- 2026-09-03 — Module 8 completed: reproducible Dataset charts, typed safe diagrams, and hash-verified optional Gemini illustrations implemented.
