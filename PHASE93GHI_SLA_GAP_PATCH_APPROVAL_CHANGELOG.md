# Phase93G-H-I Runtime SLA Onboarding Delta, Patch Safety, and Write Approval

## Phase93G — SLA gap auto-prioritizer + onboarding delta patcher

Adds `runtime_sla_gap_prioritizer.py` and execution-report artifacts:

- `grounded_probe_runtime_sla_gap_prioritizer.json`
- `grounded_probe_runtime_sla_gap_prioritizer.md`

The prioritizer reads Phase93E/F outputs and turns readiness failures into a smallest-next-delta customer patch:

- prioritized onboarding actions;
- P0/P1 affected probe counts;
- estimated readiness score gain;
- estimated P0/P1 coverage improvement;
- top-action minimal patch;
- rerun sequence after customer applies the patch.

## Phase93H — Onboarding patch safety validator

Adds `runtime_onboarding_patch_safety_validator.py` and execution-report artifacts:

- `grounded_probe_onboarding_patch_safety_validation.json`
- `grounded_probe_onboarding_patch_safety_validation.md`

The validator blocks unsafe onboarding patches before customer handoff or merge:

- production-like base URL or production environment kind;
- raw passwords/secrets/tokens inside patch values;
- write probes enabled without cleanup strategy;
- unsupported cleanup strategies.

Sensitive concrete values are redacted in `sanitized_patch_preview`.

## Phase93I — Write-sandbox approval packet

Adds `runtime_write_sandbox_approval_packet.py` and execution-report artifacts:

- `grounded_probe_write_sandbox_approval_packet.json`
- `grounded_probe_write_sandbox_approval_packet.md`

The approval packet identifies:

- mandatory write SLA candidate IDs;
- supplemental/degraded/blocked write candidate IDs;
- approval blockers such as missing approval ID, unsafe patch, non-production failure, or cleanup failure;
- customer approval checklist;
- customer-safe approval statement template.

## Executor integration

`grounded_probe_executor.py` now reports engine:

```text
grounded_probe_executor_v24_phase93i
```

Governance flags added:

- `phase93g_sla_gap_auto_prioritizer`
- `phase93h_onboarding_patch_safety_validator`
- `phase93i_write_sandbox_approval_packet`

Summary fields added:

- `runtime_sla_gap_prioritized_action_count`
- `runtime_sla_estimated_score_after_top_actions`
- `onboarding_patch_safety_issue_count`
- `onboarding_patch_safe_to_send`
- `write_sandbox_approval_required`
- `write_sandbox_approval_ready`

## Validation

Focused tests:

```bash
python -m pytest tests/test_phase93g_runtime_sla_gap_prioritizer.py tests/test_phase93h_onboarding_patch_safety_validator.py tests/test_phase93i_write_sandbox_approval_packet.py -q
# 9 passed
```

Phase93A-I tests:

```bash
python -m pytest tests/test_phase93a_runtime_onboarding_preflight.py tests/test_phase93b_runtime_probe_capability_matrix.py tests/test_phase93c_onboarding_remediation_kit.py tests/test_phase93d_runtime_execution_runbook.py tests/test_phase93e_runtime_evidence_readiness_sla_gate.py tests/test_phase93f_runtime_sla_execution_policy.py tests/test_phase93g_runtime_sla_gap_prioritizer.py tests/test_phase93h_onboarding_patch_safety_validator.py tests/test_phase93i_write_sandbox_approval_packet.py -q
# 28 passed
```

Core regression:

```bash
python -m pytest tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py tests/test_phase92r_observer_response_semantic_joiner.py tests/test_phase92s_cross_observer_conservation_reconciler.py tests/test_phase92t_runtime_finding_evidence_packager.py tests/test_phase92u_runtime_finding_customer_triage.py tests/test_phase92v_runtime_customer_report_builder.py tests/test_phase92w_runtime_reproduction_asset_linker.py tests/test_phase92x_runtime_fix_verification_loop.py tests/test_phase92y_runtime_finding_lifecycle_registry.py tests/test_phase92z_runtime_remediation_artifact_builder.py tests/test_phase93a_runtime_onboarding_preflight.py tests/test_phase93b_runtime_probe_capability_matrix.py tests/test_phase93c_onboarding_remediation_kit.py tests/test_phase93d_runtime_execution_runbook.py tests/test_phase93e_runtime_evidence_readiness_sla_gate.py tests/test_phase93f_runtime_sla_execution_policy.py tests/test_phase93g_runtime_sla_gap_prioritizer.py tests/test_phase93h_onboarding_patch_safety_validator.py tests/test_phase93i_write_sandbox_approval_packet.py -q
# 66 passed
```

Extended regression:

```bash
python -m pytest tests/test_strict_document_grounding.py tests/test_discovery_finding_gate.py tests/test_business_invariant_mining.py tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py tests/test_phase92r_observer_response_semantic_joiner.py tests/test_phase92s_cross_observer_conservation_reconciler.py tests/test_phase92t_runtime_finding_evidence_packager.py tests/test_phase92u_runtime_finding_customer_triage.py tests/test_phase92v_runtime_customer_report_builder.py tests/test_phase92w_runtime_reproduction_asset_linker.py tests/test_phase92x_runtime_fix_verification_loop.py tests/test_phase92y_runtime_finding_lifecycle_registry.py tests/test_phase92z_runtime_remediation_artifact_builder.py tests/test_phase93a_runtime_onboarding_preflight.py tests/test_phase93b_runtime_probe_capability_matrix.py tests/test_phase93c_onboarding_remediation_kit.py tests/test_phase93d_runtime_execution_runbook.py tests/test_phase93e_runtime_evidence_readiness_sla_gate.py tests/test_phase93f_runtime_sla_execution_policy.py tests/test_phase93g_runtime_sla_gap_prioritizer.py tests/test_phase93h_onboarding_patch_safety_validator.py tests/test_phase93i_write_sandbox_approval_packet.py -q
# 76 passed
```

Compile check:

```bash
python -m compileall -q ai_test_asset_center tests/test_phase93g_runtime_sla_gap_prioritizer.py tests/test_phase93h_onboarding_patch_safety_validator.py tests/test_phase93i_write_sandbox_approval_packet.py
# passed
```
