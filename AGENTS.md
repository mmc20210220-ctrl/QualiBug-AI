
## Product Health Checks

When evaluating product readiness or dogfooding bug-finding features, verify observable behavior from the running product and code before reporting status. Treat configured-but-unverified integrations as not online: for model providers, a saved key or endpoint only means "configured" until a real health check succeeds, and failures must be shown as failed/offline rather than healthy.

- The only supported backend launch authority is `ai_test_asset_center.private_pilot_entrypoint:run_server` (including `qualibug-server`). `private_pilot_service` is the core service implementation, not a direct launch path. Service composition (mixins, support modules), hook registration conventions, connector event/OAuth boundaries, and package risk points are the implementation SSOT of `ai_test_asset_center/AGENTS.md`.
- Evidence enrichment may format captured facts and generate clearly marked operator guidance, but it must never infer request bodies, credentials, actors, business rules, entity/table names, SQL, or impact claims. Generated guidance is synthetic and cannot satisfy the customer-delivery gate.
- Generic connector webhook, OAuth, token-refresh, source-first onboarding, connector configuration, provenance projection, credential-label, and operator authority-decision rules are implementation SSOTs of `ai_test_asset_center/AGENTS.md` (Connector Event & Credential Boundaries).

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
| `ai_test_asset_center/discovery_engine/_engine.py` | `__init__` | `timeout_seconds ≥ 300` | Reader prompt needs 150-200s on DeepSeek. Default 120s → silent timeout → loop appears "crashed". |
| `ai_test_asset_center/discovery_engine/_engine.py` | `__init__` | `max_tokens ≥ 32768` | Causality engine produces >41K chars JSON. Truncation at lower values causes engine failures. |
| `ai_test_asset_center/stage_reason_all_v2.py` | `MAX_HYPOTHESES` | `40` | Per-engine hypothesis cap. Raised from 15→40 to achieve 90.8% bug discovery rate. |
| `ai_test_asset_center/stage_reason_all_v2.py` | `max_workers` | `4` | Default parallel engine workers. Higher → API rate limits. |

When refactoring configuration (e.g. Policy Registry migration), always verify these floors are preserved with:
```python
assert engine.client.config.timeout_seconds >= 300, "timeout too low"
assert engine.client.config.max_tokens >= 32768, "max_tokens too low"
```

## One-off Artifact Convention (Repo-root Hygiene Boundary)

The repository root must stay clean: it holds only product code, configuration, and tracked release/test baselines.

- One-off debug scripts (`_*.py`) and run artifacts (`*.json`, `*.txt`, `*.log` outputs of scans, runs, probes, diagnostics) MUST be created in `.scratch/` (git-ignored), never in the repository root.
- Root-level patterns `/_*.py`, `/*.json`, `/_*.txt`, `/_*.log` and `/.scratch/` are ignored in `.gitignore`; the small set of tracked root JSON baselines consumed by product code, tests, or workflows is listed as explicit exceptions there. Do not add new root JSON files unless a tracked consumer reads them; otherwise place them in `.scratch/`.
- When a one-off script graduates into a durable tool, move it into `tools/` (or the owning package) with tests; never keep durable scripts at the root with a `_` prefix.
- Git history retains the migrated pre-cleanup artifacts; `.scratch/` is the local working area only and must never be committed.


1. Fail Fast / Errors Never Pass Silently：不要在代码里藏兜底逻辑来吞掉错误、隐藏问题。出了问题就应该让它爆出来，否则你永远找不到真实问题。
2. Fix the Cause, Not the Symptom / Don't Paper Over Bugs：当一个问题出现时，不要用各种 small fix、针对性补丁来掩盖它。必须定位真实根因，彻底修复。在 bug 上糊纸只会让系统积累你不知道的危险暗病。
3. Make It Observable：即使问题很难定位，也绝不要偷懒做表面修复。应该给项目增加充分的日志和可观测性，保证下次问题再现时你有足够信息去定位。问题无法修复时，只需要诚实告诉我信息不足、需新增日志，不要假装修好了。
4. Design for Debugging / Traceability：始终注意在关键路径上给自己留足排查日志，确保每一个关键节点都是可追溯的。
5. Living Documentation / Single Source of Truth：当项目关键技术栈或产品方向发生变更时，同步更新 agents.md。文档必须随代码一起演进，不能让它变成过时的谎言。
6.所有产品前后端都不能有硬编码，要保持通用性，我做的是全行业适配的，绝对不能有硬编码（适用范围见下方「禁止硬编码的适用范围」小节）
7.测试项目测试bug不能造假数据给我，没有执行找出的bug不要给我，不能给我假数据
8.首先我要的就是全行业不同软件系统都适用，只要违反这个原则都要优化
9.我的产品前端服务端口是5174，后端服务端口是8088，不要搞错了
10.所有优化在现有模块基础上优化，不要重复造轮子，所有优化都要接入主链才算闭环，不要有断点
11.有短板或者漏洞缺陷一定要从根本原因上优化，不要打补丁方式修补
12.所有优化都要从根因上修复优化，换个陌生系统必须也要适用，否则就不算从根因修复优化
### 禁止硬编码的适用范围（原则 6 解释）

