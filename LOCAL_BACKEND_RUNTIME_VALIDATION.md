# Local Backend Runtime Validation

This is the repeatable localhost validation flow for the bundled `backend.main`
FastAPI target. It proves the runtime loop can move through:

1. document-grounded candidate generation,
2. real HTTP evidence collection,
3. generated regression tests,
4. fix verification against a previous execution report,
5. closure claims recorded as `closed_by_rerun`.

Run from the repository root:

```powershell
.\RUN_LOCAL_BACKEND_RUNTIME_VALIDATION.ps1
```

Main artifacts:

```text
platform_outputs\local_backend_runtime\input_only_run\grounded_probe_execution_report.before_fix.json
platform_outputs\local_backend_runtime_after_fix\input_only_run\grounded_probe_execution_report.json
platform_outputs\local_backend_runtime_fix_rerun\grounded_probe_execution_report.json
platform_outputs\local_backend_runtime_fix_rerun\grounded_probe_commercial_closure_acceptance_ledger.json
```

Expected post-fix signal:

```text
validated_candidate_count = 0
protected_count = 4
closed_by_rerun_count = 4
handoff_rerun_closure_allowed = true
commercial_handoff_secret_audit_status = handoff_secret_audit_passed
```

Safety notes:

- The target is localhost only.
- The flow does not read benchmark ground truth.
- `.env.local` is not required and must not be printed or committed.
- Runtime outputs stay under ignored `platform_outputs/`.

