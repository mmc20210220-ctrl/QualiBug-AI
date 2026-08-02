# Fact Experimentability + First-loss Ledger (Phase 1)

Living note for SPEC `QB-DISCOVERY-FACT-TO-EXPERIMENT-V1` Phase 1.
Additive observability only: no parallel discovery mainline, no Oracle/execution
behavior change, no benchmark hardcoding.

## Schemas

| Artifact | Schema |
|---|---|
| Per-fact receipt | `qualibug.fact-experimentability-receipt.v1` |
| Aggregate ledger on knowledge asset | `qualibug.fact-experimentability-ledger.v1` |
| Product first-loss ledger (GT-free) | `qualibug.fact-first-loss-ledger.v1` |
| Evaluator GT join | `qualibug.evaluator-fact-first-loss-ledger.v1` |

## Mainline hook

After Business World Model identity closure and before scenario planning:

`enrich_asset_with_enterprise_understanding`
→ `project_business_world_model`
→ `project_fact_experimentability`
→ `project_final_scenario_planning_gate`

Every `ACCEPTED` fact gets exactly one receipt. Descriptive facts are
`NOT_TEST_WORTHY` (never silently dropped). Phase 1 does **not** fail-close
scenario planning on experimentability status.

## Receipt status / blocker codes

`READY`, `NOT_TEST_WORTHY`, `MISSING_PRIMARY_OPERATION`, `AMBIGUOUS_OPERATION`,
`MISSING_ACTOR`, `MISSING_CREDENTIAL`, `MISSING_PRECONDITION`, `MISSING_BINDING`,
`MISSING_FIXTURE`, `MISSING_OBSERVER`, `MISSING_CLEANUP`, `NON_REVERSIBLE_WRITE`,
`UNSAFE_OPERATION`, `INSUFFICIENT_SOURCE_AUTHORITY`, `CONFLICTED_FACT`.

Causal primary operations require an implementation binding. Semantic operation
name similarity is never used as causal binding.

## Planning attach

`discovery_runtime_planning.build_discovery_plan` stamps non-authoritative
`fact_refs` onto obligations/experiments when resolvable from `source_refs`.
This does not change COMPILED/BLOCKED/selection decisions.

## Product artifacts (scan output dir)

Written after the funnel report by `write_fact_tracking_report_files`:

- `fact_experimentability_report.json`
- `fact_experimentability_summary.md`
- `first_loss_ledger.json` (GT-free)
- `first_loss_summary.md`

## Evaluator join

Authenticated `tools/discovery_evaluation.py evaluate` attaches
`metrics.fact_first_loss_diagnostics` from stage-loss matrix mapping into SPEC
§9.2 stages. Every ground-truth bug receives exactly one `first_loss_stage`.
This never changes TP/FP/FN.

## Campaign isolation

Each formal evaluation must mint a new:

- `campaign_id`
- `run_id`
- artifact namespace under `_funnel_runs/<label>/`

Do not reuse prior findings, receipts, or evaluator mappings.

## Fresh 131-bug diagnostic run

Prerequisites:

- QualiBug backend `8088` / frontend `5174` as needed for product UI
- Benchmark target API gateway `8080` (Windows-native held-in profile)
- Frozen runtime bundles under `_private_eval/benchmark_mall_131_v1/runtime/`
- Clean working tree recommended before claiming a frozen commit identity

Commands:

```bash
# 1) Fresh product diagnostic scan (new campaign_id minted inside the script)
python _funnel_runs/20260802_run_fact_first_loss_scan.py

# 2) After scan completes, evaluate with the frozen manifest (evaluator-private)
python tools/discovery_evaluation.py evaluate \
  --manifest _private_eval/benchmark_mall_131_v1/evaluation_manifest.json \
  --envelope <path-to-run-envelope> \
  --output-dir _funnel_runs/<label>/evaluation
```

The scan script writes product fact-tracking reports into its output directory.
Evaluator receipts expose `metrics.stage_loss_diagnostics` and
`metrics.fact_first_loss_diagnostics`. Internal finding counts are not Recall.

## Phase 2 — Abstract Experiment + Runtime Materialization

| Artifact | Schema |
|---|---|
| Abstract experiment | `qualibug.abstract-experiment.v1` |
| Materialization receipt | `qualibug.experiment-materialization-receipt.v1` |

Flow on the existing compile pack (no parallel pipeline):

1. Capability-gap blocks (`BLOCKED_MISSING_FIXTURE` / actor / binding / observer / …)
   become `compile_receipt.status=ABSTRACT` with retained control/treatment arms.
2. `materialize_and_recompile_abstract_pack` resolves planning capabilities from
   Behavior IR + observer registry, emits a materialization receipt, and
   re-invokes the concrete compiler only when the original blocker is covered.
3. Direct `COMPILED` experiments receive a passthrough receipt
   (`SKIPPED_DIRECT_COMPILE`). Selection still requires `COMPILED`.

