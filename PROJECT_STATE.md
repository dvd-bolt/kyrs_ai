# PaperCraft AI Studio Project State

## Current snapshot
- Version: 0.2.0
- Current module: 6 — RAS finance and modelled data (`completed`)
- Last updated: 2026-09-03
- Application status: RAS/accounting and financial outputs are deterministic, Decimal-based, period/currency/unit-bound, and persist their complete recalculation recipe; modelled datasets are explicitly non-observational
- Working tree: modules 0–5 and the initial module 6 implementation are consolidated in commit `80a1fc9`; final module 6 production/test changes are scoped and reviewed with `git diff --check`
- Known blockers: none for module 6; `uv` remains unavailable, but this module introduced no dependency or lockfile change

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
- Planned cumulative percent: 53%
- Actual used percent: 20% (rounded account meter; module 6 observed from 2% to 20%, including the interrupted implementation turn)
- Remaining reserve: 80%
- Budget status: on_track (module 6 delta ≈18 pp versus 11 pp allocation; cumulative observed use remains below the 53% plan checkpoint)

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
- Module: 6 — RAS finance and modelled data
- Result: simple/compound RAS postings, turnovers and trial balance; horizontal/vertical analysis; liquidity, stability, profitability and turnover ratios; break-even/margin, NPV/IRR/PI/PP/DPP, and both loan schedules are deterministic and Decimal-based. `CalculationResult` persists its exact `CalculationSpec`; seeded datasets carry fixed non-observation disclosure.
- Files changed: `src/papercraft/infrastructure/calculations/{__init__.py,financial.py,financial_catalog.py,synthetic.py}`, `src/papercraft/profiles/models.py`, `tests_v2/{test_financial_calculations.py,test_profiles.py,test_render_qa.py}`, and `PROJECT_STATE.md`.
- Decisions: Application API 1 and database schema 5 remain unchanged; results use `ROUND_HALF_UP`, explicit period/currency/unit/scale, internal catalog `ras-chart-accounts-2026.1`, and JSON-safe decimal strings for persistence. Finance profiles prohibit model arithmetic and require synthetic-data disclosure.
- Tests: 21 targeted finance/data/profile tests passed on Python 3.13, including Hypothesis invariants, oracle values, zero division, unknown accounts, mixed currency/period, unbalanced opening ОСВ, and synthetic provenance; Ruff and strict MyPy passed. Manual oracle: ОСВ 118.00=118.00, NPV 4.13, IRR 13.07%, annuity first payment 106.62, differentiated first/last 112.00/101.00; scoped `git diff --check` passed.
- Known limitations: the RAS catalog is a versioned educational subset rather than a legal-reference database; IRR uses a bounded deterministic root search and rejects cash flows with no bracketed root. The full suite and live provider were intentionally not run. The default `python` command is 3.11, so module checks used installed Python 3.13 explicitly.

## Next module
- Module: 7 — Profiles and automatic writing
- Required inputs: [API contract](docs/API_CONTRACT.md), [data model](docs/DATA_MODEL.md), completed modules 0–6, and persisted `CalculationSpec`/`CalculationResult` values
- Entry conditions: modules 0–6 completed; finance and synthetic-data invariants remain green
- Expected result: versioned work profiles drive one-click evidence-bound automatic writing without plan approval

## Project history
- 2026-09-02 — Module 0 completed: Application API 1, worker protocol 1, database schema 5, and release policy 1 frozen without production-code changes.
- 2026-09-02 — Module 1 completed: immutable releases, atomic READY/SUCCEEDED, strict QA/review gates, edit invalidation, and guarded DOCX access implemented.
- 2026-09-02 — Module 2 completed: `DesktopApplication` facade, JSONL worker v1, durable request-id replay/conflict handling, and compatibility worker CLI implemented.
- 2026-09-02 — Module 3 completed: Credential Manager-only Gemini lifecycle, safe verification/status DTOs, and capability-bound fallback policy implemented.
- 2026-09-03 — Module 4 completed: scholarly discovery, immutable publication snapshots, fail-closed citation lineage, and bilingual scientific-article metadata implemented.
- 2026-09-03 — Module 5 completed: static AST/Tree-sitter code analysis with immutable source hashes and exact locators implemented.
- 2026-09-03 — Module 6 completed: reproducible RAS accounting, financial analysis, investment/credit calculations, and disclosed seeded modelled data implemented.
