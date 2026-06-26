# Phase92P — Business Invariant Before/After Auto Adjudicator

## Goal
Make QualiBug judge high-value business bugs from runtime before/after evidence instead of reporting static rules as bugs.

## What changed
- Added `ai_test_asset_center/business_invariant_before_after.py`.
- Integrated Phase92P into `ai_test_asset_center/grounded_probe_executor.py` write-probe verification.
- Added `tests/test_phase92p_business_invariant_before_after.py`.

## Runtime behavior
Phase92P derives proof checks from the grounded probe, the probe plan, and observed snapshot fields. A candidate is promoted only when runtime evidence violates an inferred invariant.

Supported before/after judgements:
- Rejected/forbidden operation must not mutate business state.
- Terminal-state objects must remain immutable after negative state-transition probes.
- Tenant/owner/scope boundary probes must not mutate scoped business objects.
- Resource-like numeric fields observed in snapshots must not become negative.
- Idempotency replay should not produce multiple side-effect identifiers or excess collection growth.

## Why this is not static-rule reporting
The invariant text is not emitted as a bug by itself. Phase92P requires:
1. a document-grounded candidate/probe;
2. an executed sandbox/write probe;
3. before and after snapshots or replay response evidence;
4. a deterministic observed violation.

## Validation
- `python -m pytest tests/test_phase92p_business_invariant_before_after.py -q` → 4 passed
- `python -m pytest tests/test_auto_test_data_factory.py tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py -q` → 16 passed
- `python -m pytest tests/test_strict_document_grounding.py tests/test_discovery_finding_gate.py tests/test_business_invariant_mining.py tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py -q` → 25 passed
- `python -m compileall -q ai_test_asset_center tests/test_phase92p_business_invariant_before_after.py` → passed

Full `python -m pytest -q` was started and progressed, but exceeded the execution window before completion in this environment.

## Next recommended phase
Phase92Q should add a snapshot observer planner that discovers richer collection/account/ledger observers from OpenAPI so Phase92P can evaluate conservation and idempotency with stronger multi-observer evidence.
