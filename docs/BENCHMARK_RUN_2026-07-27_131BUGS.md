# Benchmark run against a live 131-defect enterprise target

## The headline, stated plainly

Scored by the benchmark's own `scripts/score_qualibug_output.py` against its hidden
answer key:

```
ground_truth_total        : 131
reported_total            : 2
matched_total             : 0
coverage_rate             : 0.0
estimated_false_positives : 2
```

**Zero of 131.** The two published findings matched nothing and are counted as false
positives. This is the product's real end-to-end result against a real target, and no
reading of the numbers improves it.

What the run *did* produce is a measured, attributed account of why — which is the
part that can be acted on.

## The target

`qualibug_enterprise_benchmark_v0_5_windows_native_stable`, run natively on Windows:
11 Node microservices behind a gateway on `:8080`, two React frontends on `:3001` and
`:3002`, Postgres on `:5432`, 13 tables seeded. 9 enterprise documents plus the real
`schema.sql`/`seed.sql` DDL. 8 role accounts. 131 seeded defects, answer key hidden.

Discipline held throughout: nothing under `hidden_ground_truth/` was read at any point
by this agent or by the product. Only the scorer reads it, after the fact. The
benchmark's own README requires this, and a contaminated measurement would have been
worth less than no measurement.

## Where the run actually stops

The obligation attempt ledger, 435 obligations, counted by terminal reason:

| count | terminal reason | what it means |
| ---: | --- | --- |
| 174 | `MISSING_PRIMARY_OPERATION` | the rule is understood; no API operation is bound to it |
| 123 | `BLOCKED_MISSING_OBSERVER` | no declared way to observe the effect |
| 38 | `BLOCKED_MISSING_BINDING` | a required value could not be resolved |
| 33 | `BLOCKED_NON_REVERSIBLE_WRITE` | the write has no proven compensation |
| 20 | `BLOCKED_MISSING_OPERATION` | |
| 15 | unlabelled | |
| 14 | `BLOCKED_BINDING_GRAPH_INVALID` | |
| 8 | `BLOCKED_CLEANUP_CONTRACT_DRIFT` | |
| **7** | `ORACLE_NOT_VIOLATED` | **executed against the target; it behaved correctly** |
| 3 | `BLOCKED_CONFLICTING_SOURCE` | |

Seven obligations reached a real verdict. That is the honest measure of reach: 7 of 435.

**This table did not exist before this run.** Those 174 were previously reported as
`OBLIGATION_NOT_IN_PLAN` with `not_in_plan_reason: BUDGET_EXHAUSTED` — on a branch
reached only when the obligation is *not* pending for budget, with a budget of 230 and
149 used. The top blocker was labelled as a capacity problem the system did not have.
Fixing that label is what made the real ceiling visible.

The ceiling is the rule-to-surface binding: 107 of 111 Behavior IR invariants carry
`operation_refs == []`, recorded as 106 `SOURCE_INVARIANT_OPERATION_UNBOUND` gaps. The
product reads the business rules and cannot reach them, because nothing connects a rule
to a callable operation. That is the concrete form of "breadth is limited".

## Defects fixed in the product during this run

Each was found by tracing a real failure against the live target, not by reading code
speculatively.

1. **Login was hardcoded to `username`.** Two credential paths built
   `{"username": ..., "password": ...}` with no way to change the key, while
   `runtime_connectivity_auth_preflight` had supported a configurable `username_field`
   all along. Measured: `username` → 401, `email` → 200. Every authenticated probe
   degraded to unauthenticated and nothing said "could not log in".
2. **The SSRF guard blocked the approved target.** `target_policy` approved
   `localhost:8080`; `safe_urlopen` refused it as internal. Two gates disagreeing about
   one host. Resolved by making `target_policy` answer "is this the approved target"
   and narrowing the grant to that host — not by reaching for
   `QUALIBUG_SSRF_ALLOW_INTERNAL`, which grants every host at once.
3. **The campaign bound to one source document.** The auto-bind iterated the registry
   and `break`-ed on the first asset, so nine ingested documents scanned as one.
   Preflight still reported "sources: 9 passed".
