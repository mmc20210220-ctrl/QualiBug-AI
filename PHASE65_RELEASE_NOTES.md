# QualiBug AI Phase65 Release Notes

## Goal

Phase65 adds high-value financial ledger counterexample discovery without adding a
parallel accounting runtime, data model or UI. It extends the existing
`business_causality_conservation` engine with explicit, evidence-first ledger
contracts.

## What Changed

- Added `journal_balance` contracts: aggregate debit and credit amounts by
  configured voucher identity and currency, then emit P0 evidence when they do
  not balance.
- Added `period_rollforward` contracts: compare configured adjacent periods for
  closing-to-opening continuity, and optionally verify the explicit
  `opening +/- debit/credit = closing` movement formula.
- Financial contracts require explicit field mapping and explicit period order;
  schemas containing similar field names are not promoted automatically.
- Uses `Decimal` arithmetic for monetary comparisons instead of binary float
  arithmetic.
- Adds the shared `execution_safety_verdict` to direct business-causality runs;
  production and undeclared targets are blocked before any GET request.
- Changes this engine's LLM outputs to `unverified_hypothesis` only. LLM text no
  longer joins deterministic `findings` or the evidence registry.
- Reuses the existing risk planner, discovery report, evidence registry,
  confirmation flywheel and private report surface. No new runtime or visual
  subsystem was introduced.

## Business Bugs Found

- Missing debit/credit leg in a voucher caused by retry, consumer duplication or
  partial asynchronous posting.
- A ledger period closes correctly in one endpoint but is not carried into the
  following period's opening balance.
- Period closing balance diverges from the declared opening and debit/credit
  movement formula.

## Verification

- Deterministic accounting adversary: detects an unbalanced voucher, a closing
  balance that does not carry to the next period, and a broken balance movement
  formula without persisting raw voucher or account identities.
- Production-declared accounting target: blocked before the first GET request.
- Focused financial ledger tests: **3/3 passed**.
- Cross-engine regression (financial ledger, causality, metamorphic,
  reconciliation, temporal, consistency, product UI and release verification):
  **31/31 passed**.
- Non-overlapping grouped regression across the complete test inventory:
  **95/95 passed** (29 + 37 + 22 + 7).
- Python source compilation completed for application packages.
- A direct single-process `pytest -q` run reached roughly 95% progress with no
  failed assertion, then stalled during late-suite shutdown in this container.
  It is recorded as a release blocker rather than treated as a pass.

This is a controlled private-enterprise validation increment, not a GA
sign-off. The canonical clean-CI release verifier remains the release gate.