原则 6 的禁令针对**行业与业务逻辑层**：任何行业特定术语、业务规则、实体/表名、领域关键词表、客户业务数据、基准答案，都不得写死在产品代码、检测器、prompt、UI 或服务里——这些必须由来源材料/配置在运行时驱动，保证全行业通用（见原则 8）。

禁令**不针对**显式声明的部署与契约标识。下表的字面值是**有意常量**（intentional constants），在本文档中显式声明、有明确来源；记录或引用它们不构成硬编码违规，但任何一处修改都必须同步更新本表（Living Documentation，原则 5）：

| 常量 | 值 | 来源 | 性质 |
|---|---|---|---|
| QualiBug 前端端口 | `5174` | 用户显式声明（原则 9） | 有意常量：产品自身部署标识，非客户业务数据 |
| QualiBug 后端端口 | `8088` | 用户显式声明（原则 9） | 有意常量：产品自身部署标识，非客户业务数据 |
| 基准 manifest 身份 | `v0.6-windows-native-stable-131bugs` | 用户提供的基准包 `BENCHMARK_MANIFEST.json` | 有意常量：目标身份由 manifest 决定而非目录名；仅评估侧引用，绝不进入发现运行时/prompt |
| 基准目标 customer UI 端口 | `3001` | evaluator-profile 数据（基准 manifest，Windows-native 模式） | 有意常量：基准目标侧声明值，产品代码不得依赖 |
| 基准目标 admin UI 端口 | `3002` | evaluator-profile 数据（基准 manifest，Windows-native 模式） | 有意常量：基准目标侧声明值，产品代码不得依赖 |
| 基准目标 API gateway 端口 | `8080` | evaluator-profile 数据（基准 manifest，Windows-native 模式） | 有意常量：基准目标侧声明值，产品代码不得依赖 |
| 基准目标 PostgreSQL 端口 | `5432` | evaluator-profile 数据（基准 manifest，Windows-native 模式） | 有意常量：基准目标侧声明值，产品代码不得依赖 |

判别标准：常量若描述**产品自身部署身份或版本化契约身份**且在此显式登记，属于有意常量；若描述**任何客户/行业/基准目标的业务语义**并进入产品逻辑，即为原则 6 禁止的硬编码。

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
  data. QualiBug remains frontend 5174 and backend 8088. (All values here are
  intentional constants registered in 「禁止硬编码的适用范围」 above; quoting
  them is not a hardcoding violation.)

- Discovery mainline/wrapper structure, hook registration, binding closure,
  diagnostic-only planners, semantic-join limits, multi-layer observation,
  `experiment_candidate` execution, the obligation-attempt ledger and funnel
  closure, stage-receipt identity binding, trace ledger, path-placeholder
  compilation, rollback/reopen lifecycle, and Phase-1 timing receipts are
  implementation SSOTs of `ai_test_asset_center/AGENTS.md` (Discovery Runtime
  Module Notes, Runtime Execution Contract Rules, and Ledger & Run Lifecycle
  Rules).

