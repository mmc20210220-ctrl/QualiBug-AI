# Phase93M-N-O: Commercial Handoff Archive Receipt and Rerun Audit Gate

## Phase93M — commercial handoff archive manifest + immutable run receipt

Adds `runtime_handoff_archive_manifest.py` and writes:

- `grounded_probe_handoff_archive_manifest.json`
- `grounded_probe_handoff_archive_manifest.md`
- `grounded_probe_immutable_run_receipt.json`
- `grounded_probe_immutable_run_receipt.md`

The manifest records SHA-256 hashes for the probe plan, materialized customer handoff artifacts, commercial SLA gate, handoff bundle, acceptance gate, secret audit, remediation artifact, and an execution-report payload hash that intentionally avoids circular self-reference.

## Phase93N — immutable handoff receipt comparison

Adds `runtime_handoff_receipt_comparator.py` and writes:

- `grounded_probe_handoff_receipt_comparison.json`
- `grounded_probe_handoff_receipt_comparison.md`

The comparator classifies reruns as:

- `no_previous_receipt`
- `rerun_same_input_same_handoff_archive`
- `rerun_same_input_delivery_changed`
- `rerun_input_changed_new_lineage`
- `current_receipt_missing`

This tells customers whether a rerun used the same grounded input, SLA gate, handoff bundle, secret audit and artifact archive lineage.

## Phase93O — commercial rerun audit gate

Adds `runtime_handoff_rerun_audit_gate.py` and writes:

- `grounded_probe_handoff_rerun_audit_gate.json`
- `grounded_probe_handoff_rerun_audit_gate.md`

The audit gate decides whether a rerun may be used to close previous commercial findings:

- input hash changed => block closure against the old handoff;
- same input and same archive => allow closure verification;
- same input but delivery archive changed => require reviewer approval;
- no previous receipt => baseline only.

## Executor

`grounded_probe_executor.py` now reports engine version:

```text
grounded_probe_executor_v30_phase93o
```

and exposes Phase93M/N/O governance flags and summary fields.

## Verification

```bash
python -m pytest tests/test_phase93m_handoff_archive_manifest.py tests/test_phase93n_handoff_receipt_comparator.py tests/test_phase93o_handoff_rerun_audit_gate.py -q
# 9 passed
```

```bash
python -m pytest tests/test_phase93a_runtime_onboarding_preflight.py tests/test_phase93b_runtime_probe_capability_matrix.py tests/test_phase93c_onboarding_remediation_kit.py tests/test_phase93d_runtime_execution_runbook.py tests/test_phase93e_runtime_evidence_readiness_sla_gate.py tests/test_phase93f_runtime_sla_execution_policy.py tests/test_phase93g_runtime_sla_gap_prioritizer.py tests/test_phase93h_onboarding_patch_safety_validator.py tests/test_phase93i_write_sandbox_approval_packet.py tests/test_phase93j_commercial_handoff_bundle.py tests/test_phase93k_commercial_handoff_acceptance_gate.py tests/test_phase93l_handoff_secret_audit.py tests/test_phase93m_handoff_archive_manifest.py tests/test_phase93n_handoff_receipt_comparator.py tests/test_phase93o_handoff_rerun_audit_gate.py -q
# 46 passed
```

```bash
python -m pytest tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py tests/test_phase92r_observer_response_semantic_joiner.py tests/test_phase92s_cross_observer_conservation_reconciler.py tests/test_phase92t_runtime_finding_evidence_packager.py tests/test_phase92u_runtime_finding_customer_triage.py tests/test_phase92v_runtime_customer_report_builder.py tests/test_phase92w_runtime_reproduction_asset_linker.py tests/test_phase92x_runtime_fix_verification_loop.py tests/test_phase92y_runtime_finding_lifecycle_registry.py tests/test_phase92z_runtime_remediation_artifact_builder.py tests/test_phase93a_runtime_onboarding_preflight.py tests/test_phase93b_runtime_probe_capability_matrix.py tests/test_phase93c_onboarding_remediation_kit.py tests/test_phase93d_runtime_execution_runbook.py tests/test_phase93e_runtime_evidence_readiness_sla_gate.py tests/test_phase93f_runtime_sla_execution_policy.py tests/test_phase93g_runtime_sla_gap_prioritizer.py tests/test_phase93h_onboarding_patch_safety_validator.py tests/test_phase93i_write_sandbox_approval_packet.py tests/test_phase93j_commercial_handoff_bundle.py tests/test_phase93k_commercial_handoff_acceptance_gate.py tests/test_phase93l_handoff_secret_audit.py tests/test_phase93m_handoff_archive_manifest.py tests/test_phase93n_handoff_receipt_comparator.py tests/test_phase93o_handoff_rerun_audit_gate.py -q
# 84 passed
```

```bash
python -m pytest tests/test_strict_document_grounding.py tests/test_discovery_finding_gate.py tests/test_business_invariant_mining.py tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py tests/test_phase92r_observer_response_semantic_joiner.py tests/test_phase92s_cross_observer_conservation_reconciler.py tests/test_phase92t_runtime_finding_evidence_packager.py tests/test_phase92u_runtime_finding_customer_triage.py tests/test_phase92v_runtime_customer_report_builder.py tests/test_phase92w_runtime_reproduction_asset_linker.py tests/test_phase92x_runtime_fix_verification_loop.py tests/test_phase92y_runtime_finding_lifecycle_registry.py tests/test_phase92z_runtime_remediation_artifact_builder.py tests/test_phase93a_runtime_onboarding_preflight.py tests/test_phase93b_runtime_probe_capability_matrix.py tests/test_phase93c_onboarding_remediation_kit.py tests/test_phase93d_runtime_execution_runbook.py tests/test_phase93e_runtime_evidence_readiness_sla_gate.py tests/test_phase93f_runtime_sla_execution_policy.py tests/test_phase93g_runtime_sla_gap_prioritizer.py tests/test_phase93h_onboarding_patch_safety_validator.py tests/test_phase93i_write_sandbox_approval_packet.py tests/test_phase93j_commercial_handoff_bundle.py tests/test_phase93k_commercial_handoff_acceptance_gate.py tests/test_phase93l_handoff_secret_audit.py tests/test_phase93m_handoff_archive_manifest.py tests/test_phase93n_handoff_receipt_comparator.py tests/test_phase93o_handoff_rerun_audit_gate.py -q
# 94 passed
```

```bash
python -m compileall -q ai_test_asset_center tests/test_phase93m_handoff_archive_manifest.py tests/test_phase93n_handoff_receipt_comparator.py tests/test_phase93o_handoff_rerun_audit_gate.py
# passed
```