Modules: `abstract_experiment.py`, `experiment_runtime_materialization.py`,
hooked from `experiment_compiler.compile_experiments`.

## Phase 3 — Fixture / Actor / State / Observer / Cleanup front-load

Before concrete recompile, materialization now reuses:

- `disposable_fixture_contract_core.discover_fixture_candidates` +
  `build_disposable_fixture_contract`
- `enterprise_test_data_constructor.plan_prerequisite_data`
- actor/credential presence (+ optional `load_actor_tokens` when
  `planning_context` has root/project/base_url)
- state establishment steps from obligation property
- `OBSERVER_REGISTRY` implemented observers
- `cleanup_adapter_ladder.resolve_cleanup_adapter(..., availability_only=True)`

`build_discovery_plan` threads `planning_context` into `compile_experiments`.
Receipt fields mark `established_before_concrete_compile=True`. Live HTTP fixture
writes remain on the governed execute path; planning establishes contracts/plans
so concrete compile is not abandoned for missing capability proofs.

## Phase 4 — Oracle Validity Gates + Effect Observation Graph

| Artifact | Schema |
|---|---|
| Validity gates receipt | `qualibug.oracle-validity-gates-receipt.v1` |
| Effect observation graph | `qualibug.effect-observation-graph.v1` |

Hook (after authorization causality, before delivery packaging):

`execute_one_experiment`
→ `enforce_authorization_oracle_causality`
→ `enforce_oracle_validity_gates`
→ authorization delivery packaging

Gates (demote only; never upgrade): Identity, Contrast, Preconditions, Causal,
Evidence. Failed gates set `oracle_verdict.status=INDETERMINATE` with
`pre_validity_oracle_verdict` preserved. Example reason codes:
`VACUOUS_CONTRAST`, `SAME_CREDENTIAL_NO_CONTRAST`, `MISSING_BEFORE_STATE`,
`WRITE_RESPONSE_ONLY_EVIDENCE`.

Effect Observation Graph prefers independent readback over write-response-only
surfaces. Persistence claims without an independent observed readback cannot
remain PROPERTY_HELD/VIOLATION.

Modules: `oracle_validity_gates.py`, `effect_observation_graph.py`.
Post-hoc oracle fields registered in `CONTRACT_ORACLE_POST_HOC_FIELDS`.

## Phase 5 — Runtime Feedback + Blocked Obligation Recompile

| Artifact | Schema |
|---|---|
| Runtime fact candidate | `qualibug.runtime-fact-candidate.v1` |
| Candidate ledger | `qualibug.runtime-fact-candidate-ledger.v1` |
| Feedback receipt | `qualibug.runtime-feedback-receipt.v1` |

Hook on the existing experiment-candidate mainline:

`run_experiment_candidate`
→ surface observation candidates + experimentability re-projection
→ `expand_behavior_ir_from_runtime_observations` (round 2, planning_context)
→ execute round 1 / round 2
→ post-execution Runtime Fact Candidates + re-projection
→ related BLOCKED/ABSTRACT recompile (round 3) via the same expansion authority
→ execute feedback intents

Rules:

- Candidates are `RUNTIME_OBSERVED` only; `high_authority_promotions` is always 0.
- Overlap with documented operations becomes `NEEDS_AUTHORITY`, never ACCEPTED.
- Round-0 body-binding reopen includes Phase-2 `ABSTRACT` retention.
- Expansion may `RECOMPILE` without new operations when recompile ids are present.
- Oracle / delivery bars are unchanged.

Modules: `runtime_fact_candidate.py`; widenings in `discovery_funnel.py`,
`adaptive_behavior_ir_expansion.py`, `discovery_runtime_execution.py`.

## Phase 6 — Frozen 131-bug re-eval

Fresh campaign + evaluator-private scoring (after target DB reset).
Uses runtime bundle `held-in-20260801` (product-scan schema); stub `held-in/`
is not sufficient for `ObservedProductScanExecutor`.

```bash
python _funnel_runs/20260802_run_fact_to_experiment_reeval.py
```

Artifacts under `_funnel_runs/20260802_fact_to_experiment_reeval_<stamp>/`:

- `start_manifest.json`, `target_reset_receipt.json`
- `scan_output.json`, `scan_summary.json`, funnel + fact-tracking reports
- `envelope.v2.json`
- `evaluation/` authenticated receipt
- `evaluation_score_extract.json` (TP/FP/FN extract when present)

Honesty: internal findings are not Recall. Local HMAC without an
evaluator-owned observation gateway is diagnostic, not commercial promotion
evidence.

### Phase-6 diagnostic checkpoints

**20260802T025625Z** (blocked): `pipeline_health=FAILED_SAFE` because
`ASSERTION_INDETERMINATE` and
`BLOCKED_DATABASE_NUMERIC_HTTP_FALLBACK_OBSERVER_MISSING` sealed as
`UNREGISTERED` before registry registration. Formal evaluate
`NOT_MEASURED` / `pipeline_health_failed_safe`.