- The Bug discovery north star is externally measured hidden-ground-truth quality, not internal candidate, confirmed, validated, or funnel counts. Internal counts may diagnose conversion loss but MUST NOT be presented as recall, precision, or commercial capability.
- Harness evolution uses a fixed, versioned evaluator-private manifest. Discovery runtime receives only the runtime view; ground-truth paths and contents must never enter prompts, runtime context, traces, policy proposals, or product-facing outputs.
- Commercial promotion requires paired champion/challenger replay and shadow execution on identical input, fixture, context, environment, held-in, held-out, and intentionally clean targets. Estimated impact is not promotion evidence.
- Only findings that pass the formal customer-delivery gate may be scored as true or false positives. Candidates and internal clues are excluded from the commercial quality score.
- A candidate may be promoted only when no measured split regresses and at least one measured split improves. Missing pipeline health, missing target receipts, missing operational metrics, safety incidents, production requests, cleanup failures, dirty environments, or P0/P1 false positives on a clean target must block promotion visibly.
- Evaluation datasets must remain industry-neutral and data-driven. A commercial generalization claim requires at least three held-out industries; no detector, prompt, UI, or service may encode benchmark answers or customer-specific business rules.
- The authoritative implementation and Goal acceptance gates are documented in `docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md`. The evaluator contract is implemented in `ai_test_asset_center/discovery_evaluation_contract.py` and the external CLI in `tools/discovery_evaluation.py`.
- SPC Phase-1 miss diagnosis (why each known bug was not found) is evaluator-private only: `benchmark_evaluator/miss_diagnosis.py` + `tools/miss_diagnosis.py`. It may load hidden ground truth for post-run diagnosis and must never feed GT into discovery prompts, runtime context, or product outputs. Success metric remains true-positive count on the fixed 131-bug benchmark, not internal funnel counts.
- Benchmark source-material integrity (2026-08-04): the `benchmark_mall` project documents (`projects/benchmark_mall/input/API_SPEC.md`, `platform_inputs/benchmark_mall/API_SPEC.md`) were stale relative to the target's real declared API surface and omitted six endpoints the running target implements (`DELETE /api/cart/items/:id`, `GET/POST/DELETE /api/users/addresses[:id]`, `GET /api/users/admin/search`, `PATCH /api/users/admin/users/:id/balance`). The missing DELETE declarations structurally blocked every source-declared control-fixture seed (H25 cleanup authority gate) and left isolation GET experiments with empty control bodies. The documents were synchronized from the held-in-131 evaluator-profile API_SPEC (`platform_inputs/evaluation-benchmark-mall-held-in-131/API_SPEC.md`, verified against the target service code). This is visible-surface source data, never ground truth; the H25 gate (`tests/test_h25_evaluation_fixture_cleanup.py`) remains the machine check.
- Capability breakthrough architecture and Phase 0–5 migration are defined in `docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md`. The runtime fact flow (Behavior IR → Test Obligations → Executable Experiments → contract oracle → delivery gate), environment/compensation/reset rules, artifact redaction, and product quality projection are implementation SSOTs of `ai_test_asset_center/AGENTS.md`. Champion/candidate comparisons use the frozen **131-bug** evaluator-private GT under `_private_eval/_evaluator_private/benchmark_mall_131/` — never swap to the 71-bug copy for percentage comparisons. Audit packs require a clean committed worktree.
- Absolute Gate D / controlled-pilot / GA thresholds are machine-checked by `assess_discovery_goal_status` (`python tools/discovery_evaluation.py goal-status`). Missing private ground truth, incomplete receipts, or a missing unit-cost baseline must remain `NOT_MEASURED` and must not unlock a higher commercial claim.
- Gate metrics and promotion thresholds have one SSOT: `docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md`. Architecture lives in `docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md`; DOCX files are release exports and must not become independently editable specifications.
- Target authorization, product defect truth, project campaign contracts (`/api/v1` only), the module-retirement strangler inventory, canonical defect identity, and scan-envelope account ownership (workspace reconciliation) each have one implementation SSOT inside `ai_test_asset_center/`; the authoritative module list and their scope rules live in `ai_test_asset_center/AGENTS.md` (Implementation SSOT Registry). Idempotency/concurrency obligation sourcing rules live there too (Discovery Runtime Module Notes).
- Evaluator receipts/reports/comparisons are authenticated evaluator-owned artifacts. Policy comparisons must reload and revalidate all four replay/shadow reports, bind policy id/version/strategy/mainline fingerprints, and recompute GT-free evidence and the promotion decision. Historical signatures are verified through the configured receipt keyring; an invalid or missing signature must fail closed.
- A runtime receipt cannot attest its own network activity. Observed policy evaluation requires `qualibug.evaluator-execution-attestation.v1`, signed from evaluator-owned observations supplied by the trusted observation provider outside the product workspace. Missing trusted observations, subprocess isolation, or exact request coverage keeps the evaluation `NOT_MEASURED`.
- Agent semantic linking, governed UI execution, evaluator-fixture/product campaign identity separation, product identity scopes, canonical defect registry, side-effect-free import, Behavior IR operation identity, runtime interface discovery/expansion rounds, typed observers, concurrency/conservation/temporal protocols, contract-oracle activation, runtime bindings, request-body preservation, delivery-receipt binding, cleanup gates, Behavior IR validation, empty-delivery projections, and adaptive planning budgets are implementation SSOTs of `ai_test_asset_center/AGENTS.md` (Runtime Execution Contract Rules and Implementation SSOT Registry).

