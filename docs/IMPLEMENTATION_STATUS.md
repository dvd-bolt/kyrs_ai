# Implementation status

## Этап 2 — блок 1: persistence и миграции

Выполнено: версия схемы SQLite поднята до 2 additive-миграцией; добавлены `MigrationService`,
план/результат миграции, журнал миграций, revisions и backup records. `ProjectService`
получил health (SQLite integrity, SHA-256 originals и artifacts), ручной/автоматический
backup c retention 10 automatic и проверяемый restore. Существующий `SQLiteRepository`
сохранён. Целевые проверки: Ruff, strict MyPy и persistence/application tests — проходят.

## Этап 2 — блок 2: worker и checkpoints

Выполнено: `StageRun` хранит dependency/output hashes, heartbeat, progress, failure details
и remote resource IDs; добавлены кооперативные `CancellationToken`/`StageProgress`, recovery
stale running stages и ациклический dependency graph для точечной invalidation. Remote uploads
сразу регистрируются в SQLite, сохраняя метаданные и ID стадии. Целевые application/worker tests,
Ruff и strict MyPy проходят.

## Этап 2 — блок 3: ingestion и OCR-контракт

Выполнено: добавлены `VisionOCRPort` и детерминированный `FakeVision`; scanned PDF при
подключённом vision-port рендерится локально и сохраняет page locator, confidence, таблицы,
captions и uncertain numbers. DOCX сохраняет документные properties/sections/relationships;
XLSX — raw/formula/cached values, formats, types, named/merged ranges. Анализ кода отдаёт
symbols, imports, entrypoints, endpoints, classes/functions/tests с line locators по
tree-sitter-совместимому контракту (Python AST precision, portable fallback). Ingest и tabular
tests, Ruff и strict MyPy проходят.

## Этап 2 — блок 4: требования и шаблоны

Выполнено: домен содержит `RequirementCoverage`, `TemplateAnalysis` и типизированные section/style/
relationship модели, а также безопасный декларативный `TemplateApplicationPlan` (raw XML отклоняется).
`RequirementResolver` применяет фиксированный порядок методичка → шаблон → пользователь → пример →
профиль → built-in и оставляет equal-priority ambiguity для `WAITING_INPUT` вместо угадывания.

## Этап 2 — блок 5: профили работ

Выполнено: введён исполнимый контракт `ProfilePlugin` и адаптер всех встроенных `WorkProfile`: строит
blueprint, задаёт artifacts/final requirements, подготавливает facts и запускает профильную validation
финансовых расчётов и manuscript. Profile tests, Ruff и strict MyPy проходят.

## Этап 2 — блок 6: FactLedger и расчёты

Выполнено: каждый paragraph block поддерживает `numeric_fact_ids`; deterministic QA блокирует
числа без FactLedger provenance либо со ссылкой на неизвестный fact. К существующим financial
double-entry/invariants добавлены deterministic sums/percentages/mean/median/correlation с
защитой от некорректных series. Render/QA tests, Ruff и strict MyPy проходят.

## Этап 2 — блок 7: генерационный движок

Выполнено: `ContextBuilder` собирает typed section context из section-specific claims, verified
evidence, linked bibliography/datasets, glossary, requirements и conclusion dependencies. Production
generation использует этот контекст, выполняет cooperative cancellation/progress между sections и
сохраняет typed draft → deterministic validation → critic → targeted repair (bounded options cycles).
Fake E2E, render/QA tests, Ruff и strict MyPy проходят.

## Этап 2 — блок 8: визуальные материалы

Выполнено: existing dataset-backed table/chart and local Mermaid/Pillow diagram paths остаются
trusted-only, validate inputs and images, enforce subprocess timeout and fail instead of accepting
placeholder. Renderer emits numbered OMML formulae and source-located code listings; no model-supplied
executable visual code is accepted. Render/QA tests проходят.

## Этап 2 — блок 9: DOCX renderer

Выполнено: block renderer сохраняет TOC/PAGE/SEQ, bookmarks/cross-reference fields, bibliography,
repeating table headers, row no-split, title/page sections, formulas and code. Template application теперь
принимает только typed `TemplateApplicationPlan`; plan requires an actual DOCX and cannot transport raw
body XML. Render tests, Ruff и strict MyPy проходят.

## Этап 2 — блок 10: детерминированный QA

Выполнено: QA already validates requirements/conflicts, claims/evidence/citations, FactLedger,
datasets, artifacts, placeholders, OpenXML fields/package and PDF structure. Numeric paragraph-to-fact
provenance is now a blocking gate; `CRITICAL`/`BLOCKER` cannot reach successful packaging.

## Этап 2 — блок 11: PySide6 workflow

Подтверждено: существующие шесть связанных PySide6 screens expose projects/health, sources, plan,
worker controls and results paths; `pytest-qt`-compatible offscreen UI test покрывает create/save,
plan edit, retry/rebuild, pause/resume/cancel. UI MyPy override из baseline removed; strict MyPy
проверяет UI вместе с остальным пакетом.

## Этап 2 — блок 12: Fake golden E2E

Добавлен `tests_v2/test_fake_golden_e2e.py`: шесть manifests (IT/finance coursework, article,
programming/accounting reports, school project) проходят полный local FakeGemini pipeline до DOCX,
fixture-PDF и deterministic QA. Реальная PDF-конвертация остаётся отдельной Office integration-
проверкой, поскольку normal CI намеренно не требует Word/LibreOffice.

## Этап 1 baseline

Статус этапа 1 PaperCraft AI Studio на 2026-08-21. Это baseline, а не release-приёмка.

| Подсистема | Статус | Основание / следующий шаг |
| --- | --- | --- |
| Domain models и pipeline | WORKING | 49 локальных тестов `tests_v2` проходят; архитектура этапа не менялась |
| Ingest | WORKING | Покрыт `tests_v2/test_ingest_research.py` |
| Research и bibliography | WORKING | Локальные FakeGemini/evidence проверки проходят |
| Calculations | WORKING | Табличные и финансовые тесты проходят |
| Persistence | WORKING | Repository/storage тесты проходят; переписывания не выполнялось |
| Gemini Gateway | PARTIAL | FakeGemini проверен; live Gemini не проверен без ключа |
| DOCX renderer | WORKING | Renderer/QA тесты проходят |
| Office finalizer | INTEGRATION_REQUIRED | LibreOffice PDF работает при наличии бинарника; Word COM требует отдельной проверки |
| Desktop UI/worker | PARTIAL | Локальные тесты проходят; нужна проверка на целевом Windows |
| Visuals | PARTIAL | Код и локальные тесты есть; release-набор графиков ещё не принят |
| Installer | BLOCKED | Installer ещё не принят |
| Golden E2E | STUB | Созданы только шесть manifest-каркасов |

## Baseline

- Локальная проверка на доступном Python 3.13: 49 passed, 3 skipped.
- Python 3.12 не установлен; отдельная проверка 3.12 нужна.
- Live Gemini намеренно не запускался без ключа.
- LibreOffice PDF и Word COM остаются отдельными интеграционными проверками.
