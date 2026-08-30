# V1 acceptance checklist

> **Historical public-release checklist.** The active private beta is
> LibreOffice-only; its authoritative scope and checks are in
> [`BETA_ACCEPTANCE_MATRIX.md`](BETA_ACCEPTANCE_MATRIX.md). This checklist's
> Microsoft Word item does not block the private beta.

Текущий результат этапа 3: **RELEASE BLOCKED**. Release commit нельзя создавать, пока
обязательные live-пункты ниже не завершены без `CRITICAL`/`BLOCKER`.

## Проверки

- [x] Production Gemini model/thinking policy закреплена и покрыта contract tests.
- [x] Реальные SourceSnapshot и Claim → Evidence → Source → Locator реализованы.
- [x] Crossref/OpenAlex/DOI live integration: 2 passed.
- [x] OCR/Vision live fixtures: 6/6 passed.
- [x] Безопасное применение DOCX templates подтверждено.
- [x] LibreOffice matrix и PDF export: 2 passed.
- [ ] Microsoft Word COM matrix — Word недоступен в окружении.
- [x] PDF repair cycle исправил найденную live-проверкой нумерацию страниц.
- [ ] Финальный live Gemini PDF re-review — 429 provider quota.
- [x] Fault-injection/security subset: 41 passed.
- [ ] Шесть live golden E2E минимум дважды — все 12 запущены, 0 passed / 12 failed из-за 429 quota.
- [x] Полный source и frozen UI smoke выполнен.
- [x] PyInstaller/Inno Setup build, non-elevated install, first launch, worker, update/uninstall и сохранность projects проверены локально.
- [ ] Installer проверен на отдельных чистых Windows 10 и Windows 11.
- [x] TODO/FIXME/mock/stub/placeholder/exec/remote fallback audit выполнен для release scope.
- [x] Ruff, strict MyPy, pytest и `git diff --check` проходят в release scope.
- [ ] Code signing — сертификат отсутствует; допустим только unsigned beta после остальных checks.

Подробные результаты и хэши находятся в [`STAGE3_ACCEPTANCE.md`](STAGE3_ACCEPTANCE.md).

## Правило приёмки

Обычный CI не вызывает платные внешние сервисы и не требует Office. Release acceptance
обязана отдельно выполнить live jobs; локальные fakes не заменяют эти пункты.
