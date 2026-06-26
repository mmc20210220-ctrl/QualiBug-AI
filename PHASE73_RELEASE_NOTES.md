# Phase73 Release Notes — Document Contract Compilation and AST Source Evidence

## Purpose

Phase73 closes the gap exposed by document-only MES evaluation: a PRD/API can
name business constraints, but a generic keyword scan cannot turn those
constraints into executable, evidence-backed checks.  This phase adds two
small adapters that reuse existing safety, evidence, and validation boundaries
rather than creating a parallel test platform.

## Added capability

### 1. Document contract compilation

`ai_test_asset_center/document_contract_fuzzing.py` compiles explicit PRD/API
constraints and documented JSON examples into bounded sandbox-only mutation
contracts.  It supports nested numeric fields, required collections,
referential integrity, ordered process steps, idempotency and role boundaries.

A 2xx transport response with `success:false` or `ok:false` is now classified
as a protocol-status defect, not incorrectly treated as an accepted invalid
input.

### 2. Optional AST source-contract audit

`ai_test_asset_center/mes_source_contract_audit.py` maps documented
manufacturing constraints to concrete FastAPI handlers when source is supplied
by the customer.  It performs AST semantic analysis, strips comments before
matching, and binds every rule to the documented HTTP method so a GET handler
cannot be mistaken for a POST/PUT/DELETE handler.

It finds direct implementation evidence such as absent state guards, missing
idempotency queries, unsafe client-controlled identity, unscoped default
master-data selection, non-atomic inventory mutation, and incorrect reporting
calculation logic.

## Trust and safety boundaries

- No changes to `safety_boundary.py`.
- Source audit performs no HTTP request and never reads a ground-truth catalog.
- Active mutations still require `environment=sandbox`, a disposable target,
  explicit execution and an approval identifier.
- Static source evidence is kept separate from runtime replay evidence.
  Stateful findings are candidates until deterministic sandbox replay confirms
  the business effect.
- Source comments are discarded by AST parsing and cannot act as evidence.

## Verification

- Required product regression group: 11/11 passed.
- Python compilation passed for both new/updated adapters.
- Method-sensitive audit verification passed: write-side rules bind to the
  documented write handler, not a same-path read handler.
- Document contract execution was exercised only against a disposable local
  sandbox.

## Deliberately not claimed

Phase73 does not claim that static evidence alone proves all runtime effects,
and it does not treat a benchmark catalog as an input to detection.  The next
priority is to convert high-confidence source contracts into reusable
stateful sandbox replays for transaction, concurrency and cross-module
conservation behavior.
