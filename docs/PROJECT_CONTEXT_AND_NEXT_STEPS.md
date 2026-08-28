# PaperCraft AI Studio: контекст проекта и продолжение работ

Актуально на 2026-08-28. Этот файл предназначен для передачи проекта в новый чат или новому
разработчику. Его следует читать вместе с `docs/STAGE3_ACCEPTANCE.md` и
`docs/STAGE3_HANDOFF.md`.

## 1. Что это за программа

**PaperCraft AI Studio** — Windows desktop-приложение для подготовки академических документов.
Пользователь создаёт проект, указывает тему и тип работы, загружает методичку, исходные данные,
пример или DOCX-шаблон. Затем автопилот:

1. безопасно импортирует и хеширует материалы;
2. извлекает требования;
3. создаёт проверяемую карту фактов и источников;
4. строит план, текст, таблицы и визуализации;
5. связывает утверждения с доказательствами и локаторами источников;
6. формирует DOCX, при наличии Office/LibreOffice — финализирует его и экспортирует PDF;
7. выпускает QA-отчёт.

Программа не должна выдавать выдуманные факты или источники за реальные: для каждого
проверяемого утверждения есть цепочка
`Claim → Evidence → Source → Snapshot → Locator → Bibliography → Citation`.

Поддерживаются: курсовые работы, научные статьи, отчёты по практике и школьные проекты.

## 2. Архитектура

```text
src/papercraft/
├── domain/             Модели предметной области и состояния run/stage
├── profiles/           Правила типов работ и профилей
├── application/        Autopilot, стадии и сценарии приложения
├── infrastructure/
│   ├── gemini/         Production gateway, хранение credential, FakeGemini для тестов
│   ├── ingest/         Безопасный импорт, parsers и OCR/Vision
│   ├── research/       DOI/Crossref/OpenAlex, snapshots, URL/SSRF policy
│   ├── persistence/    SQLite, project storage, snapshots и артефакты
│   ├── calculations/   Таблицы, расчёты и provenance данных
│   ├── visuals/        Декларативные графики/диаграммы без исполнения кода
│   ├── render/         DOCX renderer, Word/LibreOffice/PDF finalizers
│   └── qa/             Детерминированные quality gates
├── worker/             Фоновое выполнение pipeline
└── ui/                 PySide6 desktop-интерфейс
```

Корневые `core/`, `models/`, `ui/` и `web_app.py` — legacy-прототип. Release scope —
`src/papercraft`, `tests_v2` и `packaging`.

Главный pipeline находится в `src/papercraft/application/stages.py`. Каждый stage имеет
устойчивое состояние, checkpoint, hash входов, retry/failure policy и сохраняется в SQLite.

## 3. Реализовано

- Production policy: `gemini-3.7-flash`, `gemini-3.5-flash-lite`,
  `gemini-3.1-flash-image`, `gemini-embedding-2`.
- Реальный Gemini gateway: structured output, thinking, Files/Vision, embeddings, Search,
  image generation, background operations, usage/cost accounting, bounded retry и cleanup.
- Внешние scholarly sources: Crossref, OpenAlex, DOI resolution, official-source policy,
  URL validation и SSRF protection.
- Byte-exact `SourceSnapshot` с SHA-256 и полная citation provenance.
- OCR/Vision, табличные данные, локаторы PDF-страниц и обработка неопределённых результатов.
- Безопасные DOCX templates: активное содержимое и внешние связи блокируются до обработки.
- DOCX/PDF generation, page numbering, deterministic PDF visual QA, LibreOffice update.
- UI, worker, pause/resume/cancel, recovery, atomic writes, stale-lease/cleanup handling.
- PyInstaller/Inno Setup build и локальная проверка install → launch → update → uninstall.

## 4. Подтверждённые проверки

| Проверка | Последний известный результат |
| --- | --- |
| Локальный suite | `106 passed`, `29 skipped` (opt-in external tests) |
| Ruff | PASS |
| Strict MyPy | PASS, 70 source files |
| Fault/security | 41 passed |
| Live Crossref/OpenAlex/DOI | 2 passed |
| Live OCR/Vision fixtures | 6/6 passed |
| LibreOffice Office matrix | 2 passed |
| DOCX template safety | PASS |
| PDF deterministic visual QA | PASS |
| Desktop/frozen UI smoke | PASS |
| Local installer acceptance | PASS |

Подробные доказательства, хеши installer и прошлые live-результаты находятся в
`docs/STAGE3_ACCEPTANCE.md`.

## 5. Live-обновление 27.08.2026

### HTTP 400 исправлен

`ResearchPlan` был единственной production-схемой, отправлявшей `maxItems: 80`. Текущий
Gemini Interactions endpoint отвечал HTTP 400 на эту схему, хотя локальная Pydantic-валидация
и документация structured output допускают данный keyword. В
`src/papercraft/infrastructure/gemini/gateway.py` добавлен provider-side adapter: он удаляет
только `maxItems` из отправляемой копии JSON Schema; доменная `ResearchPlan` и локальный
лимит 80 claims не изменены.

Дополнительно gateway теперь:

- использует строковый `input` для text-only structured request и мультимодальный массив
  только при наличии файлов;
- закреплён на `google-genai==2.19.0` и запускается через `uv run --locked`;
- возвращает для HTTP 400 только безопасные diagnostics: SDK/model/role/thinking/schema
  fingerprint и provider field violations, без prompt, файлов, headers или credential.

Новый opt-in контракт прошёл один раз:

```text
tests_v2/test_gemini_live.py::test_live_research_plan_structured_contract
1 passed
```

### Текущий blocker — provider quota

