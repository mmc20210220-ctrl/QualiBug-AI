# Phase93E/F — Runtime Evidence Readiness SLA Gate + SLA-Gated Execution Policy

## Phase93E

Added `runtime_evidence_readiness_sla_gate.py` to convert onboarding preflight and probe capability data into a customer-facing runtime evidence readiness score.

The gate reports:

- commercial readiness score and level;
- whether the customer runtime SLA gate passes;
- P0/P1 runtime-ready coverage;
- high-value runtime-ready coverage;
- strong before/after evidence expected coverage;
- blocking/warning reasons;
- evidence gaps and recommended customer actions.

The gate only scores evidence readiness. It never marks a behavior as a bug without runtime evidence.

## Phase93F

Added `runtime_sla_execution_policy.py` to convert the SLA gate into a concrete execution policy.

The policy groups probes into:

- `must_run_for_sla`;
- `optional_degraded_probes`;
- `blocked_before_sla`;
- `supplemental_ready_probes`.

It also states whether commercial or conditional runtime SLA can be claimed and prevents degraded probes from being counted as full SLA acceptance coverage.

## Executor outputs

New artifacts:

- `grounded_probe_runtime_evidence_readiness_sla_gate.json`
- `grounded_probe_runtime_evidence_readiness_sla_gate.md`
- `grounded_probe_runtime_sla_execution_policy.json`
- `grounded_probe_runtime_sla_execution_policy.md`

Executor version:

- `grounded_probe_executor_v21_phase93f`

## Validation

- `tests/test_phase93e_runtime_evidence_readiness_sla_gate.py` — 3 passed
- `tests/test_phase93f_runtime_sla_execution_policy.py` — 3 passed
- Phase93A-F onboarding/runtime SLA suite — 19 passed
- Phase92P-Z + Phase93A-F core runtime suite — 57 passed
- Extended strict grounding/discovery/invariant/runtime suite — 67 passed
- `compileall` over `ai_test_asset_center` and new tests — passed
