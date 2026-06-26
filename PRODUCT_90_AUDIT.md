# Product 90% Capability Audit

## Baseline

This audit records the Phase90 engineering hardening work against the product
mission: sustained discovery of real, reproducible, evidence-backed enterprise
business defects in private test environments.

## Hard gates

| Gate | Expected evidence |
|---|---|
| Runtime correctness | Lease, heartbeat, final-state consistency, detached Windows logging tests |
| Reader resilience | Single-flight, stale artifact and degraded-context tests |
| Semantic evidence | Before/action/after, bindings, observers and invariant evidence |
| False-positive control | Deduplication, adversarial validation, finding schema and human-review transition |
| Policy safety | Paired observed replay/shadow evidence plus safety, cleanup and quality gates |
| Test environment governance | Explicit cleanup evidence and dirty-environment write block |
| Production protection | Zero HTTP-request test |
| Delivery hygiene | Measured release verifier and ZIP audit |

## Scoring policy

The final score is computed in `PRODUCT_90_SCORECARD.json` only from measured
test/release evidence. A missing, failed or externally blocked hard gate caps
the score below 90.
