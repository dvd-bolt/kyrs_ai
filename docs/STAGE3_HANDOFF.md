# PaperCraft AI Studio — Stage 3 handoff

Состояние на 2026-08-28. Рабочая ветка: `codex/papercraft-v1`.

## Краткий статус

Production-функциональность этапа 3 реализована, локальные quality gates проходят. Выпуск
остаётся заблокирован только обязательными внешними проверками: доступной квотой Gemini,
Microsoft Word и отдельными чистыми Windows 10/11. Release-коммит пока создавать нельзя.

Подробная матрица с результатами и артефактами находится в
[`STAGE3_ACCEPTANCE.md`](STAGE3_ACCEPTANCE.md).

## Что реализовано

- Production policy для `gemini-3.7-flash`, `gemini-3.5-flash-lite`,
  `gemini-3.1-flash-image` и `gemini-embedding-2`.
- Реальный Gemini gateway: structured output, thinking, Files/Vision, embeddings, Search,
  image generation, background operations, usage/cost accounting, bounded retry и cleanup.
- Fail-closed preflight: недоступность обязательного внешнего сервиса не маскируется fake-mode.
- Byte-exact `SourceSnapshot` с SHA-256 и трассировка
  Claim → Evidence → Source → Snapshot → Locator → Bibliography → Citation.
- Crossref, OpenAlex, DOI resolution, official-source policy, URL/SSRF validation.
- OCR/Vision для изображений и PDF, включая таблицы, рукописный текст и page locators.
- Безопасное применение DOCX-шаблонов с сохранением геометрии, стилей, headers/footers;
  VBA, ActiveX, DDE, external relationships и другие активные компоненты отклоняются.
- DOCX/PDF rendering, LibreOffice field update, PDF pagination и deterministic visual QA.
- Fault recovery: pause/resume/cancel, checkpoints, stale leases, cleanup reconciliation,
  atomic writes и восстановление повреждённых артефактов.
- Desktop UI smoke и frozen UI/worker smoke.
- PyInstaller/Inno Setup build и сценарий install → launch → upgrade → uninstall с проверкой
  byte-exact сохранности проектов.

## Что уже проверено

- `uv run --locked python -m pytest -q`: 106 passed, 29 opt-in integration skips.
- `ruff check src tests_v2 packaging`: PASS.
- `mypy src/papercraft --strict`: PASS, 70 source files.
- Fault/security subset: 41 passed.
- Live Crossref/OpenAlex/DOI: 2 passed.
- Live OCR/Vision fixtures: 6/6 passed.
- LibreOffice matrix: 2 passed.
- DOCX template safety, PDF deterministic QA, desktop UI и local installer acceptance: PASS.
- В изменяемых файлах нет фактических credentials; единственное совпадение generic scanner —
  существующая синтетическая fixture теста secret-scanner.

## Обновление 27.08.2026

HTTP 400 `ResearchPlan` исправлен: gateway удаляет только provider-side `maxItems`, сохраняя
локальный Pydantic limit 80; text-only structured input теперь строка; SDK закреплён на
`google-genai==2.19.0`. Новый live research contract прошёл один раз.

## Что не завершено

1. Полный Gemini contract suite остаётся внешне заблокированным provider free-tier HTTP 429:
   повтор research contract и контрольный `it_coursework-1` остановились на quota после
   bounded retries. Не считать это разрешённым skip и не запускать 12 golden runs до
   восстановления quota.
2. Microsoft Word COM matrix: Word не установлен на текущей машине. LibreOffice уже проверен.
3. Installer acceptance на отдельных чистых Windows 10 и Windows 11: локальная Windows 11
   проверена, но не считается чистой средой.
4. Code signing: сертификат отсутствует. После закрытия пунктов 1–3 разрешён unsigned beta.

## Как продолжить

Секрет нельзя добавлять в `.env`, Git, логи или аргументы команд. Gemini credential должен
оставаться в Windows Credential Manager либо в переменной процесса `GEMINI_API_KEY`.

```powershell
# Локальные gates
uv run --locked python -m pytest -q
uv run --locked python -m ruff check src tests_v2 packaging
uv run --locked python -m mypy src/papercraft --strict

# Gemini contracts после восстановления quota
$env:PAPERCRAFT_RUN_GEMINI_TESTS = "1"
uv run --locked python -m pytest -q tests_v2/test_gemini_live.py

# Все шесть golden scenarios в двух повторах
$env:PAPERCRAFT_RUN_GOLDEN_TESTS = "1"
uv run --locked python -m pytest -q tests_v2/test_live_golden_e2e.py

# Word + LibreOffice matrix на машине с установленным Word
$env:PAPERCRAFT_RUN_OFFICE_TESTS = "1"
uv run --locked python -m pytest -q tests_v2/test_office_integration.py

# Windows build
powershell -ExecutionPolicy Bypass -File packaging/build_windows.ps1 `
  -Version "1.0.0-beta.1"
```

После прогонов нужно обновить `STAGE3_ACCEPTANCE.md` реальными результатами. При отсутствии
сертификата и только после полного PASS итоговый commit должен называться:

```text
release: papercraft ai studio v1 unsigned beta
```

Если появится сертификат и подпись успешно проверена:

```text
release: papercraft ai studio v1
```

## Файлы, с которых начинать

- `docs/STAGE3_ACCEPTANCE.md` — authoritative release matrix.
- `docs/V1_ACCEPTANCE.md` — критерии v1.
- `docs/IMPLEMENTATION_STATUS.md` — общий статус реализации.
- `docs/ARCHITECTURE.md` — архитектурные решения.
- `src/papercraft/infrastructure/gemini/gateway.py` — Gemini integration/retry/cleanup.
- `src/papercraft/infrastructure/research/` — snapshots и scholarly integrations.
- `tests_v2/test_live_golden_e2e.py` — двенадцать обязательных golden runs.
- `packaging/test_windows_installer.ps1` — installer acceptance harness.

## Правило выпуска

Наличие собранного installer не означает готовность релиза. Нельзя объявлять v1 или unsigned
beta готовой, пока хотя бы один обязательный gate имеет статус `BLOCKED`, `FAIL`, `CRITICAL`
или `BLOCKER`.
