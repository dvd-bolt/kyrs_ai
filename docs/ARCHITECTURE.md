# Architecture baseline

PaperCraft сохраняет слоистую архитектуру: `domain` содержит модели и правила предметной области, `application` координирует pipeline/stages, а `infrastructure` предоставляет ingest, research, calculations, persistence, Gemini, visuals и render adapters. `ui` и `worker` являются отдельными входными точками.

## Boundaries

- `application` зависит от портов и доменных моделей, а не от конкретного UI.
- `infrastructure/gemini` предоставляет gateway и FakeGemini для детерминированных тестов.
- `infrastructure/render` отвечает за DOCX и finalizer; Word COM и LibreOffice не являются частью обычного CI.
- `infrastructure/persistence` сохраняет текущий repository/storage слой.
- `tests_v2` проверяет сервисы, pipeline, ingest, research, calculations, render и worker без генерации больших golden-документов.

## Stage 1 constraints

На baseline не менялись pipeline, persistence, Gemini Gateway, финансовые расчёты или DOCX renderer. Legacy-код сохранён. Golden fixtures на этом этапе являются только контрактными manifest-файлами.
