
调用这个开源项目方式写代码 https://github.com/DietrichGebert/ponytail
## Product Health Checks

When evaluating product readiness or dogfooding bug-finding features, verify observable behavior from the running product and code before reporting status. Treat configured-but-unverified integrations as not online: for model providers, a saved key or endpoint only means "configured" until a real health check succeeds, and failures must be shown as failed/offline rather than healthy.

- The only supported backend launch authority is `ai_test_asset_center.private_pilot_entrypoint:run_server` (including `qualibug-server`). `private_pilot_service` is the core service implementation, not a direct launch path. Command-center helpers are split out of `private_pilot_service` and re-exported where needed: `private_pilot_regression_projection.py`, `private_pilot_defect_summaries.py`, `private_pilot_command_center_helpers.py`, `private_pilot_scan_prep.py` (includes ingest auto-scan), `private_pilot_continuous.py`, `private_pilot_command_center_envelope.py`, `private_pilot_command_center_builder.py` (`CommandCenterBuilderMixin`), `private_pilot_credentials_handlers.py` (`CredentialsHandlerMixin`), plus support modules `private_pilot_json_io.py`, `private_pilot_debug_client.py`, `private_pilot_project_assets.py`, `private_pilot_tenant_auth.py`, `private_pilot_campaign_projection.py`, and `private_pilot_scan_aggregates.py`. Envelope enrichments register through `register_envelope_post_hook` instead of replacing `_normalize_command_center_envelope`; the service keeps a thin dispatcher for call sites. Customer delivery classification in that core imports `customer_delivery_gate.split_customer_delivery_tracks` directly; runtime patch installation must never be required for correctness. `run_v12_pipeline` binds System Behavior Space project/root context first-class via `system_behavior_space_context`; coverage learning reorder registers through `v12_legacy_schedule.register_slice_reorder_hook`. System Behavior Space enrichment registers through first-class hooks on `business_state_graph`, `semantic_scenario_generator`, `oracle_engine`, `v12_legacy_oracle_findings`, and `regression_runner` — not by replacing those modules' methods. Neither path may replace `v12_pipeline.run_v12_pipeline`.
- Evidence enrichment may format captured facts and generate clearly marked operator guidance, but it must never infer request bodies, credentials, actors, business rules, entity/table names, SQL, or impact claims. Generated guidance is synthetic and cannot satisfy the customer-delivery gate.

## Syntax Check After Every Edit

**After ANY file edit (patch, write, terminal sed), always verify syntax before concluding the change succeeded.** A missing parenthesis, bracket, or quote is invisible in the diff but makes the entire module unimportable. Silent import failures cause background processes, cron jobs, and tests to die with zero output — and their exit code still reports "ok" to the scheduler.

Run immediately after editing any Python file:
```
python -c "import ast; ast.parse(open('path/to/file.py').read()); print('OK')"
```

Never skip this step. A "syntax OK" check takes 0.1 seconds and prevents hours of silent failures.

## Critical Configuration Guardrails

These values MUST NOT be removed or lowered below their floor. Removing them causes silent failures that look like "process died" but are actually timeouts.

| File | Line | Value | Reason |
|---|---|---|---|
| `discovery_engine.py` | `__init__` | `timeout_seconds ≥ 300` | Reader prompt needs 150-200s on DeepSeek. Default 120s → silent timeout → loop appears "crashed". |
| `discovery_engine.py` | `__init__` | `max_tokens ≥ 32768` | Causality engine produces >41K chars JSON. Truncation at lower values causes engine failures. |
| `stage_reason_all_v2.py` | `MAX_HYPOTHESES` | `15` | Per-engine hypothesis cap. Higher values increase API cost disproportionately. |
| `stage_reason_all_v2.py` | `max_workers` | `4` | Default parallel engine workers. Higher → API rate limits. |

When refactoring configuration (e.g. Policy Registry migration), always verify these floors are preserved with:
```python
assert engine.client.config.timeout_seconds >= 300, "timeout too low"
assert engine.client.config.max_tokens >= 32768, "max_tokens too low"
```


