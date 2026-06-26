# Phase75 Verification

## Targeted execution proof

Two isolated local disposable-sandbox services were used. They were created in
temporary directories and are not part of the delivery package.

1. **Direct invalid quantity scenario**
   - PRD/API declared `quantity > 0`.
   - The Agent Loop compiled two bounded mutations (`0`, `-1`) into canonical
     experiment packets.
   - After explicit sandbox approval, the target accepted both mutations.
   - Both parent ledger items moved to `EVIDENCE_CAPTURED`; no automatic human
     confirmation occurred.

2. **Fixture-backed BOM scenario**
   - PRD/API declared `componentQty > 0` and `materialCode` must reference an
     existing material.
   - The explicit fixture catalog created a material and captured its code.
   - The runner injected that valid code into the BOM request while preserving
     the foreign-key field during owned-key namespacing.
   - The target accepted invalid component quantity and the parent item moved
     to `EVIDENCE_CAPTURED` with a redacted fixture receipt.

## Regression checks

- `tests/test_agent_discovery_loop.py`: 4/4 passed.
- Required regression subset:
  `tests/test_deep_bug_mining.py tests/test_bug_validation_queue.py tests/test_product_ui.py`: 11/11 passed.
- Python compilation passed for `agent_discovery_loop.py`,
  `agent_experiment_runner.py`, `document_contract_fuzzing.py` and the CLI.

## MES document compilation proof

Without reading any benchmark truth or target source, MES PRD/API inputs
produced 66 scenario packets:

- 3 read-only role-boundary packets;
- 63 sandbox packets;
- 34 packets have no unresolved foreign-fixture dependency;
- 32 packets are explicitly `BLOCKED_BY_FIXTURE` until a project owner adds
  approved fixture bindings.

No MES target request was made during packet compilation.

## External boundary

No external LLM provider was used. Provider configuration remains unverified
until a private deployment receives a real health response.

## Full measured release verification

`python -m aitestops.cli verify-release` completed in an isolated process:

- `compileall`: passed;
- full regression suite: **99/99 passed**;
- product UI regression: passed;
- customer-visible text quality: passed;
- private service smoke: passed;
- `PHASE75_RELEASE_MANIFEST.json`: `overall_status=passed`,
  `release_ready=true`.
