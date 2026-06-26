# Phase95A Bug Engine Hardening Notes

Focus: keep the product converged on high-signal bug discovery rather than feature sprawl.

## Added

- `ai_test_asset_center/auth_boundary_probe_generator.py`
  - Generates customer-grounded negative authorization probes.
  - Covers anonymous access, role downgrade, and cross-tenant/foreign-owner access.
  - Does not invent endpoints; every generated probe reuses a source endpoint and keeps source refs.
  - Keeps GET probes read-only and requires disposable sandbox for mutating probes.

## Integrated

- `bug_discovery_probe_expander.py`
  - Now compounds Phase94A-D with Phase95A.
  - Adds `negative_auth_boundary_probe_count` into improvement evidence.
  - Preserves existing explicit enable flag behavior.

## Tests

Validated targeted engine safety and integration suites:

- `tests/test_phase94abcd_bug_discovery_engine.py`
- `tests/test_phase92f_deep_probe_plan.py`
- `tests/test_grounded_probe_executor.py`
- `tests/test_discovery_finding_gate.py`
- `tests/test_business_assurance_coverage.py`

Result: 30 targeted tests passed, plus compile check passed.
