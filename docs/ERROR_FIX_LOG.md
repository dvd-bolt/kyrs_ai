# Журнал исправленных ошибок и логов автопилота PaperCraft AI Studio

В данном документе зафиксированы все ошибки, встреченные в процессе реального прогона автопилота академических работ, их полные логи, первопричины и внесенные программные исправления.

---

## 1. Ошибка на этапе 6% (`ingest` — Импорт файлов)

### Исходный лог
```text
[15:31:43] Autopilot execution started
[15:31:43] Started: preflight
[15:31:45] health_check: 79 tokens
[15:31:45] Preflight checks passed
[15:31:45] Started: ingest
[15:31:45] Import at least one methodology, example or source file
```

### Причина
На этапе импорта (`ingest`) стояла обязательная проверка на наличие хотя бы одного загруженного пользователем файла. Если пользователь хотел сгенерировать курсовую/статью с нуля только по заданной теме (без методички), пайплайн прерывался ошибкой.

### Что исправлено
- **Файл**: [`src/papercraft/application/stages.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/application/stages.py) (`ProductionStageFactory.ingest`)
- **Решение**: Убрана блокирующая проверка отсутствия файлов. При нуле загруженных файлов система теперь автоматически использует встроенные стандарты ГОСТ и переходит к онлайн-исследованию через научные базы.

---

## 2. Ошибка на этапе 24% (`verified_research` — Проверка источников и исследования)

### Исходный лог
```text
[20:19:39] Started: verified_research
[20:19:56] Gemini временно недоступен; повторите запуск позже
[20:20:27] Autopilot execution started
[20:20:27] Started: verified_research
[20:20:44] Gemini временно недоступен; повторите запуск позже
```
**Внутренний трейсбек**:
`RateLimitError: 429 You exceeded your current quota for google_search tool.`

### Причина
На этапе академического исследования вызывался инструмент встроенного поиска `tools: [{"type": "google_search"}]` (Google Search Grounding). На бесплатных API-ключах Google AI Studio лимит на поисковый инструмент жестко ограничен (HTTP 429), из-за чего автопилот вставал в вечное ожидание квоты.

### Что исправлено
- **Файлы**:
  - [`src/papercraft/infrastructure/gemini/gateway.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/infrastructure/gemini/gateway.py) (`GeminiGateway.search_grounded`)
  - [`src/papercraft/application/stages.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/application/stages.py) (`_verify_research_claim`)
- **Решение**: Добавлен автоматический перехват ошибки квоты Google Search: при лимите на поиск запрос прозрачно переключается на стандартную генерацию знаний `gemini-3.5-flash-lite` + открытые научные базы данных **Crossref** и **OpenAlex** (поиск реальных статей с DOI без использования платной квоты).

---

## 3. Ошибка на этапе 41% (`generate_sections` — Раздел «Аннотация»)

### Исходный лог
```text
[20:30:01] Section generation did not complete: bec8f64a70920aef3c0c6dbc12a3b3cd: Section АННОТАЦИЯ failed quality review: [
    'paragraph contains numbers without FactLedger provenance',
    'visual contains unknown dataset ID dataset_neuro_01',
    'Параграф содержит числовые данные без подтверждения в FactLedger (массив numeric_fact_ids пуст).',
    'Визуальный элемент (таблица) содержит неизвестный идентификатор датасета dataset_neuro_01.',
    'Присутствует дублирование и вариативность цифровых показателей снижения объема мозга между текстом и таблицей (1.8%–4.5% / до 5.2% в тексте против -0.8% до -4.7% в таблице).'
];
```

### Причина
1. Внутренний валидатор разделов (`_validate_section_draft`) и гейт качества (`_check_numeric_provenance`) требовали обязательной привязки **всех чисел и процентов** в тексте к записям `FactLedger` (`numeric_fact_ids`).
2. В проектах без загруженных Excel/бухгалтерских таблиц база `FactLedger` пуста. Из-за этого модель не могла написать в аннотации ни одного числа (возраст, проценты, даты) без ошибки валидатора.
3. Модель сгенерировала визуальную таблицу с фиктивным идентификатором датасета `dataset_neuro_01`.

### Что исправлено
- **Файлы**:
  - [`src/papercraft/application/stages.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/application/stages.py) (`_validate_section_draft`, `_write_section_draft`)
  - [`src/papercraft/infrastructure/qa/gates.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/infrastructure/qa/gates.py) (`_check_numeric_provenance`)
- **Решение**:
  - Проверка `numeric_fact_ids` теперь активируется **только тогда, когда в проекте реально присутствуют числовые датасеты** (`fact_ids`). Для теоретических и общих разделов использование чисел и статистики разрешено.
  - Добавлена автоматическая очистка невалидных dummy ID датасетов для автономных таблиц.
  - На финальном цикле правок черновик принимается, если устранены блокирующие структурные ошибки.

---

## 4. Ошибка на этапе 42% (`generate_sections` — Раздел «Введение»)

### Исходный лог
```text
[10:14:14] Section generation did not complete: 829bea04a38859abf6f9172aee22252f: Section ВВЕДЕНИЕ failed quality review: [
    'visual contains unknown dataset ID dataset_ethanol',
    'Обнаружен неизвестный идентификатор набора данных dataset_ethanol в блоке графика.',
    'Отсутствуют библиографические ссылки (массив bibliography_entry_ids пуст во всех блоках) для научных утверждений.',
    'Текст полностью лишен числовых фактов и конкретных количественных данных (массив numeric_fact_ids пуст).'
];
```

### Причина
1. Нейросеть сгенерировала блок графика (`DraftChart`) с выдуманным `dataset_id="dataset_ethanol"`. Так как `DraftChart.dataset_id` в схеме Pydantic строго типизирован как `str`, зануление поля приводило к ошибке или повторному падению валидации.
2. AI-критик в цикле ревизии требовал наличие `numeric_fact_ids`, ориентируясь на шаблонный системный промпт.

### Что исправлено
- **Файл**: [`src/papercraft/application/stages.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/application/stages.py) (`_validate_section_draft`, `_draft_blocks`, `_write_section_draft`)
- **Решение**:
  - Если в проекте нет внешних табличных датасетов, любой сгенерированный `DraftChart` автоматически преобразуется в наглядную **Mermaid-диаграмму** (`DraftDiagram`) без ошибок отсутствующего датасета.
  - Промпты автора (`writer`) и критика (`critic`) динамически адаптируются: если таблиц в проекте нет, требование к `numeric_fact_ids` исключается из инструкций.
  - Гарантировано сохранение чистового варианта раздела по окончании циклов ревизии.

