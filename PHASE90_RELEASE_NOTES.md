# Phase90 Release Notes — Product Capability Hardening

## Scope

Phase90 hardens QualiBug's enterprise business Bug-discovery product line. It
focuses on reliability, evidence integrity, policy safety, shared-test
environment governance and delivery hygiene rather than adding unverified
surface features.

## Implemented hardening

- Windows detached-worker logging fallback that cannot terminate a Reasoner run.
- Durable Loop Runtime terminal-state reconciliation, SQLite lease and heartbeat
  semantics.
- Reader Artifact Builder/Waiter single-flight protection and stale-context
  fallback.
- Hypothesis schema isolation, preventing malformed model output from aborting a
  Discovery stage.
- Sandbox idempotency, illegal-state and authorization non-mutation regressions.
- Mandatory finding lifecycle: deduplication, deterministic adversarial checks,
  independent evidence verification and schema validation before human review.
- Strict policy promotion gate: paired replay and shadow evidence, zero safety
  incidents, zero production requests, zero cleanup failures and quality guards.
- Business Risk Coverage Map to prioritize unexplored surfaces and evidence gaps
  without repeatedly amplifying already-closed findings.
- Test Run Session, explicit cleanup plan/evidence and dirty-environment block.
- Phase90 release-package builder, package audit and CI workflow.

## Security boundary

- Production environments remain zero-HTTP-request zones.
- Product self-repair is for local/internal development only.
- Customer deployments may evolve discovery policy only inside the explicit
  policy allowlist; they may not mutate QualiBug source code or lower evidence,
  safety, review or cleanup gates.

## Release status

This document does not claim GA readiness. Use `python -m aitestops.cli
verify-release` and `PRODUCT_90_VERIFICATION.md` for measured status.

## Measured verification

- Full regression suite: 338 passed, 1 skipped in 77.18s.
- Final Release Verifier: passed (compileall 1.286s, pytest 81.018s, UI 3.092s, smoke 0.662s).
- Package audit: passed; runtime state, credentials, compiled artifacts, logs
  and corrupted non-UTF8 filesystem residue are excluded from the archive.
- Controlled repository score: 90/100; see `PRODUCT_90_SCORECARD.json`.
