# QualiBug AI · Phase55 Release Notes

## Confirmed Bug Learning Flywheel

Phase55 turns confirmed enterprise defects into an approval-gated learning and regression loop.

- QA review decisions append to a hash-chained audit ledger.
- Confirmed defects require independent quality-owner approval before they affect probe priority.
- Only approved confirmations create learned patterns and durable regression candidates.
- False positives can only create exact, expiring exceptions; no broad risk-category suppression is allowed.
- Learning artifacts store redacted metadata and hashes, never raw request/response payloads, tokens, or business rows.
- Approved GET/read-only regressions are included in release suites; write-path regressions remain sandbox-required.

## Key files

- `ai_test_asset_center/confirmed_bug_flywheel.py`
- `docs/PHASE55_CONFIRMED_BUG_FLYWHEEL.md`
- `examples/confirmed_bug_flywheel_review.example.json`
- `tests/test_confirmed_bug_flywheel.py`

## Verification

- Full test suite: 48/48 passed.
- Python compile check: passed.
