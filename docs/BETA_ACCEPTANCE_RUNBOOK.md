# PaperCraft beta acceptance runbook

This runbook is for the private beta on the `codex/papercraft-v1` branch.
It records executable acceptance steps without putting credentials in the
repository, shell history, test output, project database, or diagnostics.

## Credential safety

1. Revoke any Gemini key that has been pasted into a chat, ticket, terminal
   capture, or repository history, then create a replacement in Google AI
   Studio.
2. Restrict the replacement key to the Gemini API and a dedicated project with
   a quota/budget cap.
3. Store it with the desktop application's **Configure Gemini key** action
   (Windows Credential Manager). A temporary `GEMINI_API_KEY` process
   environment variable is allowed for local, opt-in test runs only.
4. Never pass a key on the command line, save it in `.env`, or put it in an
   acceptance artifact. If a key may have leaked, revoke it instead of trying
   to redact historical copies.

## Local quality gate

Run these commands from the repository root. They do not call Gemini unless a
separate opt-in variable is set.

```powershell
uv run --locked python -m ruff check src tests_v2 packaging
uv run --locked python -m mypy src/papercraft --strict
uv run --locked python -m pytest -q
```

## Gemini acceptance

Use only anonymised fixtures. Start with the smallest contract and advance
only after it passes; this avoids spending provider quota on a failing
pipeline. The beta admits one Gemini-generating worker per configured
projects directory; a second project is rejected immediately rather than
queueing or creating another burst of provider requests. Finish or cancel the
active generation before starting the next one. Cancellation itself remains
available while that lease is held.

```powershell
# Aggregate cap for this direct-gateway suite; choose it before the run.
$env:PAPERCRAFT_LIVE_TEST_MAX_COST_USD = "<approved-suite-cap>"
$env:PAPERCRAFT_RUN_GEMINI_TESTS = "1"
uv run --locked python -m pytest -q "tests_v2/test_gemini_live.py::test_live_research_plan_structured_contract"

# This is a cap for each golden run, not for all twelve together.
$env:PAPERCRAFT_GOLDEN_MAX_COST_USD = "<approved-per-run-cap>"
$env:PAPERCRAFT_RUN_GOLDEN_TESTS = "1"
uv run --locked python -m pytest -q "tests_v2/test_live_golden_e2e.py::test_live_golden_pipeline_twice[it_coursework-1]"

# Run only after the contract and one golden run passed within their caps.
# The direct suite deliberately skips the stored-background lifecycle.
uv run --locked python -m pytest -q tests_v2/test_gemini_live.py
uv run --locked python -m pytest -q tests_v2/test_live_golden_e2e.py

# FINAL DESTRUCTIVE PROVIDER CHECK: run only after all 12 goldens pass.
# It starts one bounded stored background interaction, cancels it, waits for
# cancellation, deletes it, and confirms that GET returns 404. Do not run this
# concurrently with another live acceptance command.
$env:PAPERCRAFT_RUN_BACKGROUND_LIFECYCLE_TESTS = "1"
try {
  uv run --locked python -m pytest -q "tests_v2/test_gemini_live.py::test_live_background_cancellation"
} finally {
  Remove-Item Env:PAPERCRAFT_RUN_BACKGROUND_LIFECYCLE_TESTS -ErrorAction SilentlyContinue
}
```

Record only safe status, timing, token/cost totals, output hashes and QA
results. A successful golden run must emit valid DOCX, PDF and QA artifacts,
have no unresolved placeholders, and clean up registered remote files. Once a
live flag is enabled, a missing credential, LibreOffice binary, or explicit
cost limit fails the acceptance command instead of producing a green skip.

## LibreOffice acceptance

LibreOffice is the beta finalizer. Microsoft Word compatibility is outside
this beta and must remain marked unverified.

```powershell
$env:PAPERCRAFT_RUN_OFFICE_TESTS = "1"
uv run --locked python -m pytest -q tests_v2/test_office_integration.py tests_v2/test_office_finalizer.py
```

Check the generated feature-matrix PDF for Russian fonts, title page,
contents/page fields, captions, tables, formulas, landscape pages, images,
bibliography and appendices. Treat a damaged DOCX/PDF, missing page numbers,
or a critical visual-QA issue as a release blocker.

## Beta exit criteria

- Ruff, strict MyPy and the local suite pass.
- The Gemini structured contract and all twelve golden executions pass with a
  replacement credential.
- After the twelve goldens, the separately opt-in stored background lifecycle
  completes cancel, confirmed cancellation, delete, and a safe 404 check.
- LibreOffice produces a valid PDF and the feature matrix has no blocker.
- Requirement coverage and user revision history are visible in the app.
- DOCX/PDF export is available only after a successful matching release QA.
  Uncovered binding methodology, institution, or user rules and evidence gaps
  block it. Profile scaffold rules remain visible as advisory coverage until a
  user promotes them to a binding requirement.
- A manually edited factual paragraph needs linked, verified evidence before it
  can pass citation QA; otherwise the revision is retained but release export
  remains blocked.
- Word, code signing and automatic updates remain explicitly out of scope.
