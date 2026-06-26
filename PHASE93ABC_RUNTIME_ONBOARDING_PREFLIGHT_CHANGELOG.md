# Phase93A/B/C — Runtime Onboarding Preflight, Capability Matrix and Remediation Kit

## Goal

Move QualiBug's commercial runtime workflow from "try probes and inspect failures" to an explicit customer-environment onboarding loop before high-value P0/P1 runtime validation.

This phase does **not** create findings from static rules. It only reports whether the customer test environment can support runtime evidence collection and what setup gaps must be fixed.

## Phase93A — Runtime onboarding preflight

Added `runtime_onboarding_preflight_v1_phase93a`.

It checks:

- base URL configured and reachable;
- production-like environment/host guard;
- strict document grounding coverage;
- auth/session readiness from customer accounts or headers;
- recommended role coverage: normal, admin, owner, cross-tenant/two-tenant;
- disposable sandbox and auto fixture readiness;
- cleanup/reset strategy declaration;
- snapshot observer readiness;
- unresolved `<FILL:...>` template placeholders.

Output:

- `report["onboarding_preflight"]`
- `grounded_probe_onboarding_preflight.json`

## Phase93B — Probe runtime capability matrix

Added `runtime_probe_capability_matrix_v1_phase93b`.

It maps preflight readiness onto every grounded probe and assigns:

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
- customer action per probe.

Executor decisions are annotated with capability lane metadata without changing the underlying safety gates.

Output:

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

Output:

- `report["onboarding_remediation_kit"]`
- `grounded_probe_onboarding_remediation_kit.json`
- `grounded_probe_onboarding_remediation_kit.md`

## Executor version

`grounded_probe_executor_v18_phase93c`

## Verification

```bash
python -m pytest tests/test_phase93a_runtime_onboarding_preflight.py tests/test_phase93b_runtime_probe_capability_matrix.py tests/test_phase93c_onboarding_remediation_kit.py -q
# 10 passed
```

```bash
python -m pytest tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py tests/test_phase92r_observer_response_semantic_joiner.py tests/test_phase92s_cross_observer_conservation_reconciler.py tests/test_phase92t_runtime_finding_evidence_packager.py tests/test_phase92u_runtime_finding_customer_triage.py tests/test_phase92v_runtime_customer_report_builder.py tests/test_phase92w_runtime_reproduction_asset_linker.py tests/test_phase92x_runtime_fix_verification_loop.py tests/test_phase92y_runtime_finding_lifecycle_registry.py tests/test_phase92z_runtime_remediation_artifact_builder.py tests/test_phase93a_runtime_onboarding_preflight.py tests/test_phase93b_runtime_probe_capability_matrix.py tests/test_phase93c_onboarding_remediation_kit.py -q
# 48 passed
```

```bash
python -m pytest tests/test_strict_document_grounding.py tests/test_discovery_finding_gate.py tests/test_business_invariant_mining.py tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py tests/test_phase92r_observer_response_semantic_joiner.py tests/test_phase92s_cross_observer_conservation_reconciler.py tests/test_phase92t_runtime_finding_evidence_packager.py tests/test_phase92u_runtime_finding_customer_triage.py tests/test_phase92v_runtime_customer_report_builder.py tests/test_phase92w_runtime_reproduction_asset_linker.py tests/test_phase92x_runtime_fix_verification_loop.py tests/test_phase92y_runtime_finding_lifecycle_registry.py tests/test_phase92z_runtime_remediation_artifact_builder.py tests/test_phase93a_runtime_onboarding_preflight.py tests/test_phase93b_runtime_probe_capability_matrix.py tests/test_phase93c_onboarding_remediation_kit.py -q
# 58 passed
```

```bash
python -m compileall -q ai_test_asset_center tests/test_phase93a_runtime_onboarding_preflight.py tests/test_phase93b_runtime_probe_capability_matrix.py tests/test_phase93c_onboarding_remediation_kit.py
# passed
```

Full `python -m pytest -q` was started with a 45s execution window; it reached ongoing passing progress and timed out before completion, with no failure output before timeout.
