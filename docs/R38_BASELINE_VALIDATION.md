# R38 baseline validation debt

Base: `4e3449e6` (latest origin/main fetched before work, R38-A and R38-B merged).
Local validation: Windows, Python 3.12.7. These results are local execution,
not evidence that a GitHub Actions runner ran. PR #213 is not used.

## Executed checks

- Initial authority + stable identity contracts: 19 passed, 1 failed.
- Corrected authority + stable identity contracts: 21 passed.
- Authority, identity, product binding and Agent Task runtime: 36 passed.
- Related compiler and authority regression selection: 117 passed after fixes.
- Initial complete permanent manifest: 1118 passed, 14 failed.
- Second complete permanent manifest: 1122 passed, 10 failed.
- Third complete permanent manifest after the wrapper/fixture test repairs:
  1126 passed, 6 failed (159.56 seconds; no tests skipped).
- Full gate remains failing; no R38-C/D/E completion or commercial capability
  is claimed by these unit and contract test results.

Reproduce the permanent manifest on PowerShell:

```powershell
$env:QUALIBUG_JWT_SECRET='ci-quality-gates-secret'
$env:QUALIBUG_LOCAL_DEV_ACTOR='1'
$env:QUALIBUG_ALLOW_PUBLIC_BIND='0'
$gateTests = @(Get-Content .github/quality-gates-backend-tests.txt |
  Where-Object { $_ -match '\S' -and $_ -notmatch '^\s*#' })
foreach ($gateTest in $gateTests) {
  if (-not (Test-Path -LiteralPath $gateTest)) { throw "Missing gate test: $gateTest" }
}
python -m pytest -q @gateTests
python -m pytest -q tests/test_agent_task_runtime.py
```

JUnit XML and full captured outputs for this checkout live in
`.scratch/r38-validation/`. They are local diagnostic artifacts, not product
Evidence or customer-run receipts.

## Changes and authority

The only runtime change is in the existing `obligation_source` authority,
`obligation_compiler.compile_obligations_from_behavior_ir`. It recomputes
`obligation_count` and `by_family` after existing consolidation. Previously
35 unbound invariant obligations could become one row while still reporting 35.
The consolidation receipt and all 35 `consolidated_invariant_refs` remain intact.

1. Authority: the existing manifest's `obligation_source`; no additional slot.
2. Customer call: `discovery_runtime_planning.build_discovery_plan` invokes it
   before the existing `experiment_compiler.compile_experiments` call.
3. Failure: existing compile exceptions and Run error propagation are unchanged;
   this change does not create a new status or a GET fallback.
4. Evidence: the compile bundle's final rows, family counts and existing
   consolidation receipt can be reconciled deterministically. This is planning
   evidence, not proof that a target request executed.
5. Stranger metric: correct Obligation funnel denominator. Improvement to
   executable ratio or real Bug yield is **NOT_MEASURED**, not inferred.

Test repairs preserve PRODUCT fail-closed rules:

- An unloadable release target test now uses the permitted release module with
  a nonexistent callable, so it actually reaches the loadability boundary.
  A separate test retains rejection of a foreign release authority.
- Legacy binding is tested only under explicit COMPATIBILITY mode.
- HTTP harnesses provide their authenticated request tenant and separate cache
  identities instead of relying on missing request-context methods.
- Fanout substitutes are injected into the function's defining namespace.
- Public execution wrappers are tested by delegation and receipt behavior,
  rather than requiring wrapper identity to equal the underlying function.
- Missing fixture identity remains BLOCKED with the exact reason and separate
  cleanup-failure count; it is not converted into a confirmed Finding.

## Remaining baseline investigations

- Conservation compilation emits an ABSTRACT experiment with
  `BLOCKED_MISSING_OBSERVER`; the old test expects a missing-actor block.
  Preserve this as an executability investigation, not a reason to invent a GET.
- The source-overlay family-count test expects two conservation obligations;
  the current compiler produces one. Trace source coverage before changing it.
- Paired evaluator input-drift validation is preempted by missing commercial
  promotion evidence; repair the fixture to exercise authenticated drift.
- The executor facade exceeds the existing 400-line architecture budget (430).
  The budget is not raised by this change.
- Conflicting aliases surface as `SAME_LABEL_MULTIPLE_IDENTITY_CONFLICT` while
  the test expects `TERM_ALIAS_IDENTITY_CONFLICT`; source-evidence preservation
  needs investigation.
- Object authority selection in an in-memory conflict does not substitute for
  the durable operator decision ledger. Verify the canonical decision path.

After the baseline gate is repaired, the requested order remains R38-C
Obligation Mainline, R38-D Executability, then R38-E unfamiliar-system Product
Golden Run. The separate classification audit and subsequent lifecycle,
TestAsset, zero-Planner regression and change-aware work remain outstanding.
No UX convergence work belongs ahead of those gates.
