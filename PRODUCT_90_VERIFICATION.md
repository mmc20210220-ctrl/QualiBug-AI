# Phase90 Product Capability Verification

## Measured verification run

The following results were executed in the Phase90 worktree after the runtime,
Reader, Sandbox, Finding Gate, policy-promotion, cleanup and release-hardening
changes.

| Verification | Command / evidence | Measured result |
|---|---|---|
| Full regression suite | `pytest -q` | **338 passed, 1 skipped** in 77.66s |
| Runtime / Reader / Sandbox / Finding / Cleanup hardening suite | targeted Phase90 test group | **92 passed** |
| Policy promotion and coverage map | `pytest -q tests/test_policy_promotion_gate.py tests/test_business_risk_coverage_map.py tests/test_phase81_evolution.py` | **41 + 43 focused regressions passed** across the two runs |
| Release verifier | `python -m aitestops.cli verify-release` | **passed**; compileall, full suite, UI, text and private-service checks all passed |
| Product UI | release verifier check | **3 passed** |
| Private service smoke | release verifier check | dashboard/control-plane/knowledge/release/benchmark pages and read-only APIs returned 200 |
| Package audit | `python build_release.py ...` and `audit_release_package(...)` | **passed**, 427 archive files, zero audit violations |

## Runtime and evidence hard gates

- Detached/invalid Windows stdout does not terminate the Reasoner worker; it falls
  back to a file logger.
- Terminal run status reconciles heartbeat, result and runtime state; failed runs
  cannot be recorded as `CONVERGED` and cannot mutate learning/promotion state.
- Reader Artifact cache uses a Builder/Waiter single-flight path, stale artifact
  fallback and `CONTEXT_PENDING`/`DEGRADED_CONTEXT` rather than a Loop crash.
- Sandbox coverage verifies idempotency, rejected state invariance and
  authorization non-mutation semantics.
- Direct Discovery findings, business-flow findings and agent-loop candidates
  are gated by deduplication, adversarial checks, evidence validation and the
  Business Finding Schema before human confirmation.
- Policy activation requires observed paired replay and shadow evidence, a
  versioned dataset, matching input/fixture/context fingerprints, enough
  samples, zero production requests, zero cleanup failure/dirty environment and
  no quality regression.
- Shared-test-environment write flows require explicit cleanup mapping/evidence;
  cleanup failure marks the environment dirty and blocks subsequent high-risk
  writes.
- Production safety regression verifies production HTTP request count remains 0.

## External validation boundary

The suite validates the engine in local controlled Sandbox/fixture conditions.
The remaining validation is external, not an unimplemented core gap:

1. Run the packaged product for 24–72 hours against a customer-approved test or
   pre-production environment with a real DeepSeek/network route.
2. Feed a real, versioned replay corpus and shadow-run receipts into the strict
   policy-promotion gate before enabling autonomous policy promotion.
3. Validate customer-specific cleanup adapters and approval scopes without
   using production data or production HTTP requests.

These external runs are not represented as passed in this repository.

## Final release verifier

`python -m aitestops.cli verify-release` completed with `overall_status=passed`.

```text
compileall             passed   1.286s
pytest                 passed  81.018s  (338 passed, 1 skipped)
product_ui_tests       passed   3.092s
customer_text_quality  passed   0.018s
private_service_smoke  passed   0.662s
```
