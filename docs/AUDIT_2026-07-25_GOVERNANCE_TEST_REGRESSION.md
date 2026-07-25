# Audit — Governance test regression in the discovery execution path

Date: 2026-07-25
Scope: the 26 currently failing tests in the observer / experiment / cleanup
governance cluster.
Method: `git bisect` plus a per-commit replay of the cluster across the 74
commits between `6d43d4d` (2026-07-18) and `074dc5e` (2026-07-25), run in an
isolated worktree.

## Verdict

These are **not** outdated assertions. They are a real regression in the
governance semantics of the discovery execution path. The cluster was fully
green as recently as 2026-07-18 and was broken by six later commits, none of
which updated the tests they invalidated.

Cluster under test:

- `tests/test_typed_observer_authorization.py`
- `tests/test_observer_evidence_truthfulness.py`
- `tests/test_family_experiment_protocols.py`
- `tests/test_entity_effect_and_snapshot_cleanup.py`

## Regression timeline

| Commit | Date | Δ failing | Subject |
|---|---|---|---|
| `99e6100` | 07-18 | 0 / 117 pass | last fully green point |
| `b087c1c` | 07-19 | **+7** | feat: enterprise test data infrastructure + pipeline improvements |
| `f716382` | 07-19 | **+1** | feat: extend observer exemption to validation and isolation |
| `e623402` | 07-19 | **+2** | feat: direct path placeholder resolution in preflight |
| `839f1eb` | 07-20 | **+13** | feat: bug 发现率提升至 90.8% (119/131) |
| `e3055dc` | 07-22 | **+1** | feat: enhance bug discovery pipeline |
| `b720ce5` | 07-23 | **+3** | feat: experiment executor enhancements — placeholder interception |
| `074dc5e` | 07-25 | −1 | fix: eliminate 29 latent NameError landmines |

Net: 27 broken, 1 recovered, 26 currently failing.

## The pattern that matters

Every one of the six regressing commits was a benchmark-recall optimization.
None of them changed a single test file.

The largest, `839f1eb`, is explicit about it. Its subject claims a jump to
90.8% (119/131) and lists the mechanism: `authorization 观察器 boolean 值`,
`control/treatment 状态判定`, `consistency 断言别名`, `fixture receipt 选择逻辑`.
Those are the evidence gates themselves. The files it touched are the
governance core:

```
ai_test_asset_center/observer_contracts_base.py       |  63 +-
ai_test_asset_center/sandbox_write_executor_base.py   | 109 +-
ai_test_asset_center/experiment_cleanup_executor.py   | 117 ++
ai_test_asset_center/experiment_compiler_obligation.py| 292 +-
ai_test_asset_center/experiment_plan_executor.py      | 135 +-
ai_test_asset_center/contract_oracles.py              |   2 +-
ai_test_asset_center/customer_delivery_gate_v2.py     |   4 +
tests/                                                |   0 files
```

The 13 tests it broke are exactly the ones that enforce the project's
fail-closed rules: an observer may not report `OBSERVED` without real
evidence, an authorization assertion requires a control plan, a write
requires a governed cleanup receipt, an empty 2xx pair is not a finding.

## What this does and does not prove

Proven:

1. The cluster was green on 2026-07-18 and is red today.
2. Each red step maps to a specific commit, and each of those commits edited
   governance modules while claiming a recall improvement.
3. No commit in the chain updated a test to justify the changed semantics.

Not proven:

- That the 119/131 figure is itself fabricated. What is established is that
  it is **unverifiable**, because the guardrails that would have rejected
  fabricated evidence and ungoverned writes were loosened in the same commit
  that produced the number. Per the north-star rule that quality claims rest
  on externally measured hidden ground truth, this figure cannot be presented
  as commercial capability until the cluster is green again and the run is
  reproduced.

## Recommended sequence

1. Do not rewrite the failing assertions. Treat them as the specification.
2. Restore the semantics commit by commit, largest first (`839f1eb` → 13),
   using the timeline above to isolate each behavioral change.
3. Re-measure benchmark recall only after the cluster is green. Any drop from
   90.8% is the true cost of the loosened gates and must be reported as such.
4. Add the cluster to a pre-merge gate so a recall optimization can never
   again silently disable an evidence gate.

## Separately verified

The six failures in `tests/test_enterprise_knowledge_center_parsing.py` are
**not** part of this regression. Replaying them at `9487f8f` (before the
format-agnostic comprehension work) reproduces the same six failures, so they
are pre-existing capability gaps in state-machine section binding, narrative
permission grants/denials, permission scope ownership, and typed positive-
integer constraints — not something the SPEC introduced.
