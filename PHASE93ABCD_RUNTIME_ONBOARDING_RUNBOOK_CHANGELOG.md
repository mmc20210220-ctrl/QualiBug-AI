# Phase93A/B/C/D — Runtime Onboarding Preflight, Capability Matrix, Remediation Kit and Execution Runbook

## Goal

Make QualiBug commercially operable when customers provide only a test/staging URL and accounts.  Before any high-value runtime validation, the engine now tells the customer whether the environment is ready, which probes can produce strong evidence, what setup gaps remain, and exactly how to run the next safe execution pass.

This phase does **not** create bug findings from static rules. It only governs customer-environment readiness and runtime execution sequencing.

## Phase93A — Runtime onboarding preflight

Added `runtime_onboarding_preflight_v1_phase93a`.

Checks:

- base URL configured and reachable;
- production-like environment/host guard;
- strict document grounding coverage;
- auth/session readiness from customer accounts or headers;
- recommended role coverage: normal, admin, owner, cross-tenant/two-tenant;
- disposable sandbox and auto fixture readiness;
- cleanup/reset strategy declaration;
- snapshot observer readiness;
- unresolved `<FILL:...>` template placeholders.

Outputs:

- `report["onboarding_preflight"]`
- `grounded_probe_onboarding_preflight.json`

## Phase93B — Probe runtime capability matrix

Added `runtime_probe_capability_matrix_v1_phase93b`.

Every grounded probe now gets:

- `preflight_lane`:
  - `read_only_runtime_ready`
  - `write_sandbox_runtime_ready`
  - `runtime_degraded`
  - `write_sandbox_blocked_by_capability`
  - `blocked_by_preflight`
  - `plan_only`
- `expected_evidence_quality`:
  - `strong_runtime_before_after`
  - `medium_runtime_request_response`
  - `weak_or_partial_runtime_evidence`
  - `no_runtime_evidence_plan_only`
  - `no_runtime_evidence_blocked`
- missing blocking/optional capabilities;
- per-probe customer action.

Executor decisions are annotated with lane metadata without changing safety gates.

Outputs:

- `report["runtime_capability_matrix"]`
- `grounded_probe_runtime_capability_matrix.json`

## Phase93C — Customer onboarding remediation kit

Added `runtime_onboarding_remediation_kit_v1_phase93c`.

It turns preflight/capability gaps into customer-safe setup actions and redacted config patch templates.

Actions include:

- configure staging/QA `base_url`;
- switch from production-like target to non-production;
- provide `auth_flow` and staging accounts;
- add normal/admin/owner/cross-tenant roles;
- enable disposable auto fixture creation;
- declare supported cleanup/reset strategy;
- expose snapshot observers;
- replace non-executable `<FILL:...>` placeholders.

Outputs:

- `report["onboarding_remediation_kit"]`
- `grounded_probe_onboarding_remediation_kit.json`
- `grounded_probe_onboarding_remediation_kit.md`

## Phase93D — Runtime execution runbook

Added `runtime_execution_runbook_v1_phase93d`.

It sequences customer runtime execution into safe lanes:

1. preflight and capability review;
2. read-only probes first;
3. approved disposable-sandbox write probes second;
4. unblock blocked/plan-only probes;
5. use remediation verification artifacts for fix reruns.

Outputs:

- `report["runtime_execution_runbook"]`
- `grounded_probe_runtime_execution_runbook.json`
- `grounded_probe_runtime_execution_runbook.md`

## Executor version

`grounded_probe_executor_v19_phase93d`

## Verification

```bash
python -m pytest tests/test_phase93a_runtime_onboarding_preflight.py tests/test_phase93b_runtime_probe_capability_matrix.py tests/test_phase93c_onboarding_remediation_kit.py tests/test_phase93d_runtime_execution_runbook.py -q
# 13 passed
```

```bash
python -m pytest tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py tests/test_phase92r_observer_response_semantic_joiner.py tests/test_phase92s_cross_observer_conservation_reconciler.py tests/test_phase92t_runtime_finding_evidence_packager.py tests/test_phase92u_runtime_finding_customer_triage.py tests/test_phase92v_runtime_customer_report_builder.py tests/test_phase92w_runtime_reproduction_asset_linker.py tests/test_phase92x_runtime_fix_verification_loop.py tests/test_phase92y_runtime_finding_lifecycle_registry.py tests/test_phase92z_runtime_remediation_artifact_builder.py tests/test_phase93a_runtime_onboarding_preflight.py tests/test_phase93b_runtime_probe_capability_matrix.py tests/test_phase93c_onboarding_remediation_kit.py tests/test_phase93d_runtime_execution_runbook.py -q
# 51 passed
```

```bash
python -m pytest tests/test_strict_document_grounding.py tests/test_discovery_finding_gate.py tests/test_business_invariant_mining.py tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py tests/test_phase92r_observer_response_semantic_joiner.py tests/test_phase92s_cross_observer_conservation_reconciler.py tests/test_phase92t_runtime_finding_evidence_packager.py tests/test_phase92u_runtime_finding_customer_triage.py tests/test_phase92v_runtime_customer_report_builder.py tests/test_phase92w_runtime_reproduction_asset_linker.py tests/test_phase92x_runtime_fix_verification_loop.py tests/test_phase92y_runtime_finding_lifecycle_registry.py tests/test_phase92z_runtime_remediation_artifact_builder.py tests/test_phase93a_runtime_onboarding_preflight.py tests/test_phase93b_runtime_probe_capability_matrix.py tests/test_phase93c_onboarding_remediation_kit.py tests/test_phase93d_runtime_execution_runbook.py -q
# 61 passed
```

```bash
python -m compileall -q ai_test_asset_center tests/test_phase93a_runtime_onboarding_preflight.py tests/test_phase93b_runtime_probe_capability_matrix.py tests/test_phase93c_onboarding_remediation_kit.py tests/test_phase93d_runtime_execution_runbook.py
# passed
```

Full `python -m pytest -q` was started with a 45s execution window; it reached ongoing passing progress and timed out before completion, with no failure output before timeout.
