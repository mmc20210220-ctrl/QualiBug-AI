## Product Health Checks

When evaluating product readiness or dogfooding bug-finding features, verify observable behavior from the running product and code before reporting status. Treat configured-but-unverified integrations as not online: for model providers, a saved key or endpoint only means "configured" until a real health check succeeds, and failures must be shown as failed/offline rather than healthy.

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
  exception.
- `experiment_candidate` planning and execution live in
  `discovery_runtime.py`; selected experiments execute only through
  `experiment_executor.execute_selected_experiments`. The legacy domain may
  run only when `legacy_champion` was explicitly selected before the run. Its
  adapter derives the common attempt ledger only from selected behavior slices,
  redacted execution traces, and runtime-backed findings; operational output
  still must pass the same customer-delivery gate. The execution-policy default
  remains `legacy_champion` until external paired evidence promotes the
  experiment candidate.
- `qualibug.obligation-attempt-ledger.v1` is the completion and funnel SSOT.
  Every selected, blocked, or deferred obligation must have exactly one
  terminal attempt with a reason code. Zero selected obligations and all-
  blocked runs remain visibly `BLOCKED`; empty findings from them must never be
  interpreted as a defect-free target.
- Trace and weakness diagnostics consume
  `qualibug.discovery-trace-ledger.v2`, keyed by obligation attempt identity.
  V1 input requires the explicit offline migration; silent schema fallback is
  prohibited. Replay and shadow runs set `customer_outputs_published=false`.
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
- Project campaign contracts live in `ai_test_asset_center/campaign_api_contract.py` and are exposed only under `/api/v1`. Evaluation submissions must be Ground-Truth-free, pass `artifact_redactor.py`, and stay `NOT_MEASURED` until an external evaluator receipt is verified.

## Brand Direction Contract

- QualiBug AI is enterprise software behavior-space infrastructure. It maps actors, states, data, rules, and real execution trajectories into a computable, verifiable, evolvable behavior-space model.
- The governed Behavior Field mark is the brand source of truth: Q is the enterprise-system boundary, the plane is behavior space, nodes are states, and the curve is an observed behavior trajectory.
- The login radar is an approved decorative metaphor for enterprise-system behavior observation; it is not a product-health signal and is not part of the governed logo geometry.
- Brand and decorative product visuals use no insect, crawler, spider-web, or scraping semantics; insect, crawler, spider-web, and scraping semantics remain prohibited. `Bug` means a verified divergence between observed and expected behavior.
- Decorative brand motion must never represent actual system health, provider health, campaign health, scan health, model health, evaluator health, or commercial readiness.
- Brand work remains industry-neutral, preserves existing product copy unless separately approved, and keeps frontend 5174 and backend 8088.
