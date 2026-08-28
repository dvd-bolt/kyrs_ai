# Stage 3 release acceptance

Дата проверки: 2026-08-21, актуализация 2026-08-28. Целевая ветка: `codex/papercraft-v1`.

## Решение

**RELEASE BLOCKED.** В production-коде не осталось известных `CRITICAL`/`BLOCKER`
дефектов, но обязательная внешняя приёмка завершена не полностью. Поэтому release commit
не создаётся. Наличие собранного unsigned installer не означает, что unsigned beta принята.

## Матрица

| Область | Результат | Проверка / основание |
| --- | --- | --- |
| Production Gemini policy | PASS | Роли закреплены за `gemini-3.7-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-image`, `gemini-embedding-2`; thinking policy задаётся явно |
| Gemini live contract | PARTIAL / EXTERNAL BLOCKER | 27.08 устранён HTTP 400 `ResearchPlan`: provider adapter удаляет только outbound `maxItems`, локальный лимит Pydantic остаётся 80; text-only input передаётся строкой; `google-genai==2.19.0`; новый research-role contract PASS один раз. Следующий повтор и golden E2E заблокированы provider free-tier HTTP 429. Исторически structured/thinking, Files/Vision и embedding PASS; Search, image и background cancel требуют свежего PASS. |
| Source provenance | PASS | byte-exact `SourceSnapshot`, SHA-256, immutable storage и цепочка Claim → Evidence → Source → Snapshot → Locator → Bibliography → Citation |
| Scholarly integrations | PASS | live Crossref/OpenAlex/DOI: 2 passed; official-source policy и SSRF checks включены |
| OCR/Vision | PASS | 6/6 live fixtures: русский scan, table/numbers, handwriting, bad quality/uncertain number, caption, mixed PDF page locators |
| DOCX templates | PASS | geometry/styles/header/footer preserved; VBA, ActiveX, embeddings, altChunk, DDE/INCLUDE и external relationships fail closed; input не перезаписывается |
| Office matrix | PARTIAL / EXTERNAL BLOCKER | LibreOffice 26.2.5.2: 2 passed и PDF generated; Word COM недоступен на машине |
| PDF visual QA | PARTIAL / EXTERNAL BLOCKER | live Gemini review первого цикла нашёл отсутствие page numbers, дефект исправлен; финальный Poppler/deterministic review PASS на 7 страницах, повторный Gemini review заблокирован 429 |
| Fault/security | PASS | 41 passed: retry classes, single-layer 429 handling, auth/safety fail-closed, pause/resume/cancel, checkpoint, cleanup retry/reconciliation, stale lease, corrupt artifact rebuild, ZIP budgets, secret scanning, diagram injection, atomic write, Office failures |
| Six live golden E2E ×2 | BLOCKED | historical 12 runs: 0 passed из-за `GeminiUnavailableError`/429. После исправления HTTP 400 новый `it_coursework-1` больше не получил invalid-argument, но остановился на `build_evidence_index` после bounded HTTP 429 retries; evidence: `build/stage3/live-golden-fixed-20260827-221148/it_coursework/run-1/acceptance.json`. 12/12 актуальных runs ещё не запускались. |
| Desktop UI smoke | PASS | реальное PySide6 окно: create/import/save/start/retry/pause/resume/cancel, failure state, completed result fixture, preview, DOCX/PDF export, section rebuild; frozen GUI также запущен |
| Installer | PARTIAL / EXTERNAL BLOCKER | PyInstaller + Inno Setup build PASS; non-elevated silent install, first launch, upgrade `beta.0 → beta.1`, frozen worker, uninstall и byte-exact сохранность изолированного `%LOCALAPPDATA%\PaperCraftAI\projects` PASS на Windows 11; отдельные clean Windows 10/11 недоступны |
| Code signing | UNSIGNED | сертификат отсутствует; EXE и installer имеют `NotSigned` |
| Final source audit | PASS WITH LEGACY NOTE | release scope не содержит неявного mock mode, TODO/FIXME или опасного shell execution; найденные placeholders являются blocking QA sentinels. Архивный root-level PyQt6 prototype не входит в wheel/installer и имеет отдельный lint debt |

## Local quality gates

- `ruff check src tests_v2 packaging`: PASS.
- `mypy src/papercraft --strict`: PASS, 70 source files.
- `uv run --locked python -m pytest -q`: 106 passed, 29 explicit opt-in integration skips.
- `uv run --locked python -m pytest -q tests_v2/test_gemini_gateway.py tests_v2/test_autopilot_e2e.py tests_v2/test_fake_golden_e2e.py`: 34 passed.
- `test_live_research_plan_structured_contract`: PASS один раз после adapter; последующий повтор BLOCKED HTTP 429.
- Fault/security regression subset: 41 passed.
- `PAPERCRAFT_RUN_RESEARCH_TESTS=1`: 2 passed.
- `PAPERCRAFT_RUN_OFFICE_TESTS=1`: 2 passed.
- PowerShell parser для installer acceptance harness и `git diff --check`: PASS.
- Exact credential leak check по 1555 файлам source/tests/packaging/docs/stage3/dist: 0 matches.

## Build evidence

- `dist/PaperCraftAI/PaperCraftAI.exe`: version `1.0.0-beta.1`, 21,938,637 bytes,
  SHA-256 `F481A6BEC687086BA22F5D672D97AACCAD1D2C75FEB5C56FE619AEF2D2FB87EB`.
- `dist/installer/PaperCraftAI-Setup-1.0.0-beta.1.exe`: 86,699,665 bytes,
  SHA-256 `A3543156980F277CA9340731DAEA6828AA55F2CC61353454A09A84C694931F62`.
- Installer preservation evidence: `build/stage3/installer-acceptance/20260821-153133/acceptance.json`, PASS.
- Final LibreOffice PDF: 193,624 bytes, 7 rendered pages, deterministic QA PASS,
  SHA-256 `9E044A321ABADF8578A84170173349D2653946895BEA99970BF193DCA3FFD40B`.

## Remaining external blockers

1. Gemini provider quota/billing must permit the complete contract suite and twelve golden runs;
   provider permissions must also allow background cancellation and conclusive get/delete of
   the two historical File IDs currently retained as undeleted audit records.
2. Microsoft Word must be available for the Word COM half of the Office matrix.
3. Separate clean Windows 10 and Windows 11 environments are required for final installer acceptance.
4. A code-signing certificate is absent; after items 1–3 pass, the permitted release route is
   `release: papercraft ai studio v1 unsigned beta` unless a certificate is supplied.
