# Architecture baseline

PaperCraft сохраняет слоистую архитектуру: `domain` содержит модели и правила предметной области, `application` координирует pipeline/stages, а `infrastructure` предоставляет ingest, research, calculations, persistence, Gemini, visuals и render adapters. `ui` и `worker` являются отдельными входными точками.

## Boundaries

- `application` зависит от портов и доменных моделей, а не от конкретного UI.
- `infrastructure/gemini` предоставляет gateway и FakeGemini для детерминированных тестов.
- `infrastructure/render` отвечает за DOCX и finalizer; Word COM и LibreOffice не являются частью обычного CI.
- `infrastructure/persistence` сохраняет текущий repository/storage слой.
- `tests_v2` проверяет сервисы, pipeline, ingest, research, calculations, render и worker без генерации больших golden-документов.

## Stage 3 integration boundaries

- Gemini вызывается только через production gateway; FakeGemini внедряется тестами явно и
  не является runtime fallback. Секрет читается из Windows Credential Manager либо process
  environment и не сохраняется в project storage.
- HTTP retries принадлежат gateway: SDK HTTP retry codes переопределены, чтобы исключить
  nested retry storm; gateway классифицирует ошибки, применяет bounded attempts, jitter и
  provider `Retry-After`/`retry in …s`. Provider ID и локальный `client_request_id` не смешиваются.
- Ingest сохраняет byte-exact snapshot до parsing. Citation provenance проходит через
  typed Claim/Evidence/Source/Snapshot/Locator records; внешние scholarly adapters возвращают
  нормализованные records, а URL verifier применяет SSRF policy.
- Любой Gemini Files upload немедленно регистрируется как remote resource. Успешное,
  ошибочное и отменённое завершение выполняют retryable terminal cleanup.
- User DOCX templates проходят package-level active-content inspection до `python-docx`.
  Word COM и LibreOffice являются взаимозаменяемыми finalizer adapters, но отсутствие обоих
  всегда release-blocking.
- Frozen GUI повторно использует тот же EXE с внутренним worker dispatch flag; subprocesses
  получают только явные argv, timeout и hidden-window policy.
- Release artifacts получают deterministic content hashes. Неподписанная сборка маркируется
  beta и не может считаться принятой без live golden, Office и clean-Windows jobs.
- Per-user installer не включает project storage в install/uninstall scope; update и uninstall
  сверяются по byte-exact hashes `%LOCALAPPDATA%\PaperCraftAI\projects`.

## Stage 1 constraints

На baseline не менялись pipeline, persistence, Gemini Gateway, финансовые расчёты или DOCX renderer. Legacy-код сохранён. Golden fixtures на этом этапе являются только контрактными manifest-файлами.
