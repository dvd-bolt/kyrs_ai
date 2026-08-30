# PaperCraft private-beta acceptance matrix

This matrix records what the beta can claim today. A row marked **pending** is
not a release claim and must be rerun with a newly created, restricted Gemini
credential before the beta is handed to a user.

| Area | Private-beta criterion | Status | Evidence / safe next action |
|---|---|---|---|
| Local quality | Ruff, strict MyPy and complete local `tests_v2` pass | Confirmed in this workspace | Ruff and strict MyPy pass; `tests_v2`: 189 passed, 29 explicit opt-in skips. Rerun the local gate for every candidate build. |
| Provider key | No credential is committed, logged, or passed on a command line | Implemented | Configure a replacement only through Windows Credential Manager or a temporary process variable. Revoke any key pasted into a chat. |
| Gemini contract | One structured contract succeeds with a replacement key | Pending live acceptance | Run `test_gemini_live.py` with the explicit opt-in flag. |
| Golden pipeline | Twelve anonymised Gemini golden runs produce DOCX, PDF, QA and remote cleanup | Pending live acceptance | Run the single `it_coursework` check first, then the full live golden suite. |
| Stored background cleanup | A bounded stored interaction is cancelled, confirmed cancelled, deleted, then returns 404 | Pending live acceptance | Run last, with both `PAPERCRAFT_RUN_GEMINI_TESTS=1` and `PAPERCRAFT_RUN_BACKGROUND_LIFECYCLE_TESTS=1`; it is deliberately excluded from `gemini-full`. |
| Cost and 429 safety | Conservative scheduling, durable cooldown and resumable state | Implemented and locally tested | Parallel generation remains off by default. Live direct tests require an aggregate suite cap; every golden run requires an explicit per-run cap. |
| Paid results | Cancel/pause/resume preserves completed provider work and only resumes missing quality work | Implemented and locally tested | Verify with the local fast-generation tests. |
| Requirements | Coverage links each requirement to manuscript/DOCX/PDF evidence; unresolved binding gaps block export | Implemented and locally tested | Review the coverage panel and successful release QA report before export. |
| Revisions | User plan/section revisions persist and invalidate only dependent stages | Implemented and locally tested | Run section-revision tests; factual edits without evidence intentionally fail QA. |
| LibreOffice | LibreOffice finalizes DOCX, exports PDF and passes visual feature-matrix checks | Confirmed on this PC | The enabled office suite passed 12/12; rerun it and inspect generated page PNGs on every target PC. |
| Microsoft Word | Word compatibility | Out of scope | It is not a private-beta criterion and is not used as a fallback. |
| Distribution | Code signing and automatic updates | Out of scope | Treat builds as private-beta artifacts only. |

The authoritative commands and credential hygiene requirements are in
[`BETA_ACCEPTANCE_RUNBOOK.md`](BETA_ACCEPTANCE_RUNBOOK.md).