1. Fail Fast / Errors Never Pass Silently：不要在代码里藏兜底逻辑来吞掉错误、隐藏问题。出了问题就应该让它爆出来，否则你永远找不到真实问题。
2. Fix the Cause, Not the Symptom / Don't Paper Over Bugs：当一个问题出现时，不要用各种 small fix、针对性补丁来掩盖它。必须定位真实根因，彻底修复。在 bug 上糊纸只会让系统积累你不知道的危险暗病。
3. Make It Observable：即使问题很难定位，也绝不要偷懒做表面修复。应该给项目增加充分的日志和可观测性，保证下次问题再现时你有足够信息去定位。问题无法修复时，只需要诚实告诉我信息不足、需新增日志，不要假装修好了。
4. Design for Debugging / Traceability：始终注意在关键路径上给自己留足排查日志，确保每一个关键节点都是可追溯的。
5. Living Documentation / Single Source of Truth：当项目关键技术栈或产品方向发生变更时，同步更新 agents.md。文档必须随代码一起演进，不能让它变成过时的谎言。
6.所有产品前后端都不能有硬编码，要保持通用性，我做的是全行业适配的，绝对不能有硬编码
7.测试项目测试bug不能造假数据给我，没有执行找出的bug不要给我，不能给我假数据
8.首先我要的就是全行业不同软件系统都适用，只要违反这个原则都要优化
9.我的产品前端服务端口是5174，后端服务端口是8088，不要搞错了

## Non-Production Execution Contract

- QualiBug automatically performs read and write probes against explicitly declared non-production targets (local, development, test, QA, SIT, UAT, staging, pre-release, and sandbox). A source-bound campaign does not require per-probe manual approval.
- Production targets are a hard write boundary. Unknown or undeclared environment types are fail-closed for writes; never infer that an unknown target is safe.
- Every write must pass through the governed sandbox executor and emit before/after observations, cleanup outcome, campaign/slice identity, target identity, and an audit receipt. Hidden seed/bootstrap writes outside this path are forbidden.
- Multi-write scenarios must expose a per-write governance hook and emit one audit receipt per actual HTTP write. Never retry an entire scenario after any write may have been accepted; compensate partial setup in reverse order and surface the original failure.
- A POST write may reach transport only when source documentation declares a concrete identity-bound DELETE or compensating action for the created resource. PUT/PATCH cleanup must project the exact entity through a documented route binding and restore only source-observed mutated fields; ambiguous collection snapshots fail closed.
- Read-only mode remains available as an operator kill-switch, but it must block writes before any request is sent and report the blocking reason visibly.

## Discovery Harness Evolution Contract

- Near-term capability stabilization uses the user-supplied Windows-native
  benchmark whose manifest identity is
  `v0.6-windows-native-stable-131bugs` and whose frozen hidden GT contains 131
  defects. Cross-industry execution is deferred until this single-target
  foundation is stable; commercial cross-industry gates remain `NOT_MEASURED`
  and are not removed.
- The 131-Bug focus does not permit benchmark hardcoding. Discovery runtime may
  consume only visible enterprise materials, configured endpoints, declared
  test actors, secret references, and runtime observations. Benchmark source,
  hidden GT, scoring rules, match keywords, reproduction answers, and evaluator
  miss labels remain evaluator-private and must never enter prompts, runtime
  context, traces, detectors, fixtures, Oracles, or product-facing outputs.
- The requested package directory name contains `v0_5`, but target identity is
  determined by `BENCHMARK_MANIFEST.json`, not by its folder name. For the
  selected Windows-native mode, the benchmark target uses customer UI 3001,
  admin UI 3002, API gateway 8080, and PostgreSQL 5432 as evaluator-profile
  data. QualiBug remains frontend 5174 and backend 8088.

