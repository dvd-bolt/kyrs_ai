# Implementation status

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