4. **Stored tokens were presented as live.** `load_actor_tokens` returned bearer tokens
   without checking `exp`; this project's were four days expired. 47 of 49 execution
   steps died on 401, and **four of the five resulting P1 "authorization" findings were
   the harness's own auth failures** — all four endpoints answer 200 with a fresh token.
5. **The markdown login fallback was dead code.** It required
   `QUALIBUG_TARGET_BASE_URL`, which the HTTP scan entrypoint never sets.
6. **The credential catalog could not read its own file shape.** `{"accounts": [...]}`
   is what the ingest API writes; the loader's dict branch kept only dict values and
   that value is a list, so eight accounts loaded as zero.

Measured effect, run 3 → run 4: `test_data_plan` `blocked_with_testability_gap` →
`ready`; coverage gaps 3 → 1; obligation execution 14/354 → 22/435; credential
verification 0/8 roles → 8/8.

None of this moved coverage off zero. Both facts are true at once and both belong in
the report.

## The gate did its job

Five findings passed the content gate in run 3 and the release gate refused to publish
them, with the reason *"发布被阻断：5 个缺陷通过交付门禁但未发布，空缺陷列表不代表目标无缺陷"*.
Four of those five were fabrications caused by defect #4.

A gate that blocks four false P1s from reaching a customer is the single most valuable
behaviour observed in this run. It must not be relaxed to raise a coverage number. Every
fix above is upstream of it.

## Two real target defects, found by hand — not by the product

Recorded separately and labelled as such, because attributing a human finding to the
product is the same false-PASS pathology this codebase exists to prevent.

**H1 — a DISABLED account can log in.** `docs/TEST_ACCOUNTS.md` states the account
"状态为 DISABLED，原则上不能登录和下单". `POST /api/auth/login` with
`disabled_buyer@example.com` returns HTTP 200 and a JWT whose own payload carries
`"status":"DISABLED"`. The service reads the status, embeds it in the token, and does
not gate on it. The DB confirms `users.status = 'DISABLED'`.

**H2 — the public catalog serves unpublished products.** `GET /api/products` with a
buyer token returns 8 products, including `SKU-DRAFT-006` (status `DRAFT`, price
1999.00), `SKU-HIDDEN-005` (status `OFF_SALE`), and two with status `DELETED`.

H1 surfaced from the credential health check — the product's own verification logged
`disabled_buyer: true` — but no obligation was generated to assert it, so the product
never reported it. That gap is more interesting than the defect: the fact was observed
and discarded.

A side note from H2: the two `DELETED` rows are `qb_auto_sku_QBBOOTSTRAP_*`, the
product's own governed writes. Cleanup soft-deleted them and they remain visible in the
catalog.

## Backend optimisation pass — the funnel, measured at each step

Seven runs against the same live target. Each fix moved obligations to the next gate,
which is the honest shape of progress here: reach improved every time, verdicts did not.

| run | obligations | top blocker | count | findings |
| --- | ---: | --- | ---: | ---: |
| 3 | 354 | `MISSING_PRIMARY_OPERATION` | 174 | 1 |
| 4 | 435 | `MISSING_PRIMARY_OPERATION` | 174 | 2 |
| 5 | 1218 | `BLOCKED_MISSING_OBSERVER` | 463 | 4 |
| 7 | 1218 | `BLOCKED_NON_REVERSIBLE_WRITE` | 575 | 4 |
| 8 | 1218 | `BLOCKED_NON_REVERSIBLE_WRITE` | 686 | 4 |

What each step bought:

1. **One semantic lexicon instead of two.** Two copies sat at different paths, read by
   disjoint module sets, with 30 conflicting verb meanings and each holding vocabulary
   the other lacked. Unified by union with a superset assertion against both. On the 27
   real documents: permissions 378→412, ownership scopes 12→18, and forbidden
   transitions 0→6. Those six moved *out* of the allowed-transition count — 58+0 and
   52+6 both total 58 — so six false-defect generators became six real questions.
2. **Prose invariants joined to operations.** The largest blocker at 40% of the run.
   Invariants with refs 6→102, obligations 435→1218, and four risk families that had
   produced literally zero obligations began producing them.
3. **Observation surfaces declared, not hardcoded.** The IR said no database while
   `adapter_capability` said yes off the same config. Fixed, verified — and it moved
   nothing, 463→463. Declaring a surface is necessary and not sufficient.
