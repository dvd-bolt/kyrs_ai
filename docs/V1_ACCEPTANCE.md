# V1 acceptance checklist

Этап 1 фиксирует воспроизводимый фундамент, но не объявляет v1 готовой.

## Проверки

- [x] Ветка `codex/papercraft-v1` создана с сохранением рабочего дерева.
- [x] Ruff для `src tests_v2` проходит на доступном Python 3.13.
- [x] `mypy src/papercraft --strict` проходит.
- [x] `pytest tests_v2 -q`: 49 passed, 3 skipped.
- [x] `git diff --check` не сообщает ошибок whitespace.
- [ ] Python 3.12 и `uv sync` подтверждены на машине разработчика.
- [ ] LibreOffice smoke подтверждён в окружении с LibreOffice.
- [ ] Word COM smoke подтверждён вручную.
- [ ] Live Gemini подтверждён вручную с ключом.
- [ ] Windows installer принят.
- [ ] Финальные golden E2E документы сгенерированы и приняты.

## Правило приёмки

Обычный CI не вызывает Gemini и не требует Word COM. Эти проверки выполняются только ручными jobs или отдельно назначенным окружением.
