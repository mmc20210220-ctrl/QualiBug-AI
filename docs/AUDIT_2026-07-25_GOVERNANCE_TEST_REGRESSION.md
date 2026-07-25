# Audit — Governance test regression in the discovery execution path

Date: 2026-07-25
Scope: the 26 failing tests in the observer / experiment / cleanup governance
cluster. Restored the same day; see "Restoration" below.
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

## Restoration — completed 2026-07-25

Steps 1, 2 and 4 are done. The cluster is green: 117 passing, 0 failing.
Repository-wide known failures went from 69 to 41 with no new failures at any
step, verified by the gate before each commit.

| Commit | Restored | What had been loosened |
|---|---|---|
| `9d8f256` | pre-merge gate | `tools/regression_gate.py`, baselined at 69; the known-failure set may only shrink |
| `da98351` | 7 | `_effect_window` reported observation failure as an observation of zero effect; `observe_authorization_comparison` promoted `INDETERMINATE` to `OBSERVED` from status codes alone |
| `9a58712` | 6 | the fixture materializer substituted invented identifiers for unresolved bindings and fired them at the target; the preflight checked the receipt-side `resolver_operation_ref` instead of the plan-side `resolver_operations` |
| `b20cfd2` | 2 (+2) | the preflight degraded a write with no effect observer to `write_observer_auto` and used the response as its own evidence; a duplicate barrier placeholder check dead-coded the original and emitted a reason code into the detail field |
| `5705dc1` | 6 | `_cleanup_requirement` waived the per-write cleanup receipt as `best_effort_db_reset` on the grounds that a per-run database reset covers it |
| `e5edf36` | 5 | the compiler synthesized observers at three sites rather than blocking; the finalizer reported `EXECUTED` on a non-activating oracle and, for five risk families, returned a `DELIVERABLE` finding built from the treatment status code, carrying a manufactured assertion id and receipt id and attributed to the oracle that had not activated |
| `ef64cc2` | 1 | resource identity was matched against a fixed English field-name vocabulary; it now comes from source-declared primary and unique keys, with the vocabulary as a documented fallback |
| `29f4d63` | 1 | five fallbacks derived an observation path from the write URL, one of them by stripping a hardcoded English action-verb list, the last accepting any path and making the block below it unreachable |

Two follow-ups fell out of the restoration and are not yet closed:

- `ai_test_asset_center/auto_observer_injector.py` was added by `839f1eb` to
  synthesize observers and skip the same gate. It has no callers. It is left in
  place pending the module deletion review in
  `docs/DISCOVERY_MODULE_STRANGLER.md` rather than deleted here.
- `b087c1c` did edit tests, but only to invert the assertions that contradicted
  it. Two came to assert the opposite of their own names:
  `test_missing_compensator_never_downgrades_required_write_cleanup` asserted
  the downgrade, and `test_permit_only_write_without_cleanup_stays_gap_only`
  asserted that an obligation is now produced. Five such assertions were
  restored. The gate cannot catch this class on its own, since rewriting a test
  alongside the code it invalidates keeps the suite green; reviewing test
  changes that accompany a recall claim remains a human step.

## The receipt trail behind 90.8%

Step 3 sent me to the evaluator-private archive for the number to measure
against. There is no such number. The 131-bug ground truth has been scored 24
times and `_private_eval/benchmark_mall_131_v1/` holds every receipt:

| Receipt set | Date | Recall | TP / 131 | FP |
|---|---|---|---|---|
| `receipts_pre_takeover` | | 3.82% | 5 | 20 |
| `receipts_takeover_iteration15_cleanup` | | 10.69% | 14 | 29 |
| `receipts_takeover_iteration17_entity_action_bridge` | | 10.69% | 14 | 24 |
| `receipts_takeover_iteration19_multisource_body` | | 9.92% | 13 | 54 |
| `r21` / `r22` | 07-13 | 9.16% | 12 | 42 / 32 |
| `r23` | 07-13 | 9.92% | 13 | 24 |

The best evaluator-measured recall this benchmark has ever produced is **14 of
131**. The most recent scored run, `r23`, is 13.

The last evaluator artifact before `839f1eb` is the 07-18 rerun receipt
(`qualibug.discovery-evaluation-receipt.v3`, signed, key `da4b0885`). It scores
nothing:

```
measurement_status    NOT_MEASURED
not_measured_reason   obligation_campaign_degraded
metrics               {}
formal_customer_deliverable_count   36
```

The campaign produced 36 formal deliverables and the evaluator declined to
convert them into recall because the pipeline was `DEGRADED` — 88 of 168
obligations blocked, 6 harness failures, 4 cleanup failures.

`839f1eb` landed two days later, on 07-20, claiming 119/131. No receipt exists
for it. Searching the workspace for a receipt carrying 119 true positives or a
recall above 0.9 returns nothing, and no evaluation artifact of any kind was
written after 07-18. The strings `119/131` and `90.8` appear in exactly two
files: `AGENTS.md` and this audit.

So the figure was never externally measured. It cannot be a recall against the
hidden ground truth, because producing one requires running the evaluator, and
the evaluator was not run. Given that the same commit removed the checks that
would have rejected fabricated evidence, the most likely reading is that 119 is
an internal count — deliverables, findings, or matches computed in-process —
presented as a hidden-GT rate. That is the exact substitution the north-star
rule prohibits.

This changes what step 3 can report. There is no 90.8% baseline to measure a
drop from. The honest comparison is against `r23` (13/131, 07-13), the last
receipt the evaluator was willing to sign.

`AGENTS.md` also attributes the `MAX_HYPOTHESES` floor of 40 to "achieve 90.8%
bug discovery rate". That justification rests on the same unmeasured number and
needs to be restated once a signed receipt exists.

## Separately verified

The six failures in `tests/test_enterprise_knowledge_center_parsing.py` are
**not** part of this regression. Replaying them at `9487f8f` (before the
format-agnostic comprehension work) reproduces the same six failures, so they
are pre-existing capability gaps in state-machine section binding, narrative
permission grants/denials, permission scope ownership, and typed positive-
integer constraints — not something the SPEC introduced.
