# PaperCraft AI Studio — Stage 3 handoff

Состояние на 2026-08-23. Рабочая ветка: `codex/papercraft-v1`.

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

- `pytest -q`: 99 passed, 28 opt-in integration skips.
- `ruff check src tests_v2 packaging`: PASS.
- `mypy src/papercraft --strict`: PASS, 70 source files.
- Fault/security subset: 41 passed.
- Live Crossref/OpenAlex/DOI: 2 passed.
- Live OCR/Vision fixtures: 6/6 passed.
- LibreOffice matrix: 2 passed.
- DOCX template safety, PDF deterministic QA, desktop UI и local installer acceptance: PASS.
- Проверка на утечку фактического Gemini credential: 0 matches.

## Что не завершено

1. Полный Gemini contract suite: structured/thinking, Files/Vision и embeddings проходили,
   но Search, image и background cancel были остановлены provider quota/HTTP 429.
2. Шесть live golden E2E дважды: все 12 запусков выполнены, 0/12 завершились успешно из-за
   `GeminiUnavailableError`/HTTP 429. Это внешний blocker, а не разрешённый skip.
3. Microsoft Word COM matrix: Word не установлен на текущей машине. LibreOffice уже проверен.
4. Installer acceptance на отдельных чистых Windows 10 и Windows 11: локальная Windows 11
   проверена, но не считается чистой средой.
5. Code signing: сертификат отсутствует. После закрытия пунктов 1–4 разрешён unsigned beta.

## Как продолжить

Секрет нельзя добавлять в `.env`, Git, логи или аргументы команд. Gemini credential должен
оставаться в Windows Credential Manager либо в переменной процесса `GEMINI_API_KEY`.

```powershell
# Локальные gates
python -m pytest -q
python -m ruff check src tests_v2 packaging
python -m mypy src/papercraft --strict

# Gemini contracts после восстановления quota
$env:PAPERCRAFT_RUN_GEMINI_TESTS = "1"
python -m pytest -q tests_v2/test_gemini_live.py

# Все шесть golden scenarios в двух повторах
$env:PAPERCRAFT_RUN_GOLDEN_TESTS = "1"
python -m pytest -q tests_v2/test_live_golden_e2e.py

# Word + LibreOffice matrix на машине с установленным Word
$env:PAPERCRAFT_RUN_OFFICE_TESTS = "1"
python -m pytest -q tests_v2/test_office_integration.py

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
