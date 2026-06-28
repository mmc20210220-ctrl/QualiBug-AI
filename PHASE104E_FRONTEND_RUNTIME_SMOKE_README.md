# Phase104E Frontend Runtime Smoke

Phase104E adds a frontend-to-backend runtime smoke harness for the Phase104 frontend integration workspace.

## Purpose

Phase104D generated a framework-neutral frontend handoff workspace. Phase104E proves the handoff actually works against the local Phase104A backend contract without requiring Node, a browser, or an external server.

The smoke runner validates:

1. Phase104D workspace completeness.
2. Workspace secret-safety.
3. Seed scenario read paths for dashboard, environment, live map, risks, risk detail, ROI, and executive report.
4. Frontend write workflow: create project, apply template, patch environment config, run preflight, generate plan, start test run, generate report.
5. Customer-safe method rejection for unsafe HTTP methods.
6. Runtime response redaction and absence of Python traceback leaks.

## Run

```powershell
python -m ai_test_asset_center.phase104_frontend_runtime_smoke --build-workspace --workspace-dir .\outputs\phase104_frontend_workspace --output-dir .\outputs\phase104_frontend_runtime_smoke
```

## Existing workspace only

```powershell
python -m ai_test_asset_center.phase104_frontend_runtime_smoke --workspace-dir .\outputs\phase104_frontend_workspace --output-dir .\outputs\phase104_frontend_runtime_smoke
```

## Output

```text
frontend_runtime_smoke_report.json
frontend_runtime_smoke_report.md
```

## Notes

The runner uses the in-process `Phase104CommandCenterHttpApp`, so it does not need to bind a port. It follows the same API envelope and route contract used by frontend clients.
