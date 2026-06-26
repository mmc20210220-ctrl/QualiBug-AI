# Phase92F — Deep Probe Frontier + Regression Assets

This patch continues from Phase92E without changing any currently running background daemon.

## What changed

1. Added `bug_engine_deep_probe_planner.py`.
   - Builds a safe next-frontier plan from validated findings and OpenAPI route metadata.
   - Covers four commercial bug classes: authorization boundary regression, state transition bypass, idempotency replay, and amount/inventory conservation.
   - Mutating/stateful probes are only planned as `disposable_sandbox_required`; autorun does not execute them silently.

2. Autorun now emits:
   - `deep_probe_plan.json`
   - `deep_probe_plan.md`

3. Customer report now emits a CI regression test asset:
   - `validated_bug_regression_pytest.py`
   - The generated pytest file asserts the fixed behavior for read-only validated auth findings.

4. `bug-engine-status` now reports whether the deep probe plan exists.

## Verification

Targeted regression suite passed:

```text
48 passed
```

Validated local autorun smoke run:

```text
terminal: COMPLETED_WITH_FINDINGS
validated_candidates: 9
raw_confirmed_signals: 9
needs_more_evidence: 0
customer_report_findings: 8
deep_probe_plan.planned_actions: 24
by_kind: auth_boundary_regression=6, state_transition=6, idempotency_replay=6, amount_inventory_conservation=6
by_execution_policy: read_only_safe=12, disposable_sandbox_required=12
```