**20260802T032124Z** (after registry + re-run):

| Field | Value |
|---|---|
| Campaign / run | `CMP_51d8f94a5fea8565c65d5150` / `RUN_21c56000563e25e70a833e16` |
| Pipeline health | `DEGRADED` (execution completed) |
| Funnel conservation | `PASS` |
| Findings / DELIVERABLE terminals | 6 / 12 |
| Fact experimentability | 36 receipts / 17 READY |
| Runtime feedback | `EXPANDED`, 800 candidates |
| Formal evaluate | `NOT_MEASURED` / `evaluator_execution_attestation_missing` |
| Miss diagnosis (evaluator-private) | TP=5, missed=126, recall≈0.038; top loss stage 2 业务模型建立失败 (104) |

Formal MEASURED still requires the evaluator-owned HTTP observation
gateway + sealed `qualibug.evaluator-execution-attestation.v1`. Local
HMAC without that gateway remains diagnostic, not commercial promotion
evidence. Miss-diagnosis TP is evaluator-private and must not be labeled
commercial Recall.

### Phase-6b observed diagnostic (gateway + attestation)

```bash
python _funnel_runs/20260802_run_fact_to_experiment_observed_diagnostic.py
# if scan checkpointed but receipt persist failed (Windows MAX_PATH):
python _funnel_runs/20260802_resume_064251Z_evaluate.py
```

Prefer short output roots / evaluation ids on Windows; deep
`dataset/version/policy/` receipt trees sit near MAX_PATH.

**Root fixes on this path**

1. Declared-source discovery no longer re-ingests nested product knowledge
   assets / duplicate input-tree copies
   (`enterprise_knowledge_center/_linking_impl.py`).
   Dual roots (`platform_inputs/<project>` and `projects/<project>/input`)
   still dedupe by content hash; divergent bytes under the same logical key
   fail closed as `DECLARED_SOURCE_LOGICAL_KEY_CONFLICT` with both paths named
   (no silent merge). T131808Z aborted on `BATCH_LOGICAL_KEY_COLLISION` for
   `markdown_api:api_spec` after `projects/.../API_SPEC.md` gained a DELETE
   address route while `platform_inputs/.../API_SPEC.md` lagged — align those
   copies before the next observed diagnostic.
2. Post-cleanup HTTP readback GET is recorded as a cleanup-phase step with
   `http_attempt_count=1` so operational receipts match gateway observations
   (`experiment_cleanup_executor_core.seal_after_cleanup_observation`).
