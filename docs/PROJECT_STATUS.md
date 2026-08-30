# PaperCraft — состояние проекта

**Актуально на 2026-08-30.** Ветка: `codex/papercraft-v1`.

## Текущий результат

Проект находится на стадии личной **LibreOffice-only private beta**. Основной
pipeline, desktop UI, durable scheduler, QA/export gates и LibreOffice
finalization реализованы. Microsoft Word compatibility, code signing и
automatic updates сознательно не входят в этот beta-цикл.

### Реализовано в текущем рабочем дереве

- Надёжный Gemini gateway: adaptive throttling, 429 retry/cooldown, durable
  cost accounting, pause/resume/cancel, remote-file and stored-interaction
  cleanup. Параллельная генерация выключена по умолчанию.
- Typed `RequirementCoverageReport`: каждое правило методички имеет статус и
  локацию в manuscript/DOCX/PDF; незакрытые requirements, evidence gaps и
  неподтверждённые числа блокируют экспорт.
- Ревизии plan/sections сохраняются в SQLite и инвалидируют только зависимые
  стадии. Text-only revisions начинают с citation QA; revisions с графиками,
  diagrams или генерируемыми figures начинают с visual generation.
- Citation audit заменяет citation graph и manuscript атомарно; отказ поздней
  проверки не повреждает ранее опубликованное состояние.
- LibreOffice обновляет поля, финализирует DOCX, экспортирует PDF и участвует
  в visual-QA feature matrix.
- CI разделяет локальные, LibreOffice, Gemini contract, golden и destructive
  background-lifecycle suites. Live suites требуют явного opt-in и лимитов
  стоимости.

## Подтверждённые проверки

Последняя локальная проверка этого рабочего дерева:

| Проверка | Результат |
| --- | --- |
| `ruff check src tests_v2 packaging` | PASS |
| `mypy src/papercraft --strict` | PASS, 77 source files |
| `pytest -q` | **209 passed, 29 expected opt-in skips** |
| LibreOffice integration/finalizer | **12 passed** |
| CI workflow YAML | successfully parsed |
| `git diff --check` | PASS |
| Scan for checked-in Google API-key-shaped literals | none found |

The opt-in skips are deliberately not counted as Gemini acceptance. They cover
live Gemini, golden, research and vision provider runs that require an explicit
replacement credential and spending cap.

## Незакрытые beta-gates

1. **Live Gemini acceptance.** Ключ, отправленный в чат, считается
   скомпрометированным и не использовался. Его нужно отозвать. После создания
   replacement credential, хранимого только в Windows Credential Manager или
   во временной process variable, выполнить строго по порядку:
   structured contract → `it_coursework` smoke → full Gemini suite → 12 golden
   runs → stored background cancel/delete/404 check.
2. **Manual factual editing UX.** Правки без claim/bibliography bindings
   сохраняются, но намеренно блокируются citation QA and export. Нужен
   dedicated UI для связывания фактической правки с evidence.
3. **Target-PC visual acceptance.** LibreOffice matrix пройдена на текущем
   компьютере; её следует повторить на каждом целевом Windows PC.

## Важные ограничения

- Не помещать Gemini credentials в `.env`, Git, SQLite, logs, fixtures,
  documentation or command-line history.
- Не объявлять live/golden acceptance успешной, пока не получены реальные
  результаты с replacement credential и утверждёнными cost caps.
- Microsoft Word не является fallback finalizer этой beta.

## Связанные документы

- [`BETA_ACCEPTANCE_MATRIX.md`](BETA_ACCEPTANCE_MATRIX.md) — матрица beta
  приёмки.
- [`BETA_ACCEPTANCE_RUNBOOK.md`](BETA_ACCEPTANCE_RUNBOOK.md) — безопасный
  порядок локальных и live проверок.
- [`PROJECT_CONTEXT_AND_NEXT_STEPS.md`](PROJECT_CONTEXT_AND_NEXT_STEPS.md) —
  расширенный handoff и исторический контекст.