## Enterprise Business Comprehension Contract

**This is the product's core capability and its current binding constraint. Read it before proposing any discovery-capability work.**

- The product's core competence is **deep comprehension of real enterprise business**, not probe volume, detector count, or funnel throughput. Only a system that genuinely understands the customer's business can find that customer's real defects. Every capability decision is judged by whether it deepens comprehension, not by whether it adds a mechanism.
- The final goal is **to find every bug in the enterprise system** — full depth and full breadth. Depth means reasoning through multi-step business causality, cross-entity state, and long-running lifecycle, not single-request assertions. Breadth means every operation, actor, state transition, invariant, and cross-system/cross-service/cross-database/cross-message-chain path, not a sampled subset. Anything that structurally caps depth or breadth is a defect in the harness, not an acceptable trade-off.
- **No limitation is acceptable by construction.** Fixed detector lists, closed bug-type taxonomies, capped hypothesis budgets, closed risk-family enumerations that silently coerce unknown families, and single-service assumptions are all prohibited as *structural* limits. Where a bound must exist for cost or safety, it must be an explicit, receipted, operator-visible budget with a named reason code — never a silent truncation and never a hidden default. A bound that cannot be seen in a receipt is a capability ceiling masquerading as a configuration value.
- The scope is **multi-system, multi-service, multi-database, multi-message-chain** enterprise topology, across all industries and all system types. A capability that only holds for one service, one datastore, or one synchronous request path is incomplete, not shipped.
- The verification layer must hold **regardless of who or what operates the system** — human operators, programs, or enterprise AI agents. Actor identity is an input to verification, never an assumption baked into a detector.
- **Comprehension is the measured bottleneck, not a hypothesis.** The authenticated benchmark run recorded in `docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md` attributes the large majority of missed defects to first loss at the **hypothesis-generation / business-comprehension stage**, upstream of compilation and execution. Therefore: capability work that adds execution mechanism while leaving comprehension untouched must not be presented as a recall improvement, and a coverage-recovery change is only credible when a re-run shows the blocked-obligation count actually fall.
- **All bug types are in scope, and the type list is open.** API, UI, business-process logic, performance, compatibility, and stability are *examples*, not an enumeration. Any defect in an enterprise business system is in scope: resource leaks, error-handling and recovery, configuration and deployment drift, data migration and schema evolution, observability gaps, localization, accessibility, availability, capacity and degradation behaviour, and classes that have no accepted name yet. A bug type must never be out of scope because the harness has no slot for it — that is a harness gap to close, not a boundary to document.
- **Bug-type reachability is a four-link chain, and every link is a potential ceiling.** To find a class of defect the product needs (1) an obligation risk family that can express the property, (2) an assertion kind that can state it, (3) an implemented observer that can measure the evidence on the right surface, and (4) an experiment protocol that can execute it. A class missing any one link is *structurally unreachable* no matter how deeply the business is understood. Any capability proposal that claims to add a new bug class must name what it adds at all four links, or say explicitly which link it leaves open.
- **Measured breadth ceiling — gap list as of the last wiring increment (do not treat as acceptable design):** the registry anchors (`test_obligation` risk families, `assertion_dsl_base` assertion kinds, `observer_contracts_base` observers) and the surface mainline wiring (`db_sql` persistence, `event_observer_http`/`ui_browser`, latency/stability surfaces) are implementation anchors of `ai_test_asset_center/AGENTS.md` (Enterprise Comprehension — Implementation Anchors).
  - Consequence to state honestly: performance, compatibility, stability, UI-surface and event/message-chain defect classes are now reachable **only when the customer declares the surface and the source material declares the contract** — `performance_latency`/`stability_reliability` need a source latency/stability contract on a GET/HEAD operation, `event_delivery_consistency` needs a source event contract plus `event_observer_http` in `declared_adapters`, `ui_state_consistency` needs a source UI browser plan plus `ui_browser` declared, and the UI read-only guard blocks any interaction step without cleanup equivalence. Message-chain topologies beyond a single declared HTTP event poll path remain unmeasured. Do not describe these classes as supported without the declarations, and do not report their absence from findings as evidence that a target is clean.