4. **Cross-entity readback.** A write is observable through the entity it references:
   `POST /api/payments/pay` declares `orderId` and moves that order to PAID, so
   `GET /api/orders/{id}` is its readback. `BLOCKED_MISSING_OBSERVER` 463→237, with
   exactly 226 landing on the next gate.
5. **Data-layer readback.** `SURFACE_DATABASE_OBSERVER` was in the allowed surface set
   with a cost weight and no finder ever emitted it. The refund endpoints are the case
   it exists for: no GET for refunds anywhere in the source, while `refunds` is a real
   table with a real key. `BLOCKED_MISSING_OBSERVER` 237→122.

Across the pass, `BLOCKED_MISSING_OBSERVER` went **463 → 122**, a 74% reduction, and
observable writes went 9 of 17 to 13 of 17. The four still unresolved are correct:
login, register and coupon-validate produce no durable entity to read back, and
`POST /api/item` is a spurious operation.

Two silent failures were removed on the way: an obligation the compiler deferred with
`MISSING_PRIMARY_OPERATION` was relabelled `BUDGET_EXHAUSTED` on a branch reached only
when budget was *not* the constraint, and the readback resolver was called inside
`except Exception: pass`, so any defect in it became an indistinguishable
`BLOCKED_MISSING_OBSERVER` — the one code that already explained most blocks.

`auto_observer_injector.py` turned out to be entirely dead, all five public functions
unreferenced. Its own fallback was the reason: it returned
`observation_mode: "response_body_only"` when no read existed, so "cancelling an order
releases its inventory" would have been checked against the cancel response. The
fallback was removed rather than the compiler's refusal weakened.

### The current blocker, and why it is not being fixed by inference

`BLOCKED_NON_REVERSIBLE_WRITE`, 686 of 1218 after the observer work landed on it. The IR declares exactly **two**
compensation relations — `cancel` compensates `POST /api/orders`, `reject` compensates
`POST /api/refunds` — and `resolve_compensation_relation` resolves nothing for any of
the 17 writes, because it requires `SOURCE_EXPLICIT` evidence.

An inverse-action table (cancel↔create, refund↔pay, release↔reserve) would unblock
several hundred obligations in one commit. It is deliberately **not** being added.

A compensation relation is the claim "this write can be undone", and cleanup receipts
are part of the evidence chain. A refund does not restore the pre-payment state in a
real system: it leaves audit rows and may not return inventory. Asserting reversibility
from an action name, without the source saying so, is a false PASS about cleanup — and
the `SOURCE_EXPLICIT` requirement exists precisely to prevent it.

The source does state the compensation: BUSINESS_RULES.md says
「已支付订单不能直接取消，只能发起退款」. So the correct next increment is deriving
compensation relations from rule text, the same way invariants are now bound to
operations — not guessing them from a verb table.

## What would actually move coverage

In order of measured blocking weight, not guesswork. Items 1 and 2 of the original
list are now done and are recorded above; this is the remaining list against the
latest run:

1. Derive compensation relations from rule text (686 obligations). The largest
   blocker, and the reason it is not a quick fix is in the section above.
2. Close the remaining observer gap (122 obligations, down from 463).
3. Repair the binding graph (130 obligations, `BLOCKED_BINDING_GRAPH_INVALID`).
4. Generate obligations for facts the product already observes but discards. H1 is the
   proof case: the login status was read, recorded as `disabled_buyer: true` by the
   product's own credential check, and no assertion was ever compiled from it.
5. Enable the browser UI probe. Two live frontends were never touched; the only
   coverage gap outside the ledger is `E_BROWSER_UI_DISABLED`.

## Reproducing

Boot the target (`03_start_all.bat`), start `qualibug-server`, then:

```bash
curl -s -X POST http://localhost:8088/api/v1/scan -H "Content-Type: application/json" -d '{"project_id":"benchmark_mall_131","base_url":"http://localhost:8080","approved_base_url":"http://localhost:8080","environment_type":"test","environment_ref":"sandbox","execution_mode":"approved_sandbox_write"}'
```

Score with the benchmark's own scorer. Product quality remains `NOT_MEASURED` in the
product's own reporting; the number at the top of this file is an external evaluator's,
which is the only kind that counts.
