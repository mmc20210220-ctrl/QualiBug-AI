# QualiBug AI Phase64 Release Notes

## Goal

Phase64 strengthens high-value business Bug mining at the authorization boundary
without creating a new runtime, permission service, UI or probe framework. It
extends the existing consistency/isolation engine with explicit role-based GET
oracles and fixes that engine's direct-execution safety gap.

## What Changed

- Added `access_contracts` to `consistency_isolation_reasoning` for explicit
  role/permission expectations on OpenAPI-declared GET endpoints.
- Detects route-level authorization bypass, unexpected non-empty results for
  restricted roles, field-level data exposure, and authorized-role rejection.
- Requires every tested role context to provide independent credentials or
  headers; role checks do not silently inherit the project-wide token.
- Persists only redacted context metadata, HTTP status, field paths and counts;
  raw response fields, header values and tokens never enter profiles or evidence.
- Applied the shared `execution_safety_verdict` to direct consistency-engine
  execution. Production or undeclared targets are blocked before any HTTP call.
- Converted this engine's LLM output to `unverified_hypothesis` only, matching
  the existing evidence-first defect policy.

## Verification

- Adversarial role test: a sales viewer configured as denied receives HTTP 200
  on a finance report. Phase64 emits a deterministic `role_access_denied` P0
  counterexample.
- Adversarial field test: a support viewer may read the report but receives a
  non-empty `gross_margin` field. Phase64 emits deterministic
  `field_authorization_leak` evidence without persisting the value.
- Production-declared target: execution is blocked before any GET request.
- Focused consistency/permission tests: **5/5 passed**.
- Cross-engine regression (consistency, metamorphic differential,
  reconciliation, temporal regression, product UI, release verifier):
  **26/26 passed**.
- Business-core regression (invariants, multi-source, lifecycle, causality,
  population, event chain, Saga, coverage, flywheel and validation queue):
  **37/37 passed**.
- Python source compilation completed for application packages.

The four non-overlapping regression groups cover **92/92 collected tests**.
A single-process `pytest -q` run in this container still stalls after the
self-dogfood audit begins without an assertion failure; isolated and
adjacent-module self-dogfood runs pass. This is a controlled engineering
increment for private enterprise validation, not a GA sign-off. CI must run the
canonical full release verifier in a clean environment before approval.