- Closing a breadth gap does not license a shortcut. A new family, assertion kind, or observer still has to be source-grounded, receipted, and fail-closed on missing evidence, and an observer must be genuinely `implemented` — declaring a surface in Behavior IR is not an implementation.
- Comprehension gains are still bound by every existing evidence rule. Understanding the business better must never become permission to infer request bodies, credentials, business rules, entity/table names, SQL, or impact conclusions; a deeper hypothesis still has to become a source-grounded obligation, a governed experiment, and a receipted observation before it is a finding. Better comprehension raises what QualiBug can legitimately *test*, never what it may *assert without evidence*.
- **Runtime observation is a first-class evidence source (source-grounded includes runtime-observed).** Enterprise source material is never complete and can itself be wrong; implicit rules and implicit semantics surface only at runtime. Therefore a finding's evidence basis may be **runtime-observed behavior** (controlled two-arm comparison: the permitted actor succeeds while a prohibited actor also succeeds; an illegal state transition; a conservation break; a leak) — reproducible under a governed experiment, receipted, and cleanup-complete — **without any written source rule for it**. Written rules remain the highest-precision binding channel where they exist, but their absence must not block discovery: it must degrade the obligation to the runtime-observation channel (receipted as such), never silently drop it. This does NOT license inference: the product still never guesses request bodies, credentials, business rules, table names, SQL, or impact — it only tests what it can observe and reproduce. Runtime-observed evidence also feeds finding matching signals (actor role + operation + violation shape + reproduction), so defects whose rule never appears in any document remain findable and matchable.
- **Source-backed business-rule semantic extraction (shadow + augment phases):** the extractor layer split (frozen regex candidate layer vs. open-semantic LLM recall), deterministic evidence validation, runtime modes, the unified rule candidate ledger, and the SPEC §19 promotion gates are implementation anchors of `ai_test_asset_center/AGENTS.md` (Enterprise Comprehension — Implementation Anchors). Only `rule_origin=explicit` candidates may ever enter formal governance; degradation is never silent.

## Brand Direction Contract

- QualiBug AI is enterprise software behavior-space infrastructure. It maps actors, states, data, rules, and real execution trajectories into a computable, verifiable, evolvable behavior-space model. The long-term direction is to become the independent verification layer (Enterprise Behavior Layer) for enterprise-system operations in the AI era — whether performed by humans, programs, or enterprise AI agents. The target is all industries and all system types: manufacturing ERP/MES/WMS is only a beachhead market, never a product boundary, and no capability may be scoped to it. The project is currently in the deep technical-capability stage, not the customer-POC stage. Bug discovery is open-ended by design: bug types (API, UI, performance, authorization, conservation, concurrency, …) are post-hoc classification labels for reporting and regression only — discovery itself is driven by business invariants and system-space coordinate changes, and must never be capped by a fixed detector list or a closed bug-type taxonomy. Any evidenced violation of a business invariant is in scope, including types that have no name yet.
- The governed Behavior Field mark is the brand source of truth: Q is the enterprise-system boundary, the plane is behavior space, nodes are states, and the curve is an observed behavior trajectory.
- The login radar is an approved decorative metaphor for enterprise-system behavior observation; it is not a product-health signal and is not part of the governed logo geometry.
- Brand and decorative product visuals use no insect, crawler, spider-web, or scraping semantics; insect, crawler, spider-web, and scraping semantics remain prohibited. `Bug` means a verified divergence between observed and expected behavior.
- Decorative brand motion must never represent actual system health, provider health, campaign health, scan health, model health, evaluator health, or commercial readiness.
- Brand work remains industry-neutral, preserves existing product copy unless separately approved, and keeps frontend 5174 and backend 8088.