3. Atomic JSON persist opens same-volume temp files via Windows `\\?\`
   extended paths (`discovery_evaluation_contract._atomic_write_json`);
   `NamedTemporaryFile` under deep receipt dirs still fails past MAX_PATH.
4. Funnel reason codes registered:
   `ASSERTION_INDETERMINATE`,
   `BLOCKED_DATABASE_NUMERIC_HTTP_FALLBACK_OBSERVER_MISSING`,
   `HARNESS_CLEANUP_TRANSPORT_FAILED`.
5. Dual-write cleanup identity is step-scoped (`source_step_id` /
   `cleanup:control_1|treatment_1`) so control+treatment creates no longer
   collapse into `identity_empty` /
   `CLEANUP_ROW_NOT_CREATED_BY_THIS_RUN`.
6. Logical cleanup tables (`payment`/`refund`/`order`) rebind to
   source-declared physical storage names (`payments`/`refunds`/`orders`)
   via Behavior IR `entity.table` stamping from `data_tables` plus
   `information_schema` catalog resolution
   (`behavior_ir_core`, `cleanup_adapter_ladder`, campaign `behavior_ir`
   threaded into adapter cleanup). Ownership stays before connect.
7. Observed-diagnostic extract walks receipt trees via `\\?\` so Windows
   MAX_PATH no longer aborts score extraction after a successful evaluate.

**Historical MEASURED baseline** (evaluator-owned, 2026-07-16): TP=3 / FP=48 /
FN=128 under `C:\Users\Test\.qualibug-evaluator\observed-131-20260716\`.

**20260802T064251Z** (`RUN_b1ce534aea84017f0cd48c8c`,
`CMP_6fd189f7f750fc813aa83d06`) — pre-table-rebinding baseline:

| Field | Value |
|---|---|
| Output | `_funnel_runs/20260802_fact_to_experiment_observed_20260802T064251Z/` |
| Pipeline health | `DEGRADED` (execution completed; funnel conservation `PASS`) |
| Formal deliverables / canonical | 10 / 10 |
| Execution attestation | `VERIFIED` |
| Formal evaluate | `NOT_MEASURED` / `obligation_campaign_degraded` |
| Degrade cause | 113 `HARNESS_FAILED` — mostly `CLEANUP_RECEIPT_FAILED` with `identity_empty` |

**20260802T092912Z** (`RUN_6346aba5507846c106017a11`) — after step-scoped
identity; failure mode shifted to `CLEANUP_DB_DELETE_FAILED:UndefinedTable`
on logical `payment`/`refund` (~101). Attestation `VERIFIED`; still
`NOT_MEASURED` / `obligation_campaign_degraded`.

**20260802T095749Z** (`RUN_d8c36b8dd743fe8edad77ba6`,
`CMP_6fd189f7f750fc813aa83d06`) — after physical-table rebinding:

| Field | Value |
|---|---|
| Output | `_funnel_runs/20260802_fact_to_experiment_observed_20260802T095749Z/` |
| Extract | `.../evaluation_score_extract.json` |
| Pipeline health | `DEGRADED` (funnel conservation `PASS`) |
| Formal deliverables / canonical | 10 / 10 |
| Execution attestation | `VERIFIED` |
| Formal evaluate | `NOT_MEASURED` / `obligation_campaign_degraded` |
| Held-in TP/FP/FN | not scored (`measured_seeded_target_count=0`) |
| Commercial promotion | `false` (one-target diagnostic only) |
| `UndefinedTable` / `identity_empty` | **0 / 0** (closed) |
| HF count | 192 terminals → campaign `degraded` |
| HF mix (new dominant) | 98 `BLOCKED_EXECUTION` mis-sealed as HF (`compiled_obligation_has_no_execution_receipt`); ~42 `CONTRACT_ORACLE_HARNESS_FAILED` still cite `CLEANUP_RECEIPT_FAILED` (now mostly field-restore `CLEANUP_MUTATION_NOT_ATTESTED` on inventory/order, not UndefinedTable); 17 `HARNESS_CLEANUP_TRANSPORT_FAILED`; 8 `CLEANUP_WRITE_COVERAGE_MISMATCH` |

Attestation is not the blocker. `UndefinedTable` cleanup DELETE is closed.
Formal MEASURED remains blocked while any `HARNESS_FAILED` terminal remains —
next root causes are (1) `BLOCKED_EXECUTION` sealed as HF without an
execution receipt, and (2) field-restore mutation attestation /
remaining cleanup receipt failures — not oracle bar lowering.
One-target diagnostic is still not commercial promotion evidence.

**Root fix for (1) — accounting terminal sealing (landed before next observed rerun)**

- Cause: `_ensure_accounting_terminal_receipts` ran *before*
  `_manual_terminal_receipts` and labelled SELECTED+COMPILED obligations with
  no execution receipt as `HARNESS_FAILED` + `BLOCKED_EXECUTION`
  (`compiled_obligation_has_no_execution_receipt`). Budget-deferred rows that
  should have been `DEFERRED` / `OBLIGATION_BUDGET_REACHED` never reached the
  manual sealer because `execution_results` was already filled. Any
  `HARNESS_FAILED` keeps `derive_campaign_terminal_status` at `degraded` and
  blocks MEASURED (`obligation_campaign_degraded`).
- Fix: seal manual/pending terminals first; keep COMPILED compile receipts and
  defer at execution for pending budget rows; remaining selected+compiled gaps
  seal as `BLOCKED` + `BLOCKED_EXECUTION` (status aligned with reason), never
  false HF. Tests in `tests/test_discovery_funnel_closure.py`.

**20260802T103156Z** (`RUN_2c3dc9cfe4b1e71ed6386166`) — after accounting-terminal
sealing fix:

| Field | Value |
|---|---|
| Output | `_funnel_runs/20260802_fact_to_experiment_observed_20260802T103156Z/` |
| Extract | `.../evaluation_score_extract.json` |
| Pipeline health | `DEGRADED` (funnel conservation `PASS`) |
| Formal deliverables / canonical | 10 / 10 |
| Execution attestation | `VERIFIED` |
| Formal evaluate | `NOT_MEASURED` / `obligation_campaign_degraded` |
| Held-in TP/FP/FN | not scored (`measured_seeded_target_count=0`) |
| HF count | **91** (was 192) |
| Old filler HF (`compiled_obligation_has_no_execution_receipt`) | **0** (closed) |
| Budget deferred correctly | 150 `OBLIGATION_BUDGET_REACHED` as `DEFERRED` |
| Remaining HF mix | 37 `CLEANUP_RECEIPT_FAILED`; 27 other oracle receipt HF; 18 `HARNESS_CLEANUP_TRANSPORT_FAILED`; 9 `CLEANUP_WRITE_COVERAGE_MISMATCH` |
| Cleanup evidence tokens | `CLEANUP_MUTATION_NOT_ATTESTED`=23; `UndefinedTable`/`identity_empty`=0 |

Next MEASURED blocker is cleanup receipt / field-restore mutation attestation
and cleanup transport failures — not oracle bar lowering.
One-target diagnostic is still not commercial promotion evidence.

**Root fix for cleanup field-restore attestation (landed after T103156Z)**

- T103156Z evidence: 13× `mutation_attestation_missing`, 10×
  `attestation_restore_value_mismatch` under `CLEANUP_MUTATION_NOT_ATTESTED`.
- Cause A: empty cleanup identity still built an unscoped restore map, then
  attestation refused → false `CLEANUP_MUTATION_NOT_ATTESTED` (blocking DELETE).
- Cause B: restore diffs and attestation snapshots could come from different
  control/treatment arms of the same identity →
  `attestation_restore_value_mismatch`.
- Fix: `_mutation_restore_plan_from_steps` binds restore map + attestation to
  one governed step; empty identity yields no restore map. Tests in
  `tests/test_adapter_cleanup_reaches_the_executor.py`.

**20260802T105854Z** (`RUN_5acf4095816575ebbcf8f91d`) — after field-restore
attestation binding fix:

| Field | Value |
|---|---|
| Output | `_funnel_runs/20260802_fact_to_experiment_observed_20260802T105854Z/` |
| Extract | `.../evaluation_score_extract.json` |
| Pipeline health | `DEGRADED` (funnel conservation `PASS`) |
| Formal deliverables / canonical | 10 / 10 |
| Execution attestation | `VERIFIED` |
| Formal evaluate | `NOT_MEASURED` / `obligation_campaign_degraded` |
| HF count | **88** |
| `CLEANUP_MUTATION_NOT_ATTESTED` / restore-value mismatch | **0 / 0** (closed) |
| Remaining HF mix | 35 `CLEANUP_RECEIPT_FAILED`; 27 other oracle receipt HF; 17 `HARNESS_CLEANUP_TRANSPORT_FAILED`; 9 `CLEANUP_WRITE_COVERAGE_MISMATCH` |

Next MEASURED blocker is remaining cleanup transport / coverage / oracle
receipt failures — not mutation attestation. One-target diagnostic is still
not commercial promotion evidence.

**Root fix for CLEANUP_WRITE_COVERAGE_MISMATCH double-count (landed after T105854Z)**

- T105854Z evidence: all 9 coverage HF had `covered_sum > accepted` because
  control+treatment `NOT_REQUIRED` / `ACCEPTED_WRITE_STATE_UNCHANGED` each
  stamped full `accepted_write_count` (e.g. 2+2[+fixture 1] vs accepted 2/3),
  and adapter COMPLETED receipts could stamp the whole write set per subject.
- Fix: attribute unchanged writes once across cleanup subjects; adapter
  cleanup evidence uses `_scoped_accepted_write_count_for_cleanup` (source
  step / compensates op). Tests in
  `tests/test_customer_delivery_gate_v2_cleanup.py` and
  `tests/test_adapter_cleanup_runtime_identity.py`.

**20260802T113119Z** (`RUN_43b81361ebd1d68c7d7d875c`,
`CMP_bb5a5a4be6fcac4cb5549389`) — coverage-fix validation rerun:

| Field | Value |
|---|---|
| Output | `_funnel_runs/20260802_fact_to_experiment_observed_20260802T113119Z/` |
| Extract | `.../evaluation_score_extract.json` |
| Pipeline health | `DEGRADED` (funnel conservation `PASS`) |
| Formal evaluate | `NOT_MEASURED` / `obligation_campaign_degraded` |
| HF count | **37** (was 88) |
| `CLEANUP_WRITE_COVERAGE_MISMATCH` | **0** (closed; coverage fix validated) |
| `CLEANUP_ROW_IDENTITY_NOT_RESOLVABLE` | **1** (was 13) |
| Remaining HF mix | 26 `HARNESS_CLEANUP_TRANSPORT_FAILED`; 11 `CONTRACT_ORACLE_HARNESS_FAILED` (`CLEANUP_RECEIPT_FAILED:cleanup:treatment_1`) |

Coverage fix worked. Remaining blockers diagnosed:

1. **26× transport HF** — all same op, `accepted_non_cleanup_write_count=0`,
   `cleanup_outcome.attempted_count=0`. Empty `governed_write_attempts` fell
   through to `REJECTED_WRITE_STATE_NOT_PROVEN_UNCHANGED` because
   `_rejected_writes_left_state_unchanged([])` is False, falsely incrementing
   `cleanup_failures` → `HARNESS_CLEANUP_TRANSPORT_FAILED`.
2. **11× oracle HF** — treatment cleanup HTTP 200 but sealed FAILED with
   `cleanup_required_write_count=0`. Aggregation used receipt-only projected
   steps that lose `effectful_write_receipt` binding, so identity-bound DELETE
   need was invisible and `restoration_verified` stayed false.

**Root fix for false cleanup HF (landed after T113119Z)**

- No governed write attempts → `NOT_REQUIRED` /
  `NO_WRITE_REACHED_TRANSPORT` (no `cleanup_failures`).
- Source-scoped arm with no cleanup-needed write → `NOT_REQUIRED` (sibling
  arms must not fail the experiment).
- Aggregation / loop entry use real write steps for identity-bound DELETE
  need detection; successful restoration can seal `COMPLETED`.
- Tests in `tests/test_adapter_cleanup_runtime_identity.py` (+ stub kwargs
  fix in `tests/test_adapter_cleanup_reaches_the_executor.py`).

Next MEASURED blocker is validating this cleanup HF reduction on a fresh
observed diagnostic. One-target diagnostic is still not commercial promotion
evidence.

**20260802T120110Z** (`RUN_f28cc1d78fbd7f3493b374e5`) — post false-cleanup-HF
fix validation:

| Field | Value |
|---|---|
| Output | `_funnel_runs/20260802_fact_to_experiment_observed_20260802T120110Z/` |
| Extract | `.../evaluation_score_extract.json` |
| Formal evaluate | `NOT_MEASURED` / `obligation_campaign_degraded` |
| HF count | **65** (was 37 on T113119Z; not yet MEASURED) |
| `CLEANUP_WRITE_COVERAGE_MISMATCH` | **0** (still closed) |
| HF mix | 45 `CONTRACT_ORACLE_HARNESS_FAILED`; 16 `HARNESS_CLEANUP_TRANSPORT_FAILED`; 4 `CLEANUP_EVIDENCE_INCOMPLETE` |
| COHF missing_requirements | 16 `CLEANUP_RECEIPT_FAILED:cleanup:treatment_1` (`CLEANUP_ROW_IDENTITY_NOT_RESOLVABLE`); 24 `OBSERVER_RECEIPT_FAILED:http_response` + `TREATMENT_RECEIPT_FAILED` (status_code=0); 8 `CONTROL_RECEIPT_FAILED`; 4 fixture cleanup |
| Evidence tokens | `CLEANUP_ROW_IDENTITY_NOT_RESOLVABLE`=16 (was 1) |

**Diagnosis (prior fix interaction, not bar lowering)**

1. Empty-write → `NOT_REQUIRED` / `NO_WRITE_REACHED_TRANSPORT` helped transport
   HF (26→16). Coverage mismatch stayed closed. Not a regression of the
   coverage double-count fix.
2. **16× treatment `CLEANUP_RECEIPT_FAILED`** — true root cause: db_sql skip
   required `source_step_id and scoped_accepted_write_count==0`. When a
   treatment cleanup entry had `compensates_operation_ref` matching no
   accepted write and **no** `source_step_id`, scoped count was 0 but the
   adapter still ran with an empty identity →
   `CLEANUP_ROW_IDENTITY_NOT_RESOLVABLE` (reproduced; control
   `field_restore` COMPLETED on the same experiment). The prior
   source_step-only guard therefore **under-skipped**, newly visible as
   more experiments reached oracle activation after transport HF dropped.
3. **status_code=0 control/treatment/observer HF** — separate transport
   non-response cluster on one fixture user path
   (`/api/auth/admin/users/<id>/status` etc.); not caused by the scoped
   cleanup guard. Remains a residual MEASURED risk after the identity
   cleanup fix.

**Root fix (landed after T120110Z)**

- db_sql cleanup: if scoped accepted-write count is 0 → `NOT_REQUIRED` /
  `NO_ACCEPTED_WRITE` whether or not `source_step_id` is stamped. Never
  call the adapter with an empty scoped write set.
- Regression:
  `test_db_sql_cleanup_scoped_zero_without_source_step_is_not_required`
  in `tests/test_adapter_cleanup_runtime_identity.py`.

Next MEASURED blocker is a fresh observed diagnostic validating the 16
identity HF close, then the status_code=0 transport cluster if it remains.
One-target diagnostic is still not commercial promotion evidence.

**20260802T123504Z** (`RUN_7ed8af964860bcbaa11ac8ee`) — db_sql scoped-zero
skip validation:

| Field | Value |
|---|---|
| Output | `_funnel_runs/20260802_fact_to_experiment_observed_20260802T123504Z/` |
| Extract | `.../evaluation_score_extract.json` |
| Formal evaluate | `NOT_MEASURED` / `obligation_campaign_degraded` |
| HF count | **26** (was 65 on T120110Z) |
| `CLEANUP_RECEIPT_FAILED` / `CLEANUP_ROW_IDENTITY_NOT_RESOLVABLE` | **0 / 0** (db_sql scoped-zero fix validated) |
| Formal deliverables | 5 |
| HF mix | 16 `HARNESS_CLEANUP_TRANSPORT_FAILED`; 7 `CONTRACT_ORACLE_HARNESS_FAILED`; 3 `CLEANUP_EVIDENCE_INCOMPLETE` |

**Diagnosis of residual 16 transport HF (same cluster as T120110Z)**

1. All 16 share one op (`bir_cf029884ce1a1add`),
   `write_request_attempt_count=0`, `accepted_write_count=0`,
   `cleanup_outcome={FAILED, attempted=0, failure=1}`.
2. Root cause: sandbox identity/mutation blocks still emit a
   `governance_receipt` after the before-GET (`before` observed, `after={}`,
   `write_request_attempt_count=0`). The empty-attempts NOT_REQUIRED path did
   not apply, and `_rejected_writes_left_state_unchanged` is False because
   before≠after({}) → false `REJECTED_WRITE_STATE_NOT_PROVEN_UNCHANGED` →
   `HARNESS_CLEANUP_TRANSPORT_FAILED`.
3. **COHF 7** — separate cluster: 6× status_code=0 /
   `CONTROL_RECEIPT_FAILED`+`TREATMENT_RECEIPT_FAILED` on
   `bir_9c52e8f65cb8c3d7`; 1× `CONTRACT_EVIDENCE_IDENTITY_DUPLICATE` on
   fixture cleanup. Not caused by the cleanup transport seal.
4. **CEI 3** — separate: accepted writes with cleanup COMPLETED but delivery
   cleanup evidence incomplete on `bir_f0a5e2bd75d3f240`.

**Root fix (landed after T123504Z)**

- `_governed_write_reached_transport`: true only when
  `write_request_attempt_count > 0`.
- Cleanup executor: zero-transport governed receipts (including before-GET +
  blocked write) seal `NOT_REQUIRED` / `NO_WRITE_REACHED_TRANSPORT` — same as
  empty attempts. Transport-reached rejected writes that cannot prove
  unchanged still fail closed.
- Tests:
  `test_zero_transport_governance_receipt_is_not_cleanup_transport_failure`,
  `test_rejected_transport_write_unproven_state_still_fails_cleanup`
  in `tests/test_adapter_cleanup_runtime_identity.py`.

Next MEASURED blocker is a fresh observed diagnostic validating the 16
transport HF close; residual COHF/CEI remain separate follow-ups.
One-target diagnostic is still not commercial promotion evidence.

**20260802T131808Z** — aborted before transport validation (~23s,
`exit_code=1`). Root cause: `BATCH_LOGICAL_KEY_COLLISION` /
`markdown_api:api_spec` during `_sync_declared_project_sources` after
divergent dual-root `API_SPEC.md` copies (projects input gained
`DELETE /api/users/addresses/:id`; platform_inputs lagged). No
`evaluation_score_extract`. Fixed by fail-closed
`DECLARED_SOURCE_LOGICAL_KEY_CONFLICT` preflight + aligning the two
workspace copies. Rerun started:
`_funnel_runs/20260802_fact_to_experiment_observed_20260802T132333Z`
(past ingest; transport HF validation pending completion).

**20260802T132333Z** (`RUN_508ae6527c342768dafd0262`,
`CMP_80a823975cec54a928f66d6b`) — post zero-transport cleanup fix:

| Field | Value |
|---|---|
| Output | `_funnel_runs/20260802_fact_to_experiment_observed_20260802T132333Z/` |
| Extract | `.../evaluation_score_extract.json` |
| Formal evaluate | `NOT_MEASURED` / `obligation_campaign_degraded` |
| HF count | **26** — all `HARNESS_CONNECTION_FAILED` (`reason_family=TARGET_SYSTEM_RESPONSE`) |
| `HARNESS_CLEANUP_TRANSPORT_FAILED` / COHF / CEI | **0 / 0 / 0** (cleanup fixes held) |
| Formal deliverables | 5 |
| Op cluster | all 26 on `bir_cf029884ce1a1add` (same op as prior false cleanup-transport HF) |
| Operational | `write_request_attempt_count=0`, cleanup `NOT_REQUIRED`; `http_request_attempt_count` 1 or 2 |
| Gateway | evaluator proxy observed matching `target_request_count` 1–2 per HF (requests reached gateway) |
| Timing | sample execution `elapsed_ms≈132` for 2 HTTP — too fast for GET retry backoff (1s+3s) |

**Diagnosis (misclassification, not pure target downtime)**

1. Prior cleanup transport false-HF on this op is closed (`NOT_REQUIRED`).
2. Governed writes blocked after a real before-GET leave write
   `status_code=0` and `write_request_attempt_count=0`. Finalize's
   `has_http` only checks step `status_code > 0`, then
   `_classify_harness_failure` **fell back to**
   `HARNESS_CONNECTION_FAILED` with no connection evidence.
3. Gateway attestation proves the before request was correlated; target
   `8080` remains reachable (HTTP responses, not connection refused).
4. Separate prior COHF `status_code=0` cluster on
   `bir_9c52e8f65cb8c3d7` (`POST .../users/<id>/status`) is a different
   op; not the residual blocker on T132333Z.

**Root fix (landed after T132333Z)**

- Plan-step executor: zero-transport governance block after
  `before.status > 0` appends the governance reason to
  `pre_transport_block_reasons` (not connection HF).
- Finalize: same detection as defense-in-depth → seal `BLOCKED` /
  `BLOCKED_MISSING_BINDING` (identity/mutation gap), never
  `HARNESS_CONNECTION_FAILED` without connection evidence.
- Classifier: only `HARNESS_CONNECTION_FAILED` when step errors contain
  connection text; empty fallback no longer claims connection.
- Tests: `tests/test_governed_zero_transport_not_connection_hf.py`,
  extended `TestHarnessFailureSubclassification` in
  `tests/test_write_reversibility_contract.py`.

Next MEASURED blocker is a fresh observed diagnostic validating HF→0
(or remaining true connection HF only). One-target diagnostic is still
not commercial promotion evidence.

**20260802T140342Z** (`RUN_a75f7e40519ee3606489b57c`,
`CMP_80a823975cec54a928f66d6b`) — connection-misclassification validation:

| Field | Value |
|---|---|
| Output | `_funnel_runs/20260802_fact_to_experiment_observed_20260802T140342Z/` |
| Extract | `.../evaluation_score_extract.json` |
| Formal evaluate | `NOT_MEASURED` / `obligation_campaign_degraded` |
| Attestation | `VERIFIED` |
| HF count | **14** (was 26 all-connection on T132333Z) |
| `HARNESS_CONNECTION_FAILED` | **0** (misclassification fix validated; those are now `BLOCKED_MISSING_BINDING`=23) |
| Formal deliverables | 3 |
| HF mix | 10 `CONTRACT_ORACLE_HARNESS_FAILED`; 4 `CLEANUP_EVIDENCE_INCOMPLETE` |

**HF breakdown (exact)**

1. **8× COHF** on `bir_9c52e8f65cb8c3d7` (`POST .../users/<id>/status`) —
   `CONTROL_RECEIPT_FAILED` + `TREATMENT_RECEIPT_FAILED` +
   `OBSERVER_RECEIPT_FAILED:http_response` (+ auth/business INDETERMINATE).
   Operational: `http_request_attempt_count=1`, `write_request_attempt_count=0`,
   cleanup `NOT_REQUIRED` / `NO_WRITE_REACHED_TRANSPORT`. Control/treatment
   evidence `status_code=0`, `response_observed=false`.
2. **4× CEI** on `bir_f0a5e2bd75d3f240` — contract cleanup `NOT_REQUIRED` /
   `ACCEPTED_WRITE_STATE_UNCHANGED`, but operational cleanup sealed
   `COMPLETED` with `attempted_count=1`, `completed_count=0`.
3. **1× COHF** `CONTRACT_EVIDENCE_IDENTITY_DUPLICATE:cleanup:fixture_cleanup:id`
   — bulk NOT_REQUIRED stamp + later real fixture COMPLETED for same subject.
4. **1× COHF** `CLEANUP_RECEIPT_FAILED:fixture_cleanup:id` +
   `CLEANUP_RESTORATION_NOT_PROVEN:cleanup:treatment_1` — DELETE 200 but
   restoration not verified (`EXECUTED_BUT_NOT_RESTORED`). Likely real
   residual; not reclassified here.

**Root fixes (landed after T140342Z)**

- Plan-step / barrier: governed `status_code=0` with
  `write_request_attempt_count=0` seals contract evidence `BLOCKED` (not
  `FAILED`); empty governance reason still enters pre-transport block list
  as `governed_write_not_attempted`.
- Step-scoped / aggregate `http_response`: zero-transport →
  `INDETERMINATE` / `HTTP_RESPONSE_NOT_ATTEMPTED` (not FAILED).
- Activation defense-in-depth: FAILED control/treatment/cleanup with
  explicit `write_reached_transport=false`, and http_response FAILED with
  the same mark, become blockers — never COHF.
- Operational cleanup: `COMPLETED` only when `cleanup_completed > 0`
  (closes false CEI from adapter-attempt-without-accept).
- Cleanup executor: bulk NOT_REQUIRED/BLOCKED loops skip `fixture_cleanup:*`;
  pending fixture cleanup replaces any prior stamp for that subject.
- Tests: `tests/test_zero_transport_not_contract_oracle_hf.py`.

Next MEASURED blocker is a fresh observed diagnostic validating HF→0
(or only the residual restoration HF if still present). One-target
diagnostic is still not commercial promotion evidence.

## Rollback

Disable by removing the `project_fact_experimentability` call from
`enterprise_understanding/integration/__init__.py` and the
`write_fact_tracking_report_files` call from `__main__.py`. Receipts are
additive; historical artifacts are not reinterpreted as new campaign results.

Phase-2 materialization can be bypassed by skipping
`materialize_and_recompile_abstract_pack` in `experiment_compiler.compile_experiments`
(capability gaps would again surface only as BLOCKED).
