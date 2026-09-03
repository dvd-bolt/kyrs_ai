# PaperCraft AI Studio — модульный план реализации через отдельные чаты

## 1. Ограничение одного пятичасового окна Plus

Цель — выполнить весь MVP в одном новом пятичасовом окне Codex Plus, распределив между модулями 100 условных единиц расхода.

Это бюджет, а не технически гарантируемое число токенов: расход зависит от модели, reasoning effort, размера контекста, инструментов и сложности изменений. Официальные оценки для Plus составляют примерно 10–100 локальных сообщений Sol, 25–200 Terra и 250–2000 Luna за пятичасовое окно. Luna предназначена для экономичных массовых задач, Terra — для основной разработки, Sol — для наиболее сложных решений. [Официальная документация OpenAI](https://learn.chatgpt.com/docs/pricing), [руководство по моделям GPT‑5.6](https://developers.openai.com/api/docs/guides/latest-model).

На момент составления плана текущее пятичасовое окно использовано на 100%, недельное — на 56%, дополнительных кредитов нет. Реализацию нужно начинать после сброса Usage.

Правила бюджета:

- Один модуль — один отдельный чат.
- Не использовать subagents и параллельные чаты.
- Не использовать `max`, `ultra`, Pro mode или Fast mode.
- После модулей 3, 6, 9 и 11 проверять Usage.
- Проценты в плане — максимальная доля свежего окна.
- Не расширять работу, если модуль закончился дешевле.
- При превышении накопленного бюджета более чем на 5 пунктов следующий модуль Terra запускается на один уровень reasoning ниже.
- Модули 0, 1, 6, 9 и 13 нельзя переводить на Luna.
- Модуль 13 включает резерв на тестирование, обновление журнала и финальный ответ.
- Не следует специально расходовать остаток ради достижения ровно 100%.
- Если до модуля 13 уже израсходовано 95%, новые изменения прекращаются: остаются только критические проверки и обновление состояния.
- Если модуль не завершён в пределах своего бюджета, он получает `blocked`; следующий модуль не начинается.

### Бюджет

| № | Модуль | Модель | Effort | Доля | Накопительно |
|---:|---|---|---|---:|---:|
| 0 | Документы и контракты | GPT-5.6 Sol | high | 5% | 5% |
| 1 | Строгая модель выпуска и QA-баги | GPT-5.6 Sol | high | 9% | 14% |
| 2 | Application facade и worker | GPT-5.6 Terra | high | 8% | 22% |
| 3 | Gemini и Credential Manager | GPT-5.6 Terra | medium | 6% | 28% |
| 4 | Источники и научные API | GPT-5.6 Terra | high | 7% | 35% |
| 5 | Анализ исходного кода | GPT-5.6 Terra | high | 7% | 42% |
| 6 | РСБУ, финансы и моделируемые данные | GPT-5.6 Sol | high | 11% | 53% |
| 7 | Профили и автоматическое написание | GPT-5.6 Terra | high | 7% | 60% |
| 8 | Графики, диаграммы и изображения | GPT-5.6 Terra | medium | 6% | 66% |
| 9 | DOCX, LibreOffice и release-QA | GPT-5.6 Sol | high | 11% | 77% |
| 10 | Новый дизайн и оболочка UI | GPT-5.6 Terra | high | 7% | 84% |
| 11 | Пользовательские сценарии и редактор | GPT-5.6 Terra | high | 8% | 92% |
| 12 | Windows-установщик | GPT-5.6 Luna | medium | 3% | 95% |
| 13 | Финальная интеграция и релиз | GPT-5.6 Sol | high | 5% | 100% |

Этот одновоконный план сознательно не включает полное физическое разнесение всех монолитов. Существующие надёжные подсистемы оборачиваются новыми интерфейсами, критические части выделяются постепенно, UI заменяется полностью. Глубокая архитектурная чистка после MVP вынесена за границы плана.

## 2. Правила работы между чатами

Перед любым модулем агент обязан:

1. Прочитать `PROJECT_STATE.md`.
2. Прочитать относящийся к модулю раздел `docs/API_CONTRACT.md`.
3. Прочитать относящийся к модулю раздел `docs/DATA_MODEL.md`.
4. Выполнить `git status --short`.
5. Изучить только затрагиваемые модулем файлы.
6. Убедиться, что зависимости отмечены как `completed`.
7. Проверить текущий накопленный бюджет Usage, если это контрольная точка.

Не нужно перечитывать:

- весь `PLAN.md`;
- архивные отчёты;
- полные логи прошлых модулей;
- legacy-каталоги, если модуль их не затрагивает;
- исходники подсистем, не входящих в модуль.

После завершения агент обязан:

1. Запустить только проверки модуля.
2. Просмотреть `git diff --stat` и проверить отсутствие случайных изменений.
3. Обновить `PROJECT_STATE.md`.
4. Записать изменённые файлы, решения, тесты и ограничения.
5. Отметить модуль как `completed` только при выполнении всех критериев.
6. Не начинать следующий модуль.
7. Ограничить финальный ответ 10–15 строками.

Публичный контракт нельзя менять молча. Сначала обновляются контракт, его версия и `PROJECT_STATE.md`, затем код и тесты.

### Стандартный запрос нового чата

> Реализуй только модуль №N из плана PaperCraft AI Studio. Используй модель и reasoning effort, указанные для этого модуля. Сначала прочитай `PROJECT_STATE.md`, соответствующие разделы `docs/API_CONTRACT.md` и `docs/DATA_MODEL.md`, проверь `git status --short` и готовность зависимостей. Изучай только относящиеся к модулю файлы. Не проводи повторный аудит, не расширяй MVP, не переделывай завершённые модули и не запускай полный набор тестов, если он не указан в критериях. После реализации выполни проверки модуля, просмотри diff и обнови `PROJECT_STATE.md`: статус, изменённые файлы, решения, тесты, ограничения, фактический расход Usage и следующий модуль. На этом остановись.

## 3. `PROJECT_STATE.md`

Модуль 0 создаёт файл короче 250 строк:

```markdown
# PaperCraft AI Studio Project State

## Current snapshot
- Version:
- Current module:
- Last updated:
- Application status:
- Working tree:
- Known blockers:

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
- Window started:
- Initial used percent:
- Planned cumulative percent:
- Actual used percent:
- Remaining reserve:
- Budget status: on_track / over_budget / exhausted

## Module status
| № | Module | Status | Model | Effort | Budget | Tests | Notes |
|---|---|---|---|---|---:|---|---|

Statuses: pending / in_progress / completed / blocked

## Current interfaces
- Application API version:
- Worker protocol version:
- Database schema version:
- Profile schema version:
- Release policy version:
- Settings format version:
- Build version:

## Last completed module
- Module:
- Result:
- Files changed:
- Decisions:
- Tests:
- Known limitations:

## Next module
- Module:
- Required inputs:
- Entry conditions:
- Expected result:

## Project history
- YYYY-MM-DD — Module N completed: one-line result
```

Правила экономии:

- Детали хранить только для последнего модуля.
- Для старых модулей оставлять одну строку истории.
- Не вставлять код, diff или полные логи.
- Не повторять `Fixed decisions`.
- Длинные отчёты тестов хранить как артефакты, указывая только путь и итог.
- Если решение уже есть в API или Data Model, в журнале хранить только ссылку на раздел.

## 4. Зафиксированный MVP

Главный сценарий:

1. Пользователь один раз вводит Gemini API-ключ.
2. Создаёт курсовую, научную статью, отчёт по практике или школьный проект.
3. Заполняет тему, реквизиты и требования.
4. При необходимости добавляет файлы, ссылки, DOI, таблицы или папку исходного кода.
5. Нажимает `Создать и запустить`.
6. План формируется и выполняется автоматически.
7. Приложение исследует тему, пишет разделы, выполняет расчёты, создаёт визуализации и DOCX.
8. Автоматический QA исправляет найденные проблемы.
9. Пользователь получает `READY_TO_SUBMIT` и итоговый DOCX.

Пользовательский экспорт — только DOCX. Внутренние PDF/PNG используются для preview и QA.

Статусы:

- `DRAFT`;
- `RUNNING`;
- `WAITING_PROVIDER`;
- `WAITING_INPUT`;
- `QUALITY_FAILED`;
- `READY_TO_SUBMIT`;
- `SUPERSEDED`;
- `CANCELLED`.

Статус «Готово к сдаче» означает прохождение внутренних автоматических проверок текущей ревизии. Он не гарантирует оценку преподавателя или результат внешнего антиплагиата.

Моделируемые данные допускаются как воспроизводимый расчётный сценарий. Они не могут приписываться реальным наблюдениям или реальной организации. Для научной эмпирической статьи отсутствие наблюдений приводит к переходу в теоретический/обзорный формат либо `WAITING_INPUT`.

## 5. Application API MVP

Публичной границей локального приложения является `DesktopApplication`.

### Credentials

- `credential_status() -> CredentialStatus`
- `configure_gemini(api_key) -> CredentialStatus`
- `verify_gemini() -> ProviderCheck`
- `delete_gemini_key() -> None`

`CredentialStatus`:

- `configured`
- `verified`
- `state: missing | valid | unverified | invalid`
- `last_checked_at`
- `safe_message`

Ключ никогда не возвращается из API.

### Projects

- `list_projects(filter, sort) -> list[ProjectSummary]`
- `get_project(project_id) -> ProjectWorkspaceView`
- `create_project(ProjectDraft) -> ProjectView`
- `update_project(project_id, ProjectPatch) -> ProjectView`
- `archive_project(project_id) -> None`
- `restore_project(project_id) -> ProjectView`

### Sources

- `import_source(project_id, SourceInput) -> SourceView`
- `remove_source(project_id, source_id) -> None`
- `reclassify_source(project_id, source_id, role) -> SourceView`
- `list_sources(project_id) -> list[SourceView]`

### Generation

- `start_generation(project_id) -> RunSnapshot`
- `pause_generation(run_id) -> RunSnapshot`
- `resume_generation(run_id) -> RunSnapshot`
- `cancel_generation(run_id) -> RunSnapshot`
- `retry_generation(run_id) -> RunSnapshot`
- `get_run_snapshot(run_id) -> RunSnapshot`

### Editing and result

- `get_submission_result(project_id) -> SubmissionResult`
- `update_text_section(section_id, text) -> SectionRevision`
- `regenerate_section(section_id, instruction) -> RunSnapshot`
- `export_ready_docx(release_id, destination) -> ExportResult`
- `create_backup(project_id, destination) -> BackupResult`
- `restore_backup(archive) -> ProjectView`

### Worker protocol

`WorkerRequest`:

- `protocol_version`
- `request_id`
- `action`
- `project_id`
- `run_id`
- `stage_id`
- `section_id`

`WorkerEvent`:

- `protocol_version`
- `request_id`
- `project_id`
- `run_id`
- `sequence`
- `timestamp`
- `event_type`
- `stage`
- `status`
- `progress`
- `message`
- `error_code`
- `retry_at`
- `estimated_cost`

Секреты, prompts и содержимое документов не передаются через argv, stdout или события.

## 6. Модули

### Модуль 0. Документы и заморозка контрактов  
**GPT-5.6 Sol — high — 5%**

**Назначение:** создать компактный источник истины для остальных чатов.

**Файлы:**

- `PLAN.md`
- `PROJECT_STATE.md`
- `docs/API_CONTRACT.md`
- `docs/DATA_MODEL.md`

**Работа:**

- Перенести этот модульный план в `PLAN.md`.
- Создать постоянную структуру журнала.
- Зафиксировать Application API и JSONL worker protocol.
- Описать сущности, поля, связи, хранение и жизненные циклы.
- Установить версии: Application API `1`, worker `1`, database `5`, profiles `1`, release policy `1`, settings `1`.
- Создать таблицу модулей со статусом `pending`.

**Проверки:**

- Автоматические: Markdown links, отсутствие незаполненных `TBD`, совпадение статусов и номеров модулей.
- Ручные: другой агент может понять модуль 1 без чтения истории чата.

**Риск:** избыточная документация. Каждый файл должен описывать только свою область.

**Критерий готовности:** все контракты однозначны, модуль 0 отмечен `completed`, production-код не изменён.

---

### Модуль 1. Строгая модель выпуска и критические QA-исправления  
**GPT-5.6 Sol — high — 9%**

**Назначение:** исключить ложный статус успешной работы до расширения функций.

**Входные условия:** модуль 0 завершён.

**Компоненты:**

- `application/stages.py`
- `infrastructure/qa/gates.py`
- модели QA/release;
- сервис открытия и экспорта документов;
- соответствующие тесты.

**Работа:**

- Ввести `SubmissionRelease`.
- Передавать WorkProfile во все QA-контексты.
- Исправить чтение `profile.policy.minimum_sources`.
- Запретить implicit `accepted=True`.
- Учитывать `accepted`, factual issues и blocker issues модельных review.
- Запретить принятие раздела после исчерпания repair-loop.
- Связать `RunStatus.SUCCEEDED` только с `READY_TO_SUBMIT`.
- Закрыть обход release-gate через `open_in_word`.
- Сбрасывать READY после изменения исходников, профиля, раздела или модели.

**Проверки:**

- Автоматические: `accepted=false`, отсутствующее поле, minimum sources, QA FAIL/WARNING, устаревший hash, обход открытия.
- Ручные: документ с искусственно внесённой ошибкой не экспортируется.

**Риск:** существующие успешные проекты станут draft. Это ожидаемая миграция качества.

**Критерий готовности:** DOCX существует отдельно от статуса READY; fail-open путей нет.

---

### Модуль 2. Application facade и надёжный worker  
**GPT-5.6 Terra — high — 8%**

**Назначение:** отделить новый UI от SQLite, файлов и pipeline.

**Входные условия:** модули 0–1 завершены.

**Компоненты:**

- `application/api/*`
- `application/runtime.py`
- `worker/commands.py`
- `worker/cli.py`
- compatibility adapters.

**Работа:**

- Реализовать команды и query из Application API.
- Ввести версионированные DTO.
- Обновить JSONL worker protocol.
- Сделать `request_id` идемпотентным.
- Разделить пользовательские события и диагностический stderr.
- Сохранить существующий pipeline через compatibility facade.
- Обеспечить pause/resume/cancel/crash recovery.

**Проверки:**

- Автоматические: duplicate request, kill/resume, pause/cancel, corrupted event, sequence ordering.
- Ручные: завершение worker во время генерации и продолжение после запуска.

**Риск:** двойное сохранение артефактов. Записи фиксируются по stage/item idempotency key.

**Критерий готовности:** UI может использовать только `DesktopApplication`.

---

### Модуль 3. Gemini и Windows Credential Manager  
**GPT-5.6 Terra — medium — 6%**

**Назначение:** обеспечить однократный ввод Gemini-ключа.

**Входные условия:** модуль 2 завершён.

**Компоненты:**

- credential service;
- Gemini coordinator;
- model capability registry;
- конфигурация provider policy;
- credential-тесты.

**Работа:**

- Хранить ключ только в Windows Credential Manager.
- Проверять ключ при первом вводе.
- Реализовать статус, замену и удаление.
- Исключить ключ из БД, логов, ошибок, argv и worker events.
- Задать primary/fallback модели отдельно для text, structured output, vision и image generation.
- Переключаться только при retryable/model compatibility ошибках.
- При quota/network переходить в `WAITING_PROVIDER`, сохраняя ключ.
- При перезапуске продолжать ожидание автоматически.

**Проверки:**

- Автоматические: fake keyring, invalid auth, quota, timeout, fallback, secret scanning.
- Ручные: ввод → закрытие → запуск → генерация без повторного запроса.

**Риск:** ключ отозван пользователем. Повторный ввод допускается только после подтверждённого `AUTH_INVALID`.

**Критерий готовности:** ключ переживает перезапуск и никогда не появляется в открытом виде.

---

### Модуль 4. Источники, научные статьи и scholarly API  
**GPT-5.6 Terra — high — 7%**

**Назначение:** формировать доказательную базу без выдуманных источников.

**Входные условия:** модули 1–3 завершены.

**Компоненты:**

- `infrastructure/research/*`
- source/evidence repositories;
- research pipeline stage;
- библиография.

**Работа:**

- Сохранить Crossref, OpenAlex и DOI.
- Исправить deduplication и минимумы источников.
- Создавать immutable snapshot и locator.
- Выделять claims не только из research plan, но и из финального текста.
- Связывать citation с claim и evidence.
- Проверять DOI, URL, автора, год и название.
- Для научной статьи поддержать RU/EN название, аннотацию и ключевые слова.
- Не считать metadata API доказательством содержания статьи.

**Проверки:**

- Автоматические: ложный DOI, подменённый snapshot, citation без evidence, дубликаты, API outage.
- Ручные: научная статья с реальными DOI и проверяемой библиографией.

**Риск:** внешний API недоступен. Выпуск разрешён только при достаточном уже проверенном наборе.

**Критерий готовности:** каждое фактическое утверждение имеет проверяемую lineage.

---

### Модуль 5. Статический анализ исходного кода  
**GPT-5.6 Terra — high — 7%**

**Назначение:** добавить доказуемый анализ учебных программных проектов.

**Входные условия:** модули 1, 2 и 4 завершены.

**Компоненты:**

- `domain/code.py`
- code analysis service;
- parser adapters;
- source-code test corpus.

**Работа:**

- Использовать Python AST для Python.
- Использовать закреплённые Tree-sitter grammars для JS, TS, Java, C, C++ и C#.
- Извлекать модули, символы, импорты, зависимости, entrypoints, тесты и endpoints.
- Сохранять file, line range и source hash.
- Выявлять базовые code smells и структурные риски.
- Не запускать импортированный код.
- Разрешать runtime-утверждения только по приложенным логам или test reports.

**Проверки:**

- Автоматические: corpus каждого языка, неправильная строка, отсутствующий символ, syntax error, секрет, prompt injection в комментарии.
- Ручные: mixed-language проект с переходом из DOCX к исходному файлу.

**Риск:** парсер не понимает нестандартный синтаксис. Такой файл получает fallback и пониженную confidence.

**Критерий готовности:** листинги и выводы имеют точные locators и совпадают с originals.

---

### Модуль 6. РСБУ, финансовые расчёты и моделируемые данные  
**GPT-5.6 Sol — high — 11%**

**Назначение:** исключить расчёты Gemini и сделать финансовые результаты воспроизводимыми.

**Входные условия:** модули 1, 2 и 4 завершены.

**Компоненты:**

- `infrastructure/calculations/*`
- `financial_catalog.py`
- модели dataset/calculation/accounting;
- finance/accounting profiles.

**Работа:**

- Использовать Decimal, явные единицы, валюту, период и округление.
- Добавить версионированный справочник счетов РСБУ.
- Поддержать простые и составные проводки.
- Рассчитывать обороты, остатки и ОСВ.
- Добавить горизонтальный/вертикальный анализ.
- Добавить ликвидность, устойчивость, рентабельность и оборачиваемость.
- Добавить break-even и маржинальные показатели.
- Добавить NPV, IRR, PI, PP, DPP.
- Добавить аннуитетный и дифференцированный кредитный график.
- Формировать `CalculationSpec` и `CalculationResult`.
- Генерировать воспроизводимые модельные сценарии из seed и ограничений.
- Не приписывать модельные значения реальной организации или наблюдению.

**Проверки:**

- Автоматические: oracle values, property-based invariants, деление на ноль, неизвестный счёт, смешанные валюты/периоды, несбалансированная ОСВ.
- Ручные: сверка эталонной бухгалтерской и инвестиционной задачи.

**Риск:** устаревание нормативной базы. Справочник содержит версию и дату действия.

**Критерий готовности:** каждое число в документе можно независимо пересчитать из сохранённых входов.

---

### Модуль 7. Профили работ и автоматическое написание  
**GPT-5.6 Terra — high — 7%**

**Назначение:** реализовать полный autopilot без подтверждения плана.

**Входные условия:** модули 1–6 завершены.

**Компоненты:**

- profile models;
- prompt contracts;
- blueprint/writing stages;
- manuscript revisions.

**Работа:**

- Зафиксировать четыре профиля работы.
- Выделять требования из задания и методички.
- Генерировать blueprint и сразу запускать написание.
- Передавать разделу только относящиеся к нему evidence, code findings и calculations.
- Сохранять typed content blocks вместо неструктурированного текста.
- Добавить bounded repair.
- Поддержать изменение обычного текстового раздела.
- Для смешанного раздела использовать регенерацию по инструкции.

**Проверки:**

- Автоматические: profile snapshots, обязательные разделы, отсутствие approval checkpoint, invalid structured output, repair limit.
- Ручные: по одному проекту каждого профиля.

**Риск:** слишком большой prompt context. Контекст строится отдельно для каждого раздела.

**Критерий готовности:** одна команда создаёт полную рукопись без промежуточного подтверждения.

---

### Модуль 8. Графики, диаграммы и генерация изображений  
**GPT-5.6 Terra — medium — 6%**

**Назначение:** добавить все согласованные визуальные материалы.

**Входные условия:** модули 3, 5–7 завершены.

**Компоненты:**

- visual asset models;
- chart renderer;
- diagram renderer;
- Gemini image adapter.

**Работа:**

- Создавать графики локально из Dataset.
- Добавить typed `DiagramSpec` с nodes и edges.
- Рендерить диаграммы безопасно в SVG/PNG без активного содержимого.
- Генерировать иллюстрации через Gemini.
- Сохранять prompt, модель, hash, caption и alt text.
- Не использовать AI-изображение как фактическое доказательство.
- Встраивать доступную таблицу или текстовое резюме графика.

**Проверки:**

- Автоматические: точки графика, подписи, единицы, DPI, nodes/edges, повреждённое изображение.
- Ручные: визуальная проверка смешанной страницы DOCX.

**Риск:** нерелевантное AI-изображение. После одной регенерации неудача блокирует только необязательную иллюстрацию, но не весь текст.

**Критерий готовности:** визуалы воспроизводимы, подписаны и корректно связаны с данными.

---

### Модуль 9. DOCX, LibreOffice и release-QA  
**GPT-5.6 Sol — high — 11%**

**Назначение:** выпускать один проверенный DOCX.

**Входные условия:** модули 1 и 4–8 завершены.

**Компоненты:**

- DOCX renderer;
- styles/templates/fields;
- LibreOffice finalizer;
- document QA;
- release service.

**Работа:**

- Разделить draft и final artifacts.
- Обновлять TOC, PAGE, SEQ и REF через bundled LibreOffice.
- Проверять OpenXML, стили, поля, таблицы, изображения и библиографию.
- Рендерить внутренний PDF/PNG для постраничного QA.
- Проверять overflow, пустые страницы, orphan headings и обрезанные подписи.
- Запретить macros и внешние активные ссылки.
- Связать QA с точными хешами input, manuscript и DOCX.
- Разрешить экспорт только для `READY_TO_SUBMIT`.
- Удалить пользовательский PDF-экспорт.

**Проверки:**

- Автоматические: corrupt ZIP, placeholders, поля, ссылки, overflow, stale revision.
- Ручные: открытие без repair dialog в Word и bundled LibreOffice.

**Риск:** различия Word и LibreOffice. Release fixtures проверяются в обеих программах.

**Критерий готовности:** финальный DOCX открывается без восстановления и соответствует текущей прошедшей QA-ревизии.

---

### Модуль 10. Новый дизайн и UI-оболочка  
**GPT-5.6 Terra — high — 7%**

**Назначение:** полностью заменить текущий визуальный слой.

**Входные условия:** стабильный Application API модуля 2.

**Компоненты:**

- `ui/theme/*`
- `ui/components/*`
- shell/navigation;
- базовые экраны.

**Работа:**

- Удалить постоянный пошаговый sidebar.
- Реализовать верхнюю панель и горизонтальную навигацию проекта.
- Создать светлую academic-editorial тему.
- Использовать Literata, Golos Text и Cascadia Mono.
- Применить canvas `#F5F1E8`, paper `#FFFDF8`, ink `#1F2521`, accent `#145C55`.
- Добавить focus states, reduced motion и токены размеров.
- Создать оболочки библиотеки, мастера, материалов, генерации, документа и настроек.
- Удалить фиолетовые градиенты, dashboard-карточки и AI-sparkle метафоры.

**Проверки:**

- Автоматические: screenshot smoke, component states, контраст токенов.
- Ручные: 1366×768, 1920×1080 и Windows scaling 100–200%.

**Риск:** смешение старого и нового UI. Новый shell не импортирует старые page widgets.

**Критерий готовности:** все маршруты используют единую новую визуальную систему.

---

### Модуль 11. Пользовательские сценарии, preview и редактор  
**GPT-5.6 Terra — high — 8%**

**Назначение:** соединить новый UI с рабочими сервисами.

**Входные условия:** модули 2–10 завершены.

**Компоненты:**

- onboarding;
- project library;
- project wizard;
- materials;
- generation monitor;
- document workspace;
- settings.

**Работа:**

- Реализовать первый запуск и настройку ключа.
- Добавить поиск, фильтры, архивирование и восстановление проектов.
- Создать мастер `Тип → Задание → Материалы → Реквизиты`.
- Завершать мастер кнопкой `Создать и запустить`.
- Отображать прогресс человеческими фазами.
- Добавить pause/resume/cancel.
- Реализовать трёхзонный документ: структура, preview, QA/evidence.
- Добавить редактирование текста и регенерацию сложного раздела.
- После изменения автоматически сбрасывать release и повторять QA.
- Добавить test/replace/delete Gemini key.
- Обеспечить клавиатурную навигацию и NVDA labels.

**Проверки:**

- Автоматические: pytest-qt, state routing, form validation, double-click protection, editor invalidation.
- Ручные: полный сценарий мышью и клавиатурой.

**Риск:** UI зависает на тяжёлой операции. Все операции выполняются через worker/events.

**Критерий готовности:** новый пользователь проходит весь сценарий без терминала и технического журнала.

---

### Модуль 12. Windows-установщик  
**GPT-5.6 Luna — medium — 3%**

**Назначение:** подготовить переносимую beta-версию.

**Входные условия:** модууль 11 завершён.

**Компоненты:**

- PyInstaller configuration;
- Inno Setup;
- version metadata;
- bundled fonts и LibreOffice.

**Работа:**

- Собрать приложение без консольного окна.
- Создать per-user installer без admin.
- Устанавливать в `%LOCALAPPDATA%\Programs\PaperCraft`.
- Сохранять проекты и Credential Manager при обновлении.
- Добавить ярлык в меню «Пуск».
- При удалении отдельно предлагать удалить пользовательские данные.
- Не включать ключ и пользовательские проекты в installer.
- Зафиксировать ручное обновление и unsigned beta.

**Проверки:**

- Автоматические: packaged import/start smoke.
- Ручные: install, update и uninstall на чистом Windows-профиле.

**Риск:** размер LibreOffice. Используется минимально необходимый portable runtime.

**Критерий готовности:** приложение запускается без установленного Python и LibreOffice.

---

### Модуль 13. Финальная интеграция и выпуск MVP  
**GPT-5.6 Sol — high — 5%**

**Назначение:** проверить продукт целиком и закрыть проектный журнал.

**Входные условия:** модули 0–12 завершены, накопленный расход не выше 95%.

**Компоненты:**

- полный test suite;
- golden projects;
- release checklist;
- `PROJECT_STATE.md`.

**Работа:**

- Запустить Ruff, strict MyPy и полный pytest.
- Выполнить deterministic golden-сценарии:
  - общая курсовая;
  - научная статья;
  - отчёт по практике;
  - школьный проект;
  - анализ кода;
  - РСБУ и финансовые расчёты;
  - графики, диаграмма и AI-изображение.
- Проверить негативные сценарии release-gate.
- Выполнить live Gemini smoke при наличии ключа.
- Выполнить LibreOffice/Word smoke.
- Проверить установщик и восстановление после перезапуска.
- Удалить только доказанно неиспользуемые compatibility imports.
- Обновить `PROJECT_STATE.md` до `MVP ready`.

**Проверки:**

- Автоматические: полный suite без ошибок и ResourceWarning.
- Ручные: install → key → project → generation → READY → DOCX → restart.

**Риск:** исправления финального теста разрастаются. Разрешены только blocker/regression fixes; улучшения записываются в backlog.

**Критерий готовности:**

- все 14 модулей имеют `completed`;
- обязательные тесты зелёные;
- секретов в репозитории и логах нет;
- ни один QA FAIL не выпускает DOCX как готовый;
- ключ не запрашивается повторно;
- хотя бы один live проект доходит до `READY_TO_SUBMIT`;
- итоговый DOCX открывается без восстановления;
- `PROJECT_STATE.md` содержит `Application status: MVP ready`.

## 7. Границы одновоконного плана

В MVP входят:

- четыре типа работ;
- полностью новый UI;
- Gemini с одним сохранённым ключом;
- научные источники;
- статический анализ популярных языков;
- РСБУ и полный учебный набор финансовых формул;
- расчётные сценарии;
- графики, диаграммы и изображения;
- автоматическое написание без подтверждения плана;
- section editor и preview;
- DOCX со строгим автоматическим release-QA;
- Windows installer.

За пределами окна остаются:

- полное переписывание SQLite repository;
- полное удаление всех compatibility facade;
- отдельные профили диплома, диссертации и лабораторной;
- SaaS, аккаунты и cloud sync;
- внешний антиплагиат;
- PDF-экспорт;
- полноценный Word-подобный редактор;
- МСФО и налоговая отчётность;
- автоматическое обновление;
- цифровая подпись установщика;
- выполнение пользовательского кода;
- расширенная телеметрия и analytics.

Распределение моделей основано на OpenAI Docs: Sol оставлен для контрактов, финансовой корректности и release-gates; Terra выполняет основную инженерную и интерфейсную работу; Luna используется только для механической упаковки. Такой выбор сохраняет качество критических модулей и уменьшает расход пятичасового окна.