Контрольный `it_coursework-1` после исправления больше не получил HTTP 400. Он остановился
на `build_evidence_index` после bounded retries с `GeminiUnavailableError` / HTTP 429:
free-tier limit для `gemini-3.7-flash` исчерпан. Повторный дешёвый `ResearchPlan` contract
после provider retry window также завершился HTTP 429. Это внешний blocker; 12 golden runs
не запускались, чтобы не расходовать quota параллельными запросами.

Последний безопасный artifact (игнорируется Git):

```text
build/stage3/live-golden-fixed-20260827-221148/it_coursework/run-1/acceptance.json
```

## 6. План продолжения

### Шаг A — безопасность и подготовка

1. Ключи, которые были отправлены в чат, нужно отозвать у провайдеров и выпустить новые.
2. Новый Gemini credential хранить только в Windows Credential Manager либо во временной
   process environment variable `GEMINI_API_KEY`.
3. Никогда не добавлять ключ в `.env`, исходники, тестовые fixtures, Git, SQLite, QA-отчёты,
   комментарии или вывод команд.

### Шаг B — восстановить Gemini quota и подтвердить исправление

1. Не менять model, retry policy или schema adapter при HTTP 429.
2. После реального provider retry window сначала запустить только
   `test_live_research_plan_structured_contract` через `uv run --locked`.
3. При PASS выполнить один `it_coursework-1` в новом timestamped
   `PAPERCRAFT_GOLDEN_OUTPUT_DIR`.
4. Успех означает статус `succeeded`, ненулевую стоимость, DOCX/PDF/QA artifacts и отсутствие
   неочищенных remote files.

### Шаг C — закрыть Gemini acceptance

После PASS одного сценария:

1. Запустить `tests_v2/test_gemini_live.py`.
2. Запустить все шесть golden scenarios в двух повторах: 12 успешных запусков обязательны.
3. Убедиться, что Files/Vision, embeddings, Search, image, background cancel и final Gemini
   PDF review имеют актуальные live-results; исправить дефекты, а не отмечать их как skip.
4. Сохранить только безопасные acceptance JSON и обновить `docs/STAGE3_ACCEPTANCE.md`.

### Шаг D — остальные release gates

1. Установить Microsoft Word и прогнать Word + LibreOffice matrix.
2. Собрать installer и выполнить сценарий на отдельных чистых Windows 10 и Windows 11.
3. При отсутствии certificate выпускать только unsigned beta.
4. Выполнить финальный audit TODO/FIXME/mock/stub/placeholder/exec/remote fallback.
5. Только при полном PASS создать один из commit:

```text
release: papercraft ai studio v1
release: papercraft ai studio v1 unsigned beta
```

Нельзя объявлять релиз готовым при `CRITICAL`, `BLOCKER`, `FAIL` или обязательном `BLOCKED`.

## 7. Полезные команды

Перед live-запусками credential уже должен находиться в Windows Credential Manager или в
переменной текущего процесса. Не вставлять значение ключа в команды, документы или историю Git.

```powershell
# Базовые gates — не использовать системный python
uv run --locked python -m pytest -q
uv run --locked python -m ruff check src tests_v2 packaging
uv run --locked python -m mypy src/papercraft --strict

# Один golden E2E после исправления
$env:PAPERCRAFT_RUN_GOLDEN_TESTS = "1"
uv run --locked python -m pytest -q "tests_v2/test_live_golden_e2e.py::test_live_golden_pipeline_twice[it_coursework-1]"

# Gemini contracts
$env:PAPERCRAFT_RUN_GEMINI_TESTS = "1"
uv run --locked python -m pytest -q tests_v2/test_gemini_live.py

# 12 golden E2E runs
$env:PAPERCRAFT_RUN_GOLDEN_TESTS = "1"
uv run --locked python -m pytest -q tests_v2/test_live_golden_e2e.py

# Office matrix
$env:PAPERCRAFT_RUN_OFFICE_TESTS = "1"
uv run --locked python -m pytest -q tests_v2/test_office_integration.py

# Windows build
powershell -ExecutionPolicy Bypass -File packaging/build_windows.ps1 -Version "1.0.0-beta.1"
```

## 8. Git и передача

- Repository: `https://github.com/dvd-bolt/kyrs_ai`
- Branch: `codex/papercraft-v1`
- Актуальный commit следует брать из `git log -1 --oneline`; не дублировать его SHA в этом
  файле, чтобы handoff не устаревал после технических исправлений.
- Branch tracking настроен на `origin/codex/papercraft-v1`.

Важные документы:

- `docs/PROJECT_CONTEXT_AND_NEXT_STEPS.md` — этот файл.
- `docs/STAGE3_HANDOFF.md` — компактный handoff.
- `docs/STAGE3_ACCEPTANCE.md` — release matrix и доказательства.
- `docs/V1_ACCEPTANCE.md` — критерии v1.
- `docs/IMPLEMENTATION_STATUS.md` — общий status.
- `docs/ARCHITECTURE.md` — архитектурные ограничения.

## 9. Короткий prompt для следующего чата

```text
Прочитай docs/PROJECT_CONTEXT_AND_NEXT_STEPS.md, docs/STAGE3_ACCEPTANCE.md и
docs/STAGE3_HANDOFF.md. Продолжи только Stage 3 PaperCraft AI Studio на ветке
codex/papercraft-v1. HTTP 400 ResearchPlan уже исправлен provider-side удалением maxItems;
сначала дождись доступной Gemini quota, затем прогони один it_coursework golden E2E через
uv run --locked. Не сохраняй и не выводи Gemini credential. Не создавай release commit,
пока обязательные live/Office/clean-Windows gates не пройдут.
```
