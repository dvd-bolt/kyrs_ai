# Stage 3 release acceptance

Дата проверки: 2026-08-21. Целевая ветка: `codex/papercraft-v1`.

## Решение

**RELEASE BLOCKED.** В production-коде не осталось известных `CRITICAL`/`BLOCKER`
дефектов, но обязательная внешняя приёмка завершена не полностью. Поэтому release commit
не создаётся. Наличие собранного unsigned installer не означает, что unsigned beta принята.

## Матрица

| Область | Результат | Проверка / основание |
| --- | --- | --- |
| Production Gemini policy | PASS | Роли закреплены за `gemini-3.7-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-image`, `gemini-embedding-2`; thinking policy задаётся явно |
| Gemini live contract | PARTIAL / EXTERNAL BLOCKER | structured output + thinking PASS (включая восстановление после 429 за 210 с); Files/Vision lifecycle и embedding PASS; Search и background cancel получили 429; image model сообщил нулевую доступную квоту; успешный public `store=False` response не содержит provider interaction/request ID, поэтому локальный `client_request_id` хранится отдельно; повторная проверка двух historical File IDs получила 403 |
| Source provenance | PASS | byte-exact `SourceSnapshot`, SHA-256, immutable storage и цепочка Claim → Evidence → Source → Snapshot → Locator → Bibliography → Citation |
| Scholarly integrations | PASS | live Crossref/OpenAlex/DOI: 2 passed; official-source policy и SSRF checks включены |
| OCR/Vision | PASS | 6/6 live fixtures: русский scan, table/numbers, handwriting, bad quality/uncertain number, caption, mixed PDF page locators |
| DOCX templates | PASS | geometry/styles/header/footer preserved; VBA, ActiveX, embeddings, altChunk, DDE/INCLUDE и external relationships fail closed; input не перезаписывается |
| Office matrix | PARTIAL / EXTERNAL BLOCKER | LibreOffice 26.2.5.2: 2 passed и PDF generated; Word COM недоступен на машине |
| PDF visual QA | PARTIAL / EXTERNAL BLOCKER | live Gemini review первого цикла нашёл отсутствие page numbers, дефект исправлен; финальный Poppler/deterministic review PASS на 7 страницах, повторный Gemini review заблокирован 429 |
| Fault/security | PASS | 41 passed: retry classes, single-layer 429 handling, auth/safety fail-closed, pause/resume/cancel, checkpoint, cleanup retry/reconciliation, stale lease, corrupt artifact rebuild, ZIP budgets, secret scanning, diagram injection, atomic write, Office failures |
| Six live golden E2E ×2 | BLOCKED | все 12 runs реально выполнены за 48:18: 0 passed, 12 failed с `GeminiUnavailableError`/429; первый run дошёл до `extract_requirements`, остальные 11 остановились fail-closed на `preflight`; 12 отдельных acceptance JSON сохранены, новые File uploads очищены 2/2 |
| Desktop UI smoke | PASS | реальное PySide6 окно: create/import/save/start/retry/pause/resume/cancel, failure state, completed result fixture, preview, DOCX/PDF export, section rebuild; frozen GUI также запущен |
| Installer | PARTIAL / EXTERNAL BLOCKER | PyInstaller + Inno Setup build PASS; non-elevated silent install, first launch, upgrade `beta.0 → beta.1`, frozen worker, uninstall и byte-exact сохранность изолированного `%LOCALAPPDATA%\PaperCraftAI\projects` PASS на Windows 11; отдельные clean Windows 10/11 недоступны |
| Code signing | UNSIGNED | сертификат отсутствует; EXE и installer имеют `NotSigned` |
| Final source audit | PASS WITH LEGACY NOTE | release scope не содержит неявного mock mode, TODO/FIXME или опасного shell execution; найденные placeholders являются blocking QA sentinels. Архивный root-level PyQt6 prototype не входит в wheel/installer и имеет отдельный lint debt |

## Local quality gates

- `ruff check src tests_v2 packaging`: PASS.
- `mypy src/papercraft --strict`: PASS, 70 source files.
- `pytest -q`: 99 passed, 28 explicit opt-in integration skips.
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
