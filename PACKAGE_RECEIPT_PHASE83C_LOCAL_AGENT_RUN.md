# QualiBug Phase83C Local Agent Run Pack

## Package purpose

Windows local execution package for a local Code Agent. Includes the Phase83C Loop Runtime fix and relative-path startup / status / stop tooling.

## Included

- Full QualiBug source tree
- MES BugLab target
- Phase83C Runtime Supervisor fixes
- `.env.local.example` without secret values
- PowerShell and batch launchers
- Local Code Agent run contract

## Excluded

- `.env` / `.env.local`
- API keys / tokens / secrets
- Python bytecode / cache directories
- `platform_outputs` and runtime SQLite / logs
- temporary worktrees and local artifacts

## Verification scope

This package was assembled from `QualiBug_AI_Phase83C_Loop_Runtime_Fix.zip`. The included Phase83C verification reported 93 targeted tests passed and 309 tests collected. A complete test-suite run was not represented as passed because it exceeded the prior isolated environment time budget.
