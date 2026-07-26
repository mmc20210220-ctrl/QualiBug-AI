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

## What would actually move coverage

In order of measured blocking weight, not guesswork:

1. Bind invariants to operations (174 + 20 obligations). Until a rule references
   something callable, no amount of comprehension reaches the target.
2. Declare observers for the effects obligations assert (123 obligations).
3. Generate obligations for facts the product already observes but discards — H1 is the
   proof case: the login status was read and no assertion was compiled from it.
4. Enable the browser UI probe. Two live frontends were never touched; the only
   remaining coverage gap is `E_BROWSER_UI_DISABLED`.

## Reproducing

Boot the target (`03_start_all.bat`), start `qualibug-server`, then:

```bash
curl -s -X POST http://localhost:8088/api/v1/scan -H "Content-Type: application/json" -d '{"project_id":"benchmark_mall_131","base_url":"http://localhost:8080","approved_base_url":"http://localhost:8080","environment_type":"test","environment_ref":"sandbox","execution_mode":"approved_sandbox_write"}'
```

Score with the benchmark's own scorer. Product quality remains `NOT_MEASURED` in the
product's own reporting; the number at the top of this file is an external evaluator's,
which is the only kind that counts.