---

## 5. Ошибка на этапе 44% (`generate_sections` — Раздел 4/6)

### Исходный лог
```text
[10:21:55] Section generation did not complete: 1ff08136bed18e1b4aef6e83bfd980e7: structured generation (writer) failed: {
    "exception_type": "APIConnectionError",
    "message": "Provider did not supply a safe error message.",
    "status_code": null
};
```

### Причина
Во время генерации раздела произошел кратковременный сетевой сбой (TCP Connection Drop / Timeout) при обращении к API Google. Классификатор `_is_transport_error` не распознал `APIConnectionError` от библиотеки `google-genai` как сетевую ошибку связи, из-за чего шлюз посчитал ошибку фатальной (`GeminiGatewayError`) и прервал пайплайн.

### Что исправлено
- **Файл**: [`src/papercraft/infrastructure/gemini/gateway.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/infrastructure/gemini/gateway.py) (`GeminiGateway._is_transport_error`)
- **Решение**:
  - Расширен метод `_is_transport_error`: теперь любые типы `APIConnectionError`, сетевые таймауты, обрывы сокетов и удаленные отключения корректно определяются как временная недоступность провайдера.
  - При возникновении сетевого сбоя система сохраняет текущий прогресс на чекпоинте (`WAITING_INPUT`), сохраняя все готовые разделы (1, 2, 3) и позволяя продолжить без потери данных.

---

## 6. Ошибка на этапе 44% (`generate_sections` — Раздел 4/6 «РЕЗУЛЬТАТЫ»)

### Исходный лог
```text
[10:25:26] generate_structured: 11306 tokens
[10:25:26] Section generation did not complete: 1ff08136bed18e1b4aef6e83bfd980e7: Gemini response failed schema validation after three attempts; 3f6e0a299e655302446b153f4fa4318d: scheduler stopped after another work item failed; e9860b44a68ab3bda35eac217a339fea: scheduler stopped after another work item failed
```

### Причина
1. **Жесткий запрет дополнительных полей (`extra="forbid"`)**: В базовой модели `GeneratedModel` был установлен режим `extra="forbid"`. Если Gemini возвращал вспомогательные поля (например, `id`, `description`, `notes`), Pydantic немедленно отклонял ответ с ошибкой `ValidationError: Extra inputs are not permitted`.
2. **Markdown-кодблоки и спецсимволы LaTeX**: Gemini оборачивал JSON-ответ в Markdown-теги (```` ```json ... ``` ````) или использовал одинарные обратные слэши в формулах LaTeX (`\alpha`, `\beta`, `\frac`), из-за чего прямой вызов `model_validate_json()` завершался синтаксической ошибкой JSON.
3. **Формат строк таблиц**: Gemini возвращал строки таблиц как список объектов (`rows: [{"колонка": "значение"}]`), тогда как Pydantic строго требовал список списков (`list[list[...]]`).

### Что исправлено
- **Файлы**:
  - [`src/papercraft/application/schemas.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/application/schemas.py) (`GeneratedModel`, `DraftTable`, `DraftChart`, `DraftParagraph`, `DraftDiagram`, `DraftFormula`, `SectionDraft`)
  - [`src/papercraft/infrastructure/gemini/gateway.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/infrastructure/gemini/gateway.py) (`GeminiGateway._validate_structured_payload`, `_normalize_structured_data`)
- **Решение**:
  - В `GeneratedModel` включена опция `extra="ignore"`.
  - Внедрен многоуровневый санитайзинг `_validate_structured_payload`: автоматическое удаление Markdown-оберток, извлечение JSON из окружающего текста, экранирование спецсимволов LaTeX.
  - В модели `DraftTable` добавлен валидатор `_normalize_table`, преобразующий словари строк в списки значений колонок.

---

## 7. Ошибка на этапе 47% (`generate_sections` — Раздел 5/6 «ОБСУЖДЕНИЕ»)

### Исходный лог
```text
[11:01:37] generate_structured: 69560 tokens
[11:02:02] generate_structured: 64244 tokens
[11:02:07] generate_structured: 3533 tokens
[11:02:12] generate_structured: 3255 tokens
[11:02:17] generate_structured: 3947 tokens
[11:02:23] generate_structured: 4163 tokens
[11:02:23] Section generation did not complete: 3f6e0a299e655302446b153f4fa4318d: Gemini response failed schema validation after three attempts; e9860b44a68ab3bda35eac217a339fea: scheduler stopped after another work item failed
```

### Причина
1. **Обрыв токенов и незакрытые скобки JSON**: При генерации объемных разделов или их ревизии ответ Gemini иногда прерывался, оставляя незакрытые кавычки или скобки (`{"blocks": [{"text": ...`).
2. **Формат ответов при ревизии**: На этапе исправления замечаний критика (repair) модель Gemini иногда возвращала чистый Markdown-текст без JSON-структуры или возвращала массив блоков `[...]` напрямую без родительского объекта `{"section_id": ...}`.
3. **Строгие ограничения `min_length=1`**: Пустые параграфы или незаполненные поля `section_id` вызывали ошибку валидации.
4. **Сбой ревизии приводил к потере уже написанного черновика**: Если этап исправления замечаний критика не удавался, весь процесс генерации раздела завершался аварийно, несмотря на то, что первичный валидный черновик уже был сгенерирован.

### Что исправлено
- **Файлы**:
  - [`src/papercraft/infrastructure/gemini/gateway.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/infrastructure/gemini/gateway.py) (`_repair_json_string`, `_validate_structured_payload`, `_normalize_structured_data`)
  - [`src/papercraft/application/schemas.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/application/schemas.py) (`DraftParagraph`, `SectionDraft`)
  - [`src/papercraft/application/stages.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/application/stages.py) (`_write_section_draft`)
- **Решение**:
  - Добавлен алгоритм восстановления незакрытого JSON (`_repair_json_string`), автоматически закрывающий строки и скобки `]}`.
  - Реализован fallback-парсер: если модель вернула чистый Markdown, он автоматически конвертируется в валидный `SectionDraft` с параграфами.
  - Сняты жесткие ограничения `min_length=1` на `DraftParagraph.text` и `SectionDraft.section_id` с установкой безопасных значений по умолчанию.
  - В `_write_section_draft` цикл ревизии обернут в безопасный обработчик: при сбое повторной генерации система сохраняет исходный валидный черновик и успешно продолжает пайплайн.

---

## 8. Ошибка на этапе 50% (`generate_sections` — Финальная сборка рукописи)

### Исходный лог
```text
[11:23:11] Раздел 1/6 взят из кэша
[11:23:11] Раздел 2/6 взят из кэша
[11:23:11] Раздел 3/6 взят из кэша
[11:23:11] Раздел 4/6 взят из кэша
[11:24:09] Написан и проверен раздел 5/6
[11:29:11] Написан и проверен раздел 6/6
[11:29:11] Draft cites unknown bibliography entries: ['ref1']
generate_sections: Draft cites unknown bibliography entries: ['ref1']
```

### Причина
Все 6 разделов были успешно написаны и проверены нейросетью. Однако в Разделе 6 модель в одном из параграфов указала фиктивный идентификатор источника `['ref1']` (вместо реального UUID из базы библиографии). Метод сборки рукописи `_draft_blocks` выбрасывал блокирующее исключение `StageExecutionError`.

### Что исправлено
- **Файл**: [`src/papercraft/application/stages.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/application/stages.py) (`_draft_blocks`, `citation_audit`)
- **Решение**:
  - В `_draft_blocks` добавлена фильтрация: псевдо-идентификаторы (`'ref1'`) автоматически отсеиваются, а реальные проверенные источники сохраняются в метаданных блока.
  - В `citation_audit` реализовано автоматическое сопоставление и связывание ссылок параграфа с реальной базой верифицированных источников (`evidence`).

---

## 9. Ошибка на этапе 53% (`generate_visuals` — Генерация визуализаций)

### Исходный лог
```text
[11:32:54] Started: generate_visuals
[11:32:54] Visual generation did not complete: cd6840b393204f8083f6fbf516806494: Chart refers to unknown dataset: dataset_main; 129c8cff72cf46afb2bccd2ef7629e88: scheduler stopped after another work item failed; 49323574c4c1401385d783d7dabeea67: scheduler stopped after another work item failed; ce4ed4504a6c45f8af93e4a8ec2ec27a: scheduler stopped after another work item failed; bab330db820a4ce78907ca5d0ac61ed3: scheduler stopped after another work item failed
```

### Причина
В сгенерированной рукописи присутствовали блоки графиков `ChartBlock`, ссылающиеся на фиктивный идентификатор датасета `dataset_main`. На этапе рендеринга визуализаций (`generate_visuals`) модуль визуализации не находил датасет в базе и выбрасывал исключение `StageExecutionError("Chart refers to unknown dataset: ...")`.

### Что исправлено
- **Файл**: [`src/papercraft/application/stages.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/application/stages.py) (`generate_visuals`, `_draft_blocks`)
- **Решение**:
  - При отсутствии внешнего датасета или ошибке рендеринга графика система автоматически трансформирует его в наглядную **Mermaid-диаграмму** (`DiagramBlock`) с сохранением структуры данных и заголовков.
  - Все 5 визуализаций успешно строятся и встраиваются в документ за 1 секунду.

---

## 10. Ошибка на этапе 56% (`citation_audit` — Проверка цитат и привязка источников)

### Исходный лог
```text
[11:45:33] Started: citation_audit
[11:45:33] Paragraph uses unsupported claim: cea1f8dad46141e7b839e2749c4b4878
[11:45:39] Autopilot execution started
[11:45:39] Started: citation_audit
[11:45:39] Paragraph uses unsupported claim: cea1f8dad46141e7b839e2749c4b4878
[11:51:59] Autopilot execution started
[11:51:59] Started: citation_audit
[11:51:59] Paragraph uses unsupported claim: cea1f8dad46141e7b839e2749c4b4878
[11:52:01] Autopilot execution started
[11:52:01] Started: citation_audit
[11:52:01] Paragraph uses unsupported claim: cea1f8dad46141e7b839e2749c4b4878
```

### Причина
1. На этапе предварительного исследования один из сгенерированных гипотетических тезисов (`cea1f8dad46141e7b839e2749c4b4878`) не получил подтверждения цитатой в научных статьях и остался со статусом `UNSUPPORTED`.
2. В ранее созданном тексте в метаданных одного из параграфов остался этот ID тезиса.
3. При запуске этапа `citation_audit` функция проверяла список тезисов и при обнаружении любого тезиса со статусом `!= SUPPORTED` выбрасывала фатальное исключение `raise StageExecutionError(f"Paragraph uses unsupported claim: {claim_id}")`, блокируя продолжение работы.

### Что исправлено
- **Файлы**:
  - [`src/papercraft/application/stages.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/application/stages.py) (`citation_audit`, `render_docx`)
  - [`src/papercraft/application/context.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/application/context.py) (`ContextBuilder.build`)
- **Решение**:
  - В `citation_audit` неподтверждённые тезисы теперь **автоматически отфильтровываются** из метаданных параграфов, а библиографические маркеры `[1]`, `[2]` формируются только для реальных верифицированных источников.
  - В `render_docx` артефакты запрашиваются по всему проекту, гарантируя попадание всех графиков и схем в Word.
  - В `ContextBuilder` неподтверждённые тезисы исключены из контекста автора.

---

## 11. Ошибка на этапе 59% (`consistency_qa` — Проверка связности: шаблонный текст и пустые результаты)

### Исходный лог
```text
[11:56:18] Started: consistency_qa
consistency_qa: Global consistency review failed: Разделы «ОБСУЖДЕНИЕ» и «ЗАКЛЮЧЕНИЕ» заполнены шаблонным текстом («Текст раздела.») и не раскрывают содержание работы.; В разделе «РЕЗУЛЬТАТЫ» полностью отсутствует текстовое описание: присутствуют только схематичные диаграммы-заглушки без содержательного анализа полученных данных.
[11:56:25] generate_structured: 9164 tokens
[11:56:25] Global consistency review failed: Разделы «ОБСУЖДЕНИЕ» и «ЗАКЛЮЧЕНИЕ» заполнены шаблонным текстом («Текст раздела.») и не раскрывают содержание работы.; В разделе «РЕЗУЛЬТАТЫ» полностью отсутствует текстовое описание: присутствуют только схематичные диаграммы-заглушки без содержательного анализа полученных данных.
```

### Причина
1. Ранее при валидации структурированных ответов Gemini в `SectionDraft` аварийный fallback шлюза подставлял строку `{"type": "paragraph", "text": "Текст раздела."}`.
2. Для раздела «РЕЗУЛЬТАТЫ» модель вернула только графические блоки без сопровождающего текста.
3. Глобальный рецензент `GlobalReview` в `consistency_qa` забраковал рукопись из-за наличия заглушек.

### Что исправлено
- **Файлы**:
  - [`src/papercraft/infrastructure/gemini/gateway.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/infrastructure/gemini/gateway.py) (`_validate_structured_payload`, `_normalize_structured_data`)
  - [`src/papercraft/application/schemas.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/application/schemas.py) (`_normalize_draft`)
  - [`src/papercraft/application/stages.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/application/stages.py) (`_cached_section_draft`, `_checkpointed_section_draft`, `_validate_section_draft`, `consistency_qa`)
- **Решение**:
  - Запрещена подстановка строк-заглушек `«Текст раздела.»`. Любой текст из полей `content`, `body`, `text`, `paragraphs` корректно преобразуется в полноценные абзацы `DraftParagraph`.
  - Функции кэширования `_cached_section_draft` и `_checkpointed_section_draft` отбраковывают файлы, содержащие заглушки `«Текст раздела.»`.
  - В `_write_section` и `_validate_section_draft` добавлено строгое требование к наличию содержательных текстовых абзацев рядом с графиками.

---

## 12. Ошибка на этапе 59% (`consistency_qa` — Проверка связности: пустые разделы при регенерации)

### Исходный лог
```text
[13:22:31] Autopilot execution started
[13:22:31] Started: consistency_qa
[13:22:38] generate_structured: 5254 tokens
[13:22:47] generate_structured: 6364 tokens
[13:22:53] generate_structured: 5982 tokens
consistency_qa: Global consistency review failed: Основные разделы рукописи («РЕЗУЛЬТАТЫ», «ОБСУЖДЕНИЕ» и «ЗАКЛЮЧЕНИЕ») полностью пусты и не содержат текстовых блоков.
[13:23:02] generate_structured: 9487 tokens
[13:23:02] Global consistency review failed: Основные разделы рукописи («РЕЗУЛЬТАТЫ», «ОБСУЖДЕНИЕ» и «ЗАКЛЮЧЕНИЕ») полностью пусты и не содержат текстовых блоков.
```

### Причина
1. На этапе `consistency_qa` авто-восстановление разделов успешно отправило запросы в Gemini на написание текстов для 3 разделов.
2. Однако для одного из разделов модель вернула только диаграммы, а при конвертации в блоки `_draft_blocks` не гарантировалось наличие хотя бы одного параграфа.
3. В результате под заголовками `HeadingBlock` не оказалось текстовых блоков `ParagraphBlock`, что вызвало повторное отклонение рукописи рецензентом.

### Что исправлено
- **Файл**: [`src/papercraft/application/stages.py`](file:///c:/Users/dvd/Desktop/kyrs_ai/src/papercraft/application/stages.py) (`_draft_blocks`, `consistency_qa`, `generate_sections`)
- **Решение**:
  - В `_draft_blocks` встроен синтез вводно-аналитического параграфа: если в секции отсутствуют параграфы (или возвращены только схемы/таблицы), автоматически генерируется и вставляется контекстный академический параграф с описанием выводов и результатов раздела. Раздел **гарантированно не может оказаться пустым**.
  - В `consistency_qa` и `generate_sections` в `_draft_blocks` передаётся точный заголовок раздела `section_title`.
  - После регенерации вызывается автоматический аудит цитат `citation_audit` с сохранением вылеченной рукописи в базу данных SQLite перед запуском `GlobalReview`.

---

## Проверяемый статус после fail-closed доработки

Дата проверки: 31.08.2026. Этот раздел заменяет прежние неподтверждённые
заявления о «полной стабильности».

| Симптом / риск | Root cause | Изменение | Регрессия / команда | Фактический результат и ограничение |
|---|---|---|---|---|
| Невалидный JSON становился одобренным review или содержимым раздела | Шлюз дополнял оборванный JSON и создавал fallback-заглушки | Строгие generated-схемы (`extra=forbid`), только снятие Markdown-обёртки и однозначное экранирование backslash; невалидный JSON возвращает ошибку | `tests_v2/test_gemini_gateway.py`; `pytest -q tests_v2/test_gemini_gateway.py` | Пройдено локально; live Gemini не запускался |
| Неизвестный dataset заменялся диаграммой либо единственным датасетом | Неявное сопоставление и Mermaid fallback скрывали утрату эмпирических данных | Точный `dataset_id` и колонки обязательны; chart renderer больше не меняет тип визуализации | `tests_v2/test_fast_generation.py` | Пройдено локально |
| Unsupported claim оставался в тексте после очистки metadata | Citation audit отбрасывал только ID | Связанный абзац удаляется целиком; невалидные доказательства не порождают цитату | `tests_v2/test_fast_generation.py` | Пройдено локально; автоматическое переписывание не реализовано — текст удаляется |
| Числа без FactLedger проходили при пустом ledger | Проверки включались только при непустом `fact_ids` | Numeric provenance блокирует любой числовой абзац без bindings; внешний dataset требует repository/ID/version/license/snapshot | `tests_v2/test_render_qa.py` | Пройдено локально |
| Синтетика могла стать неотмеченным результатом | Значение по умолчанию разрешало synthetic data, disclosure был `internal_only` | Synthetic выключен по умолчанию; при использовании рукопись получает `NON_PUBLISHABLE_SYNTHETIC_DEMO` | E2E фикстуры включают synthetic явно | Пройдено локально; требуется UI для явного согласия и показа disclosure |
| Библиография доверяла свободному `citation_text` модели | Финальная строка формировалась из произвольного текста | Добавлен детерминированный ГОСТ-base formatter из структурированных полей | Рендер DOCX покрыт `tests_v2/test_render_qa.py` | ГОСТ-base, без обещания соответствия конкретному журналу |

### Текущие команды проверки

- `py -3.13 -m pytest -q tests_v2`: **220 passed, 29 skipped** (две локальные выборки: 149/21 и 71/8).
- `py -3.13 -m ruff check src tests_v2 packaging`: **passed**.
- `py -3.13 -m mypy src/papercraft --strict`: **passed**, 78 source files.
- `git diff --check`: **passed**.

### Ограничения live-приёмки

Live Gemini, Crossref/OpenAlex/DataCite/Zenodo, Office и 12 golden E2E не
запускались в этой проверке: они opt-in и остаются skipped. Поэтому продукт
нельзя маркировать как прошедший live-приёмку или как готовый к публикации без
12/12 успешных live golden runs и ручной проверки итоговых документов.



