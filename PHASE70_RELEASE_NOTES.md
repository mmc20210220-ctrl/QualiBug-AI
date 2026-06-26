# Phase70 Release Notes — Inventory Reservation and Available-Stock Conservation

## Objective

Phase70 improves QualiBug's ability to detect high-impact inventory defects
that individual order, reservation and inventory APIs can all hide: inventory
reserved totals drifting from active reservation detail, available quantities
being calculated incorrectly, silent oversell, and active reservations without
a matching stock fact.

It does not add an inventory runtime, write probe, extra UI surface, or a
parallel business engine. It extends the existing
`business_causality_conservation` contract, evidence, risk-planning and
release-gate path.

## Change

A new enterprise-configured contract type is available:

```text
inventory_reservation_balance
```

The contract executes only when the enterprise explicitly supplies:

- collection GET paths for inventory snapshots and reservation detail;
- inventory and reservation join keys, such as SKU plus warehouse;
- on-hand, reserved and available stock fields;
- reservation quantity and status fields;
- the list of active reservation states; and
- an explicit tolerance and backorder policy.

Against complete snapshots, the Oracle verifies:

```text
sum(active reservation quantity) == snapshot reserved
available == on_hand - reserved
available >= 0  unless allow_negative_available is explicitly true
```

It emits evidence-backed P1 findings for reservation-quantity drift,
available-stock formula drift and active reservations without a stock snapshot.
It emits a P0 `inventory_oversell_risk` only when the enterprise explicitly
disallows negative available inventory.

## Safety and evidence boundary

All live execution remains GET-only and is subject to the existing shared
safety verdict. Production, undeclared and unsafe targets are blocked before
the first network request. The Oracle requires complete source and dependent
snapshots; duplicate keys or incomplete mappings are skipped with an
observation instead of being promoted to a defect.

Evidence contains field mappings, aggregate quantities, tolerance and hashed
stock identities. It does not persist SKU values, warehouse values, order IDs,
reservation IDs, raw rows, request headers or credentials. LLM output remains
candidate-only and cannot create formal findings.

## Release state

The deterministic inventory contract path is engineering-validated. Real LLM
provider health remains deployment-specific and unverified in this isolated
runtime; it is not required for the deterministic Phase70 Oracle.