- Discovery mainline authority is selected before campaign creation and frozen
  in `qualibug.discovery-mainline-run.v1`. The public
  `v12_pipeline.run_v12_pipeline` function is a compatibility wrapper that
  invokes `discovery_mainline.run_discovery_mainline` exactly once. It must
  never retry with, fall back to, or switch to the other authority after an
  exception.   Legacy schedule, scenario HTTP execution, oracle-finding, and other
  compatibility helpers live in `v12_legacy_schedule.py`,
  `v12_legacy_scenario_exec.py`, `v12_legacy_oracle_findings.py`, and
  `v12_compat_helpers.py` and are re-exported from `v12_pipeline` — they are
  not on the `experiment_candidate` delivery path. Finding enrichment registers
  through `register_finding_enricher`, not by replacing `run_v12_pipeline`
  symbols. BSG/SSG/Oracle/regression System Behavior Space installers register
  the same first-class hook pattern (`register_bsg_*_hook`,
  `register_scenario_enricher`, `register_oracle_*_hook`,
  `register_*_hook` on `regression_runner`). Structured regression oracles
  register through `register_probe_oracle_enricher` and
  `register_structured_oracle_judge_hook`.
  Runtime/fuzzer/slice/DB helpers SSOTs are `pipeline_runtime.py`,
  `pipeline_fuzzer.py`, `pipeline_slices.py`, and `pipeline_db.py` (explicit
  imports; `import *` must not be used for `_`-prefixed symbols). Industry
  coupon DB sampling was removed from the product path.
- `experiment_candidate` planning and execution live in
  `discovery_runtime.py`; selected experiments execute only through
  `experiment_executor.execute_selected_experiments`. Contract oracle evaluation
  on that path is the customer-delivery authority; the legacy `OracleEngine`
  registry is diagnostic only and must not auto-attach industry oracles from
  path, entity, or domain heuristics. The legacy domain may run only when
  `legacy_champion` was explicitly selected before the run and a gate-verifiable
  legacy runner is installed. The product `run_v12_pipeline` wrapper currently
  installs only `experiment_candidate`; selecting unavailable
  `legacy_champion` fails closed with `mainline_runner_unavailable` before
  campaign creation. The execution-policy default remains `legacy_champion`
  until external paired evidence promotes the experiment candidate — operators
  must explicitly select `experiment_candidate` for operational discovery runs
  until that promotion evidence exists or a legacy runner is restored.
- `qualibug.obligation-attempt-ledger.v1` is the completion and funnel SSOT.
  Every selected, blocked, or deferred obligation must have exactly one
  terminal attempt with a reason code. Zero selected obligations and all-
  blocked runs remain visibly `BLOCKED`; empty findings from them must never be
  interpreted as a defect-free target.
- Trace and weakness diagnostics consume
  `qualibug.discovery-trace-ledger.v3`, keyed by obligation attempt identity.
  V1 input requires the explicit offline migration; silent schema fallback is
  prohibited. Replay and shadow runs set `customer_outputs_published=false`.
- A path placeholder may compile only when its binding plan names an exact,
  source-declared concrete `GET`/`HEAD` operation from Behavior IR. Runtime
  materializes that value with the control actor before control/treatment,
  emits a fingerprint-only binding receipt, and reuses the same resource value
  for both paths. Missing, invalid, or unsuccessful resolvers remain visibly
  `BLOCKED_MISSING_BINDING`; invented identifiers and hidden seed reads are
  prohibited.
- Runtime rollback is a next-run policy decision only. Select
  `legacy_champion` before creating a new immutable run contract; never roll
  back inside an active campaign or after either runner has started.
- A campaign may reopen a terminal attempt only when its prior immutable ledger
  proves that every terminal was `BLOCKED`/`DEFERRED`, zero executed
  target-request receipts exist, and no behavior slice was attempted. The retry
  must emit an audit event; any observed target request permanently forbids
  whole-run retry.
- Phase-1 cycle-time claims require immutable
  `qualibug.discovery-phase1-timing.v1` receipts from
  `tools/discovery_phase1_timing.py`: five warm runs for baseline and candidate,
  matching command/input/environment/runtime/system identities, a clean code
  commit, and at least 60% p50 improvement. Timing evidence never substitutes
  for external quality evidence.

