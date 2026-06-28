# Phase104H CI Quality Gate

Phase104H adds a repeatable CI quality gate around the Phase104 frontend release chain.

It generates:

- `.github/workflows/qualibug_phase104_quality_gate.yml`
- `scripts/Run-Phase104QualityGate.ps1`
- `scripts/run_phase104_quality_gate.py`
- `docs/CI_QUALITY_GATE_RUNBOOK.md`
- `docs/CI_RELEASE_POLICY.md`
- `docs/GITHUB_ACTIONS_SETUP.md`
- `frontend_release_readiness/` from Phase104G
- `phase104_ci_quality_gate_manifest.json / .md`
- `ci_quality_gate_report.json / .md`
- `CHECKSUMS.sha256`
- `phase104_ci_quality_gate_bundle.zip`

Run:

```powershell
python -m ai_test_asset_center.phase104_ci_quality_gate --output-dir .\outputs\phase104_ci_quality_gate
```

Validate an existing bundle:

```powershell
python -m ai_test_asset_center.phase104_ci_quality_gate --validate-only --output-dir .\outputs\phase104_ci_quality_gate
```