- The Bug discovery north star is externally measured hidden-ground-truth quality, not internal candidate, confirmed, validated, or funnel counts. Internal counts may diagnose conversion loss but MUST NOT be presented as recall, precision, or commercial capability.
- Harness evolution uses a fixed, versioned evaluator-private manifest. Discovery runtime receives only the runtime view; ground-truth paths and contents must never enter prompts, runtime context, traces, policy proposals, or product-facing outputs.
- Commercial promotion requires paired champion/challenger replay and shadow execution on identical input, fixture, context, environment, held-in, held-out, and intentionally clean targets. Estimated impact is not promotion evidence.
- Only findings that pass the formal customer-delivery gate may be scored as true or false positives. Candidates and internal clues are excluded from the commercial quality score.
- A candidate may be promoted only when no measured split regresses and at least one measured split improves. Missing pipeline health, missing target receipts, missing operational metrics, safety incidents, production requests, cleanup failures, dirty environments, or P0/P1 false positives on a clean target must block promotion visibly.
- Evaluation datasets must remain industry-neutral and data-driven. A commercial generalization claim requires at least three held-out industries; no detector, prompt, UI, or service may encode benchmark answers or customer-specific business rules.
- The authoritative implementation and Goal acceptance gates are documented in `docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md`. The evaluator contract is implemented in `ai_test_asset_center/discovery_evaluation_contract.py` and the external CLI in `tools/discovery_evaluation.py`.
- SPC Phase-1 miss diagnosis (why each known bug was not found) is evaluator-private only: `benchmark_evaluator/miss_diagnosis.py` + `tools/miss_diagnosis.py`. It may load hidden ground truth for post-run diagnosis and must never feed GT into discovery prompts, runtime context, or product outputs. Success metric remains true-positive count on the fixed 131-bug benchmark, not internal funnel counts.
- Capability breakthrough architecture and Phase 0–5 migration are defined in `docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md`. Runtime facts flow through Behavior IR (`behavior_ir.py`) → Test Obligations → Executable Experiments on `run_v12_pipeline` via `experiment_executor.py` (selected experiment → fixture DAG → governed requests → observers → assertion DSL → contract oracle → delivery gate). Missing environment_type must not default to `test`. Unresolved actor/fixture/observer/cleanup compensation must be `BLOCKED`, never `COMPILED`. Global reset may set `environment_restored` but must preserve original `cleanup_failures`. Artifact persistence must use `artifact_redactor.py`; product quality claims must use `discovery_quality_projection.py` (including `obligation_execution_projection` for Spec §10.3 UI) and remain `NOT_MEASURED` until external evaluator receipts exist. Champion/candidate comparisons use the frozen **131-bug** evaluator-private GT under `_private_eval/_evaluator_private/benchmark_mall_131/` — never swap to the 71-bug copy for percentage comparisons. Audit packs require a clean committed worktree.
- Absolute Gate D / controlled-pilot / GA thresholds are machine-checked by `assess_discovery_goal_status` (`python tools/discovery_evaluation.py goal-status`). Missing private ground truth, incomplete receipts, or a missing unit-cost baseline must remain `NOT_MEASURED` and must not unlock a higher commercial claim.
- Gate metrics and promotion thresholds have one SSOT: `docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md`. Architecture lives in `docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md`; DOCX files are release exports and must not become independently editable specifications.
- Target authorization has one SSOT: `ai_test_asset_center/target_policy.py`. Environment identity and environment type are separate required facts; localhost or an environment name must never imply write safety. Project preflight, V12 runtime, API/DB/UI adapters, and sandbox writes must consume the same `TargetPolicyDecision`.
- Product defect truth has one SSOT: `ai_test_asset_center/discovery_quality_projection.py`. Current-run `deliverable|candidate|rejected`, current campaign, and historical shelf are separate scopes; legacy readiness counters are diagnostic only and cannot replace `formal_customer_deliverable_count`.
- Idempotency and concurrency obligations must come from an explicit source invariant joined to an exact Behavior IR operation. A write method alone is not evidence that an idempotency or concurrency contract exists; blanket write-effect obligations are forbidden.
- Project campaign contracts live in `ai_test_asset_center/campaign_api_contract.py` and are exposed only under `/api/v1`. Evaluation submissions must be Ground-Truth-free, pass `artifact_redactor.py`, and stay `NOT_MEASURED` until an external evaluator receipt is verified.
- Python-module retirement uses the non-destructive strangler inventory in `ai_test_asset_center/architecture_inventory.py`, with roots and responsibility overrides in `ai_test_asset_center/architecture_roots.json`. Architecture counts are diagnostic only and must never become discovery-quality claims. Per-root runtime evidence is collected by `tools/collect_architecture_import_trace.py`, which marks a root complete only after a real successful Python process observes the declared module and, when declared, executes the exact callable. Runtime trace roots cover declared product/evaluation/tooling authorities, project scripts, and active discovery entrypoints; test modules remain static reachability roots and are verified by the separate passing-test gate rather than hundreds of synthetic import-only sessions. A runtime import trace is trusted only when its content is authenticated with an evaluator-owned HMAC key stored outside the product workspace; unsigned or invalid traces fail closed. Even a complete authenticated trace can advance a candidate only to manual deletion review. No module may be deleted automatically: static unreachability, a complete supported-entrypoint runtime import trace, resolved dynamic-import uncertainty, passing tests, and manual deletion review are all required. The operating procedure lives in `docs/DISCOVERY_MODULE_STRANGLER.md`.
- Evaluator receipts/reports/comparisons are authenticated evaluator-owned artifacts. Policy comparisons must reload and revalidate all four replay/shadow reports, bind policy id/version/strategy/mainline fingerprints, and recompute GT-free evidence and the promotion decision. Historical signatures are verified through the configured receipt keyring; an invalid or missing signature must fail closed.
- A runtime receipt cannot attest its own network activity. Observed policy evaluation requires `qualibug.evaluator-execution-attestation.v1`, signed from evaluator-owned observations supplied by the trusted observation provider outside the product workspace. Missing trusted observations, subprocess isolation, or exact request coverage keeps the evaluation `NOT_MEASURED`.
- Agent semantic linking may propose intent only between exact source-backed rule and interface identities. Unknown, duplicate, low-confidence, or over-budget proposals must be rejected visibly in `qualibug.agent-semantic-link-receipt.v1`; they must never enter Behavior IR or abort otherwise valid source-grounded planning. Malformed provider responses and provider failures remain fail-fast.
- Explicit UI execution requests are part of the `run_v12_pipeline` compatibility path and execute only through the governed UI adapter. Playwright locator intent must resolve to one visible DOM/accessibility candidate and emit `qualibug.multimodal-locator.v1` with both DOM and element-image fingerprints; ambiguous or missing candidates remain blocked.
- Evaluator fixture campaign identity and product mainline campaign identity are separate authorities. The evaluator-owned loopback gateway binds the product identity from complete correlation headers, strips those headers before forwarding, and seals exact per-attempt request/write counts outside the product workspace. A one-target authenticated diagnostic is evidence for that target only and can never bypass commercial-shape promotion gates.
- Product identity consistency may contain only product-owned scopes. The external evaluator must first validate every submitted scope against the independently rebuilt canonical and occurrence identities, then bind `formal_authority_occurrence_ids`, `evaluator_submission_occurrence_ids`, and `evaluator_submission_ids` itself. Missing evaluator-owned scopes in product output are not a product failure; conflicting submitted scopes fail closed.
- Canonical product defect identity has one registry authority: `canonical_defect_registry.py`. `canonical_defect_count` is customer-visible unique truth; `delivery_occurrence_count` is audit evidence only. Titles, severity, confidence, historical rows, and delivery occurrences must never create a parallel customer-visible defect identity or readiness count.
- Importing `ai_test_asset_center` must be side-effect free. Runtime scenario contracts are validated and explicitly compiled into the V12 path; product `scan()` rejects evaluator-private seed/observation fields. Evaluator scoring must never be installed through product package import or product runtime patches.
- Behavior IR operation identity is canonicalized by service + method + normalized path template. Duplicate source operation aliases are retained as source references and emitted as explicit conflict receipts; they must not silently overwrite one another. Markdown JSON/YAML/curl request examples must remain structured request examples, and an unbound state transition must emit a visible coverage gap rather than infer an operation.
- Runtime interface discovery is a governed read-only planning round (`planning_round=0`). Candidate paths may be derived only from documented route vocabulary plus the deployment-owned semantic action policy. Anonymous `401/403` is not proof of route existence: discovery requires a correlated active test-actor confirmation, stops after the first conclusive response, excludes disabled/locked actors, and emits no finding by itself.
- A proven runtime interface may expand Behavior IR only through `qualibug.behavior-ir-expansion-round.v1`. The receipt binds input/output Behavior IR identities and exact observation fingerprints; only obligation identities absent from the immutable first round may enter `planning_round=2`, and those experiments execute only through `execute_selected_experiments`. Indeterminate or absent observations must terminate the round as `STAGNATED`, never invent an operation or rerun prior obligations.
- Runtime state snapshot observers (`before_state`, `after_state`, `final_state`) are implemented typed observers. They compile only when a source-declared effect read exists, emit fingerprinted state receipts from governed write before/after snapshots, exclude cleanup phases from experiment final state, and feed assertion DSL evidence through `experiment_executor.py`.
- Runtime `barrier_timeline` is an implemented typed observer, but it must remain fail-closed: only explicit barrier/timeline events with a release marker and at least two participants may produce an OBSERVED receipt. Sequential HTTP steps alone must remain INDETERMINATE and must never be treated as concurrency evidence.
- Runtime `typed_assertion` and `source_invariant` are implemented typed observers for contract lineage only. They prove that a bounded assertion kind and source-grounded invariant entered the runtime chain; they must not evaluate the business result or create a customer-visible finding by themselves.
- Concurrency executable experiments must compile as one control participant and one treatment participant sharing a `barrier_group`. The executor must release that group concurrently and emit explicit ready/release/completed timeline events; a sequential treatment-only plan is a protocol bug, not concurrency evidence.
- Contract oracle activation must require a control plan only when the typed assertion semantically requires control or the experiment actually contains control steps. State/validation single-write experiments may activate from source refs, treatment evidence, typed observers, and actor/fixture/cleanup receipts; authorization/isolation/visibility assertions still require control and remain fail-closed.
- Behavior IR validation must report duplicate node identities as `duplicate_node_id:<collection>:<id>`. Source-to-IR conversion may merge canonical operations with explicit source references, but downstream compilers must never receive an IR graph where duplicate node ids were silently dropped or overwritten.
- Conservation executable experiments must use a dedicated `conservation_write` protocol, not the generic treatment fallback. Conservation obligations require source lineage, typed/source-invariant receipts, entity-state snapshot evidence, and assertion DSL `before_values`/`after_values` derived from real governed before/after observations.
- Temporal executable experiments must use a dedicated `temporal_write` protocol plus `temporal_window` typed observer, not a generic HTTP treatment. Temporal windows must come from source-grounded assertion/property evidence; runtime convergence evidence must come from actual trigger/final-observed timeline events and feed assertion DSL `converged`/`within_window`.
- Cleanup requirement detection must compare all non-server-managed business fields on the same observed entity, not only fields echoed by the write response. A write response that returns only an identity still requires cleanup when governed before/after snapshots prove business state changed.
- Empty formal delivery projections may synthesize an explicit empty defect-identity consistency receipt only when there are zero delivery occurrences, zero canonical registry rows, and zero formal customer deliverables. Any non-empty formal delivery scope must validate real canonical identity consistency and fail closed on missing or mismatched scopes.
- Adaptive obligation planning binds the configured slice limit in `qualibug.adaptive-planning-budget.v1`; runtime must never silently increase it. Prior-run `qualibug.adaptive-planning-history.v1` may influence compile/execution conversion only when policy id, policy version, strategy fingerprint, and receipt fingerprint match. Product-owned history must keep formal yield, model cost, and unit deliverable cost `NOT_MEASURED` until authoritative external or provider receipts exist; missing or non-matching history is an explicit cold start.

## Brand Direction Contract

- QualiBug AI is enterprise software behavior-space infrastructure. It maps actors, states, data, rules, and real execution trajectories into a computable, verifiable, evolvable behavior-space model.
- The governed Behavior Field mark is the brand source of truth: Q is the enterprise-system boundary, the plane is behavior space, nodes are states, and the curve is an observed behavior trajectory.
- The login radar is an approved decorative metaphor for enterprise-system behavior observation; it is not a product-health signal and is not part of the governed logo geometry.
- Brand and decorative product visuals use no insect, crawler, spider-web, or scraping semantics; insect, crawler, spider-web, and scraping semantics remain prohibited. `Bug` means a verified divergence between observed and expected behavior.
- Decorative brand motion must never represent actual system health, provider health, campaign health, scan health, model health, evaluator health, or commercial readiness.
- Brand work remains industry-neutral, preserves existing product copy unless separately approved, and keeps frontend 5174 and backend 8088.
