# QualiBug 全行业自主 Bug 发现能力突破实施 Spec

> 文档状态：实施中；工程主链已落地，Gate D 仍为 `NOT_MEASURED`
> 面向执行者：Grok 4.5 及后续代码审计者
> 上位约束：`AGENTS.md`、`docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md`
> 产品端口：前端 `5174`，后端 `8088`
> 核心原则：全行业、来源驱动、真实执行、完整证据、隐藏真值隔离、失败显式、禁止客户或靶场硬编码
>
> 文档治理：本文件描述实现架构，指标和晋级门槛只以
> `docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md` 为准。DOCX 仅是发布导出件，
> 不得独立修改形成第二事实源。

## 1. 决策摘要

QualiBug 当前的主要瓶颈不是“LLM 没有生成足够多的 Bug 猜想”，而是从资料理解到正式缺陷之间的整条链路发生了严重的信息丢失、执行损耗和误判：

1. 多源资料被压缩为文本或宽泛的通用承诺，缺少统一、可执行、带来源引用的行为模型。
2. LLM/分析器的丰富验证计划经过 `hypothesis_slice_bridge.py` 后通常只剩“一个 endpoint + 一个通用 Oracle”，控制组、处理组、前置状态、角色、数据依赖、观察面和清理计划被丢失。
3. 运行时大量探针缺少真实 ID、请求体、角色或前置数据，导致候选无法触达真实业务状态。
4. 多个业务 Oracle 以 HTTP 状态码或字段名启发式代替业务结果验证，形成高误报；例如“两次并发请求都返回 200”不能单独证明竞态 Bug。
5. 当前规划单位是“假设数量/切片数量”，不是“尚未覆盖的业务义务”；更多候选只会增加成本、重复和错误探针。
6. 内部计数、产品分数、正式交付门禁和外部评测之间不一致，甚至在 `NOT_MEASURED` 时仍显示接近满分，无法支撑商业承诺。
7. 评测提交物中存在明文密码和 JWT，属于必须先修复的证据治理问题。

因此，本 Spec 不要求继续堆叠 detector、prompt 或行业规则。目标是把主链重构为：

```text
企业多源资料 + 目标环境观测
  -> 带来源和置信度的 Behavior IR
  -> 可度量的 Test Obligations
  -> 保留完整验证意图的 Executable Experiments
  -> 受治理的 Fixture / Binding / Execution
  -> Contract-based Oracles + Disprover
  -> 客户可交付缺陷
  -> 外部隐藏真值评测
  -> 非回归 Harness 演进
```

不得新建一条旁路扫描链。上述对象必须进入现有 `run_v12_pipeline(...)` 主链，旧的切片、场景和 Oracle 逐步通过兼容适配器迁移，最终收敛到一个执行与记账事实源。

## 2. 当前真实基线

### 2.1 单靶场诊断结果

基于 2026-07-10 最新真实运行产物 `_funnel_runs/llm_throughput.json` 和对应 evaluator submission 的外部只读诊断：

| 指标 | 当前观测 |
|---|---:|
| 隐藏真值 Bug | 131 |
| LLM 假设 | 676 |
| 候选行为切片 | 444 |
| 入选执行 | 122 |
| 形成可验证轨迹 | 86 |
| 运行产物 findings | 91 |
| 按当前 customer-delivery gate 重算的可交付 findings | 34 |
| 去重后正式 findings | 21 |
| 真阳性 | 6 |
| 假阳性 | 15 |
| 假阴性 | 125 |
| Recall | 4.58% |
| Precision | 28.57% |
| F1 | 7.89% |

这只是单靶场外部诊断，不是完整的商业基线。完整商业基线仍须满足冻结 manifest、held-in、至少三个 held-out 行业、clean target、replay 和 shadow 的要求。

### 2.2 漏斗与可信度异常

- `dropped_no_endpoint = 71`。
- `missing_runtime_path_binding = 39`，最新 funnel 的 skip telemetry 仍记录 26 个路径绑定和 4 个请求体绑定缺口。
- 34 个可交付 findings 中有 13 个被正式去重，重复率过高。
- 57 个未通过当前交付门禁的 findings 中，56 个包含 `CLEANUP_NOT_SUCCEEDED`。
- 去重后的 15 个假阳性主要集中在 Permission、Isolation 和 Concurrency Oracle。
- Reasoner 报告 22 次模型请求，但 `model_usage.request_count`、tokens 和 cost 全部为 0，成本观测失真。
- 运行仍显示产品 `score = 100.0`，同时外部评测状态是 `NOT_MEASURED`；该分数不得被解释为 Bug 发现能力。
- `_funnel_runs/llm_throughput.evaluation_submission.json` 当前包含可恢复的 JWT 和测试账号明文密码。任何后续持久化与导出都必须在写盘前完成递归脱敏。

### 2.3 结论

当前不能按“全行业全自动 Bug 发现产品”商业化。可以继续作为内部研发系统，但在达到本文 Gate D 之前不能对外承诺自主发现能力；达到 controlled pilot gate 后只能做受控私有试点；达到 Autonomous GA gate 后才能使用“全自动”主张。

## 3. 目标、非目标与不可变约束

### 3.1 产品目标

给定任意已明确声明为非生产的项目环境，以及客户提供的 PRD、API、数据库 Schema、角色权限、UI 资料、历史缺陷、日志/事件说明等多源资料，QualiBug 应当：

1. 自动构建可追溯的系统行为模型，并显式标记冲突、未知和缺口。
2. 自动生成行业无关、系统来源驱动的测试义务。
3. 自动准备受治理的测试数据和角色上下文。
4. 自动执行 API、UI、DB、异步事件等可用观察面上的探针。
5. 只把真实执行并通过正式证据门禁的结果作为客户 Bug。
6. 为每个 Bug 提供可重放步骤、请求/响应、业务断言、before/after、清理结果和审计回执。
7. 通过外部隐藏真值评测持续演进 Harness，且不得让隐藏答案进入运行时。

### 3.2 非目标

- 不承诺发现 100% 的未知 Bug。
- 不通过增加 benchmark endpoint、Bug ID、客户名称或行业特例提升分数。
- 不通过降低证据门禁、把 candidate 改名为 confirmed、扩大模糊匹配阈值提升指标。
- 不把源代码扫描默认开启；只有客户明确提供并授权时才作为独立 source adapter。
- 不允许模型直接生成并执行任意 Python/SQL/shell。所有动作必须编译到受限 Experiment DSL 和受治理执行器。
- 不在生产环境执行写探针；unknown environment 继续 fail-closed。

### 3.3 冻结约束

- `timeout_seconds >= 300`。
- `max_tokens >= 32768`。
- `MAX_HYPOTHESES = 15`。
- `max_workers = 4`。
- 前端端口 `5174`，后端端口 `8088`。
- 每个实际 HTTP 写操作必须经过 governed sandbox executor，并独立产生审计回执。
- 写场景发生可能已接受的写入后，不得重试整个场景；部分 setup 失败时按逆序补偿并保留原始错误。
- customer-delivery evidence threshold 不得为提高分数而降低。
- evaluator、隐藏真值、数据集切分和 target fixture 在同一 champion/challenger 比较中不可修改。

## 4. 根因与对应架构决策

### R1：多源知识没有形成可执行事实模型

当前 `enterprise_knowledge_center.py` 已能提取 rules、permissions、relationships 等信息，但 `v12_pipeline.py::_knowledge_asset_planning_text(...)` 又把结构化资产压回自然语言；下游无法稳定引用实体、操作、角色、状态与关系。

决策：新增版本化 `Behavior IR`，所有推理、义务、实验和证据只引用 IR 中存在的 ID。自然语言只用于解释，不再是主链内部事实源。

### R2：Hypothesis -> Slice 转换破坏验证意图

`hypothesis_slice_bridge.py` 当前把 hypothesis 绑定为单 endpoint，并根据 family 选择一个通用 Oracle；原始多步骤 `verification_method`、角色、前置条件、观测面和反证条件未被完整保留。

决策：用 `TestObligation -> ExecutableExperiment` 编译链替代单 endpoint slice。无法完整编译的义务进入显式 coverage gap，不得执行一个语义不同的替代探针。

### R3：测试数据和运行时绑定不是场景级能力

当前虽然能成功创建一个 bootstrap fixture，但大量场景仍使用未绑定 `{id}`，且 owner/viewer、tenant、状态前置、关联实体没有统一的 resolver graph。

决策：每个 Experiment 必须先通过静态 binding check，再由场景级 Fixture Planner 构建依赖 DAG。所有 ID 必须来自真实 list/read/setup response 或明确的 disposable fixture receipt。

### R4：业务 Oracle 使用启发式代理真实业务结果

典型问题包括：

- `ConcurrencyOracle` 把两个 200 当作竞态。
- `IdempotencyOracle` 只看重复请求成功，不比较业务副作用。
- `StateOracle` 主要依赖 status code 和宽泛 forbidden flag。
- `PermissionOracle` 依赖预填 expected status，但缺少同资源的 authorized control。
- `PrivacyOracle` 不能在没有来源隐私策略时把所有手机号或 token 字段一概判 Bug。

决策：协议 Oracle 与业务 Oracle 分层。协议 Oracle 可通用运行；业务 Oracle 只能执行从来源编译出的 typed assertion，并满足对应的 control、fixture 和 observer requirements。

### R5：规划目标是候选数量，不是覆盖和信息增益

676 条 LLM 假设没有转化为同比例的真实 Bug。当前优先级主要来自模型 confidence、severity 和简单多样性，缺少执行成功率、历史正式 TP yield、覆盖新颖度、控制组可用性和单位成本。

决策：Planner 以 `TestObligation` 为最小单元，优化“正式真阳性信息增益”，不是最大化候选数量。

### R6：度量、健康状态和产品展示不是一个事实源

内部 validated、formal deliverable、submission、去重后 findings 和 evaluator metrics 当前会产生不同计数；`NOT_MEASURED` 仍可显示 100 分。

决策：评测与商业主张只读取 evaluator report；运行时计数仅用于漏斗诊断。所有页面和 API 使用同一 projection，`NOT_MEASURED` 时不得生成质量分。

### R7：证据持久化泄露凭证

运行提交中存在 JWT 和密码。现有局部 `_redact` 不能保证所有 artifacts 在写盘前脱敏。

决策：在 artifact persistence boundary 增加统一递归 redaction 和 secret scanner。运行时通过 `secret_ref` 获取凭证，持久化只保留不可逆指纹、角色和是否存在，禁止保留 secret value。

## 5. 目标领域模型

### 5.1 Behavior IR

新增 `ai_test_asset_center/behavior_ir.py`，定义不可变、可序列化、带 schema version 的内部模型。若仓库已有等价模型，应扩展并统一，不得再并行创建第二套事实模型。

最小结构：

```json
{
  "schema_version": "qualibug.behavior-ir.v1",
  "model_id": "content-addressed-id",
  "project_id": "opaque-project-id",
  "source_snapshot_hash": "sha256",
  "sources": [],
  "entities": [],
  "operations": [],
  "actors": [],
  "states": [],
  "relations": [],
  "invariants": [],
  "observation_surfaces": [],
  "capabilities": [],
  "conflicts": [],
  "coverage_gaps": []
}
```

每个事实节点必须包含：

- 稳定 ID；
- typed fields；
- `source_refs[]`，包含 source ID、版本、locator、quote hash；
- `confidence`；
- `derivation`，区分 explicit、schema-derived、runtime-observed、model-inferred；
- `status`，至少包括 accepted、conflicting、unsupported、unknown；
- 不得包含隐藏真值引用。

`Operation` 不只保存 path，还要保存 method、operation ID、request/response schema、parameters、security、side-effect class、read/write、可能的 entity/state 关系以及可用 examples。

`Actor` 保存角色、tenant/scope、credential `secret_ref`、账号状态和来源，不保存明文凭证。

`Invariant` 必须是 typed expression，不得只是一段无法执行的文本。自然语言说明保存在 `description`，实际验证使用受限 DSL。

### 5.2 Test Obligation

新增 `ai_test_asset_center/test_obligation.py`：

```json
{
  "obligation_id": "stable-id",
  "risk_family": "authorization|isolation|state|conservation|...",
  "subject_refs": [],
  "property": {},
  "required_actors": [],
  "required_operations": [],
  "required_fixtures": [],
  "required_observers": [],
  "cleanup_requirement": {},
  "source_refs": [],
  "confidence": 0.0,
  "compile_status": "PENDING"
}
```

义务只能由以下来源产生：

1. IR 中的显式业务规则；
2. 结构化 Schema 约束；
3. API/角色/状态/关系交叉形成的行业无关属性模板；
4. 有来源引用且只引用 IR ID 的模型推理；
5. 已正式确认 Bug 的 pattern-level 知识，不包含客户实例答案。

### 5.3 Executable Experiment Contract

新增 `ai_test_asset_center/experiment_contract.py` 与 `experiment_compiler.py`。最小结构：

```json
{
  "schema_version": "qualibug.experiment.v1",
  "experiment_id": "stable-id",
  "obligation_id": "...",
  "policy_version": "...",
  "control_plan": [],
  "treatment_plan": [],
  "binding_plan": [],
  "setup_plan": [],
  "assertions": [],
  "observers": [],
  "async_observation_policy": {},
  "cleanup_plan": [],
  "safety_contract": {},
  "source_refs": [],
  "compile_receipt": {}
}
```

编译成功必须同时满足：

- 所有 operation 指向 Behavior IR 中真实存在的 operation ID；
- 所有 path/body/query/header placeholder 有 binding source；
- 所有 actor 有可验证 credential reference 或明确 anonymous actor；
- 负向测试有同一契约的有效正向 control；
- 业务断言有可用 observer；
- 写操作有可执行或显式声明的 cleanup/compensation；
- target environment 已声明为 non-production；
- 证据收集要求可满足。

编译失败使用稳定 reason code：

- `BLOCKED_MISSING_OPERATION`
- `BLOCKED_MISSING_ACTOR`
- `BLOCKED_MISSING_FIXTURE`
- `BLOCKED_MISSING_BINDING`
- `BLOCKED_MISSING_OBSERVER`
- `BLOCKED_NON_REVERSIBLE_WRITE`
- `BLOCKED_CONFLICTING_SOURCE`
- `BLOCKED_UNSUPPORTED_ADAPTER`

禁止把编译失败的 obligation 静默丢弃，也禁止退化为语义不同的单 GET/单状态码探针。

### 5.4 Assertion DSL

新增受限、无 `eval` 的 assertion evaluator，至少支持：

- HTTP status class / exact status；
- JSON path 存在、类型、集合关系、数值比较；
- 两次 observation 的 equality/delta；
- collection cardinality；
- state transition；
- owner/tenant visibility；
- conservation equation；
- idempotency effect cardinality；
- concurrency final invariant；
- bounded eventual consistency window；
- DB/API/UI 跨观察面一致性。

所有 assertion 必须记录 expected、actual、observer receipt 和 source refs。

## 6. 行业无关属性模板

模板只能绑定 Behavior IR 角色，不得绑定名称字符串。示例：

| 属性族 | Control | Treatment | 正式确认要求 |
|---|---|---|---|
| Authorization | 允许角色对同一资源成功 | 非允许角色执行同一操作 | actor 身份真实、资源相同、control 成功、treatment 越权且业务结果生效 |
| Isolation | owner 可访问自有对象 | 另一 actor/tenant 访问 owner 对象 | owner binding、viewer binding、对象归属证据、返回体或 DB/UI 泄露证据 |
| State | 将实体置于已观察前置状态 | 执行允许或禁止转换 | before state、动作、after state、来源状态规则 |
| Idempotency | 一次逻辑写入 | 同 idempotency identity 重放 | 比较业务 effect，而不是只比较 HTTP status |
| Concurrency | 顺序执行基线 | 同一冲突资源的同步并发 | barrier 时间线、最终状态、不变量或版本冲突证据；两个 2xx 不是充分条件 |
| Conservation | 读取各组成量 | 执行动作后再读取 | typed equation 的 before/after 值与容差 |
| Validation | 来源合法样本成功 | 单维度 mutation | 同一契约 control 成功，mutation 被错误接受或产生非法状态 |
| Visibility | 来源允许状态可见 | 禁止状态/范围查询 | 返回记录与状态/范围绑定，不以字段名猜测 |
| Temporal | 边界内 control | 边界外/过期/未来处理 | 使用可控时钟或明确时间来源，禁止用机器当前时间做宽泛推断 |
| Privacy | 来源允许字段集合 | 非允许 actor/surface 请求 | 必须有隐私/字段可见性来源，不能因为看到手机号就直接报 Bug |

现有 `oracle_engine.py` 中的业务启发式 Oracle 必须进入以下三种状态之一：

1. 重写为上述 contract-based Oracle；
2. 降级为内部 clue，不可进入 customer-delivery gate；
3. 删除。

协议级 Oracle（5xx、结构无法解析、明确契约不一致等）可以保留，但也必须排除 harness error、无效凭证、未绑定请求和 fixture failure。

## 7. Fixture、Binding 与执行架构

### 7.1 Runtime Binding Graph

新增 `ai_test_asset_center/runtime_binding_graph.py`。Binding source 优先级必须可追溯：

1. 当前 Experiment setup response；
2. 同 actor 的已验证 list/read response；
3. evaluator-frozen fixture/runtime context；
4. disposable fixture receipt；
5. API 文档 example；
6. Schema 生成值。

低优先级值不得覆盖高优先级运行时值。每次替换记录 source、field/path、原值指纹和新值指纹。

### 7.2 场景级 Fixture DAG

扩展 `auto_test_data_factory.py`，不再只提供一个 campaign bootstrap：

- 根据 operation schema、DB foreign key、IR relation 和 experiment requirements 建立 setup DAG；
- 支持 owner/viewer、tenant A/B、前置 state、关联实体和可冲突资源；
- 创建后必须通过 read observer 证明 fixture 可见且满足前置条件；
- cleanup 按 DAG 逆序执行；
- setup 被拒绝不是 cleanup failure；只有已经接受的写入未被清理才是 cleanup failure；
- 一个 actor 无权限 setup 时应尝试来源允许的 fixture actor，而不是修改业务期望；
- fixture 不可构造时阻断 Experiment 并记录缺口，禁止使用假 ID 继续执行。

### 7.3 执行器

扩展 `sandbox_write_executor.py`、`grounded_probe_executor.py` 和 `v12_pipeline.py`：

- 每个 step 有 `step_id`、`experiment_id`、`obligation_id`、`actor_id`、`target_id`；
- 写请求前记录 before observation 和 governance decision；
- 写请求后记录 response、after observation、cleanup outcome；
- 多写 scenario 每个写均调用治理 hook；
- 读取可有有界 retry；可能已接受的写不得整体 retry；
- async observer 使用策略化窗口、退避和停止条件，不能固定 sleep；
- trace 同时保留 control 和 treatment，不得只保存失败步骤。

### 7.4 Adapter Contract

新增统一 `ExecutionAdapter` 接口，先把已有能力接入同一主链：

- HTTP/API；
- Browser/UI；
- DB read snapshot；
- 日志/审计记录；
- 可选 event/job adapter。

Adapter 必须声明 capabilities、read/write classification、required credentials、receipts 和 cleanup semantics。缺少 adapter 时显示 `BLOCKED_UNSUPPORTED_ADAPTER`，不得声称已覆盖该观察面。

## 8. Planner 与 Harness 演进

### 8.1 规划单位

`adaptive_discovery_planner.py` 以义务覆盖为目标。候选排序至少使用：

```text
score =
  severity_weight
  * coverage_novelty
  * source_confidence
  * predicted_compile_success
  * predicted_execution_success
  * predicted_formal_yield
  * information_gain
  / expected_cost
```

不得把模型自报 confidence 直接当成正式 yield。权重只能通过历史正式 evaluator receipts 和运行 trace 学习。

### 8.2 多样性与预算

- 按 risk family、entity、operation、actor pair、surface 和 experiment type 设置最小覆盖配额；
- 同一 endpoint 的重复 hypothesis 先聚类，再生成少量有差异的 experiment；
- 预算首先保证可编译且未覆盖的高价值义务；
- 未执行义务必须进入 next-round ledger；
- campaign completed 只能表示所有 in-scope obligations 已确认、已明确阻断或已达到可解释停止条件，不能仅表示 round limit 用完。

### 8.3 学习边界

- 运行时只学习 pattern-level 成功/失败、compile/execution/gate yield 和成本；
- 隐藏 ground-truth instance、Bug ID、endpoint 答案和 match keywords 不得进入 policy proposal；
- champion/challenger 必须按 `DISCOVERY_HARNESS_EVOLUTION_GOAL.md` 进行 paired replay + shadow；
- 不允许用 estimated impact 替代真实运行；
- clean target 上的 P0/P1 假阳性为硬阻断。

## 9. Evidence、去重、门禁与安全

### 9.1 Canonical Defect Signature

去重从标题相似度迁移到：

```text
target + normalized operation + property/invariant id + actor relation
+ resource identity class + observed outcome signature
```

相同根因的多次 reproduction 合并为一个 defect 的多个 evidence receipts；不同 mutation 或不同业务结果不得错误合并。

### 9.2 正式门禁

`customer_delivery_gate.py` 成为唯一实现，以下调用方只能引用它，不得各自复制判断：

- `discovery_funnel.py`
- evaluator submission builder
- command center/backend API
- frontend projection
- `discovery_evaluation_contract.py`

门禁必须区分：

- `customer_deliverable_defect`
- `executed_clue`
- `harness_failure`
- `blocked_experiment`
- `not_executed`

正式 defect 至少需要：真实 treatment 执行、有效 control、typed assertion、expected/actual、可重放步骤、必要 observer、cleanup 成功/无需 cleanup、完整审计 identity。

### 9.3 Artifact Redaction Boundary

新增 `ai_test_asset_center/artifact_redactor.py`，所有 JSON/JSONL/report/evaluator submission 写盘前调用：

- JWT、Bearer、Cookie、password、secret、API key、private key、DSN credential 递归脱敏；
- 保留 `secret_present`、secret type、不可逆 hash 和 vault reference；
- 不得依靠前端隐藏；磁盘中的原始 artifact 本身必须安全；
- redaction 后运行 secret scanner，命中高置信 secret 时写盘失败并将 pipeline 标记为 `FAILED_SAFE`；
- evaluator matching 所需的业务字段可以保留，但凭证值永远不需要进入 evaluator。

现有已经包含 secret 的 artifact 不得作为可分发商业证据包。

## 10. 健康状态、观测与产品真相

### 10.1 Pipeline Health

`build_pipeline_health(...)` 必须把以下任一条件视为非 OK：

- `result.error` 非空；
- 关键 phase 缺失；
- execution 未完成或无真实流量；
- engine failed/degraded 超过策略阈值；
- model request 已发生但 usage/cost unknown；
- binding、fixture、observer 或 cleanup 阻断；
- evaluator submission secret scan 失败；
- candidate lineage 不完整；
- target cleanliness 未证明。

不得使用 `except Exception: pass` 隐藏关键路径失败。可选 adapter 失败可以让对应 capability 进入 `DEGRADED/BLOCKED`，但必须带结构化 error、stack/trace reference 和影响范围。

### 10.2 Cross-stage Ledger

每个 obligation/candidate/experiment 从生成到正式记账使用同一 lineage：

```text
source facts -> obligation -> compile receipt -> selection receipt
-> fixture receipts -> execution steps -> oracle assertions
-> disprover verdict -> delivery gate -> evaluator match class
```

必须能按以下维度解释损耗：source、engine、risk family、entity、operation、adapter、actor、compile reason、execution reason、oracle、gate reason 和 round。

### 10.3 产品 UI/API

修改现有 Dashboard、CoverageMatrix、Findings、EvidenceChain 和 TestTasks 页面，不另建 demo 页面：

- `NOT_MEASURED` 显示为“尚未完成外部质量评测”，不显示 100 分；
- 区分外部质量指标与内部漏斗指标；
- 显示 obligations 总数、已编译、已执行、正式缺陷、阻断原因；
- 显示当前 target/split/policy/manifest fingerprints；
- 显示 engine/adapter/fixture/cleanup health；
- Findings 默认只显示 customer-deliverable defects；clues 和 harness failures 独立入口；
- 所有数字来自后端统一 projection，前端不自行重算。

### 10.4 已实现的项目 API 契约

保留 `/api/v1` 作为唯一公共前缀：

- `POST /api/v1/projects/{project_id}/environment/preflight` 返回版本化输入检查、
  `blocking_codes` 和唯一 `TargetPolicyDecision`；
- `POST /api/v1/projects/{project_id}/campaigns` 创建 `draft|ready` campaign，
  `POST .../campaigns/{campaign_id}/run|resume` 进入现有 V12 主链；
- `GET .../campaigns/{campaign_id}` 及其 `slices`、`identity-traces`、
  `findings?classification=deliverable|candidate|rejected`、finding `evidence`
  和 `replay` 资源均读取同一 projection；
- `POST /api/v1/evaluations/submissions` 仅生成脱敏且不含 Ground Truth 的
  envelope，初始状态固定为 `NOT_MEASURED`。

Campaign、Pipeline Health、Execution 枚举分别固定为
`draft|ready|running|partial|blocked|failed_safe|completed`、
`OK|DEGRADED|BLOCKED|FAILED_SAFE` 和
`completed|partial|blocked|failed_safe|not_executed`。所有错误包含 `stage`、
`code`、`identity`、`retryability`、`operator_action`，所有合同包含 schema
version 和不可变指纹。

## 11. 文件级实施范围

执行者必须先检查是否存在等价模块，优先扩展现有 SSOT，避免重复实现。

### Phase 0：可信评测与安全基线

新增或修改：

- `ai_test_asset_center/artifact_redactor.py`
- `ai_test_asset_center/customer_delivery_gate.py`
- `ai_test_asset_center/discovery_funnel.py`
- `ai_test_asset_center/scan_operational_metrics.py`
- `ai_test_asset_center/discovery_trace_ledger.py`
- `ai_test_asset_center/discovery_evaluation_contract.py`
- `ai_test_asset_center/benchmark_compute.py`
- `tools/discovery_evaluation.py`
- `_funnel_benchmark.py`
- frontend 的 Dashboard / Coverage / Findings / Evidence projection

交付：

- 冻结单靶场诊断 baseline receipt；
- 正式 count SSOT；
- `NOT_MEASURED` 不再显示质量分；
- submission 无 secret；
- usage/cost unknown 不再显示 0；
- health fail-fast。

### Phase 1：Behavior IR 与 Obligation Compiler

新增或修改：

- `ai_test_asset_center/behavior_ir.py`
- `ai_test_asset_center/test_obligation.py`
- `ai_test_asset_center/obligation_compiler.py`
- `ai_test_asset_center/enterprise_knowledge_center.py`
- `ai_test_asset_center/business_state_graph.py`
- `ai_test_asset_center/system_behavior_space.py`
- `ai_test_asset_center/reasoner_prompt.py`
- `ai_test_asset_center/stage_reason_all_v2.py`

要求：

- LLM 输出必须引用 operation/entity/invariant IDs；
- 删除生产 prompt 对订单、SKU、优惠券、商城或 QualiBug 自身 endpoint 的系统性偏置；
- examples 改为 pattern-level schema，或根据当前 IR 动态实例化；
- `analyzers_adapter.py` 不再依赖固定行业路径前缀；
- `supplementary_behavior_slices.py` 中 address/order/cart/user/account/inventory 等路径 token 不能决定通用探针生成。

### Phase 2：Experiment Compiler、Binding 与 Fixture

新增或修改：

- `ai_test_asset_center/experiment_contract.py`
- `ai_test_asset_center/experiment_compiler.py`
- `ai_test_asset_center/runtime_binding_graph.py`
- `ai_test_asset_center/auto_test_data_factory.py`
- `ai_test_asset_center/semantic_scenario_generator.py`
- `ai_test_asset_center/hypothesis_slice_bridge.py`
- `ai_test_asset_center/sandbox_write_executor.py`
- `ai_test_asset_center/grounded_probe_executor.py`
- `ai_test_asset_center/v12_pipeline.py`

要求：

- 旧 slice 只能作为 obligation adapter 输入；
- 禁止将 rich verification plan 降为单 endpoint fallback；
- 编译前完整 binding check；
- 场景级 fixture DAG；
- control/treatment/observer/cleanup 全链。

### Phase 3：Contract-based Oracle 与正式去重

新增或修改：

- `ai_test_asset_center/assertion_dsl.py`
- `ai_test_asset_center/contract_oracles.py`
- `ai_test_asset_center/oracle_engine.py`
- `ai_test_asset_center/runtime_verifier.py`
- `ai_test_asset_center/discovery_finding_gate.py`
- `ai_test_asset_center/customer_delivery_gate.py`
- canonical defect registry/dedupe 现有 SSOT

要求：

- Oracle activation requirements；
- control 与 treatment 对比；
- 业务结果和副作用观察；
- verifier 先排除 harness failure；
- 可选独立 disprover 必须引用相同 evidence contract，不能凭语言偏好推翻事实。

### Phase 4：Adaptive Planner 与多观察面

新增或修改：

- `ai_test_asset_center/adaptive_discovery_planner.py`
- `ai_test_asset_center/policy_registry.py`
- `ai_test_asset_center/policy_wiring.py`
- `ai_test_asset_center/observed_product_scan_executor.py`
- `ai_test_asset_center/v12_pipeline.py`
- UI/browser、DB、log/event adapters

要求：

- obligation coverage 与 information gain 排序；
- 每轮正式 yield、compile/execution success、duplicate、cost 可观测；
- adapter capability 显式；
- 未支持 surface 不得伪装已覆盖。

### Phase 5：受治理演进与商业门禁

修改：

- `ai_test_asset_center/discovery_weakness_miner.py`
- `ai_test_asset_center/discovery_harness_proposer.py`
- `ai_test_asset_center/discovery_policy_evaluation_runner.py`
- `ai_test_asset_center/policy_evaluation_gate.py`
- `tools/run_observed_discovery_policy_evaluation.py`
- command center/backend/frontend quality projection

要求：

- weakness cluster 使用 compile/execution/verifier/evaluator trace，不用标题聚类；
- proposal 只能修改 bounded harness surfaces；
- 四份完整 champion/challenger replay/shadow report 才允许 promotion；
- rollback、lineage、post-promotion monitoring 有不可变 receipt。

## 12. 测试策略

### 12.1 单元与契约测试

至少新增：

- Behavior IR schema、source refs、conflict/gap 测试；
- obligation 去重和通用属性模板测试；
- experiment compiler 全字段/阻断 reason 测试；
- control/treatment 保留测试；
- binding provenance 和 fixture DAG 测试；
- 每类 contract Oracle 的正例、反例和 harness-error 反例；
- secret redaction 与拒绝写盘测试；
- pipeline health 对 result.error/no traffic/unknown cost 的测试；
- delivery count 在 funnel/submission/evaluator/API 一致测试；
- 禁止 benchmark/customer/industry endpoint 硬编码静态检查。

### 12.2 行业无关集成测试

至少覆盖三个彼此不同的 held-out 行业和一个 clean target。数据和规则来自各自 source assets，测试代码不得写入答案。必须证明同一属性模板可绑定不同实体/操作名称。

### 12.3 Live target 测试

- 每个正式 finding 都来自真实 HTTP/UI/DB/event observation；
- 运行前后 target reset/cleanliness receipts 完整；
- 不得把模拟 finding 注入 scan result；
- 没执行的义务只计 coverage gap；
- 外部 evaluator 单独读取 hidden truth；
- current benchmark 只作为一个 held-in 或 held-out target，不能代表全行业能力。

### 12.4 修改后必做检查

每修改一个 Python 文件立即执行：

```powershell
python -c "import ast; ast.parse(open('path/to/file.py').read()); print('OK')"
```

阶段完成时至少执行：

```powershell
python -m pytest -q
python tools/discovery_evaluation.py inspect --manifest <private-manifest>
python tools/discovery_evaluation.py evaluate --manifest <private-manifest> --target-id <target> --run-envelope <run-envelope> --output-root <private-receipt-root>
python tools/discovery_evaluation.py aggregate --manifest <private-manifest> --receipt-dir <receipt-dir> --output <report.json>
```

前端变更还必须执行 lint、typecheck/test 和 build，并用前端 `5174`、后端 `8088` 的真实服务验证关键页面。

配置守卫：

```python
assert engine.client.config.timeout_seconds >= 300, "timeout too low"
assert engine.client.config.max_tokens >= 32768, "max_tokens too low"
```

并验证 `MAX_HYPOTHESES == 15`、`max_workers <= 4`。

## 13. 阶段验收门

### Gate A：可信度与安全

- evaluator submission 和商业 evidence package 无明文 secret；
- `NOT_MEASURED` 不显示 recall/precision/quality score；
- funnel、delivery gate、submission、evaluator 的正式 finding count 一致；
- `result.error`、no traffic、unknown usage、cleanup failure 均阻止健康声明；
- baseline、manifest、policy、target、source、fixture fingerprints 可追溯。

### Gate B：编译与执行

- 100% obligation 有 lineage；
- 100% experiment 要么编译成功，要么有稳定 blocking reason；
- 编译成功 experiment 的 unresolved placeholder = 0；
- execution success rate >= 90%；
- 写探针 audit receipt coverage = 100%；
- cleanup success = 100%；
- control/treatment evidence completeness >= 95%。

### Gate C：误报治理

- clean target P0/P1 false positives = 0；
- 所有 Permission/Isolation finding 均有同资源 authorized control；
- 所有 Concurrency/Idempotency finding 均有业务 effect 或最终不变量证据；
- harness error 被计为 harness failure，不进入 defect；
- duplicate rate <= 15%。

### Gate D：能力突破

沿用 `DISCOVERY_HARNESS_EVOLUTION_GOAL.md`：

- hidden benchmark Recall >= 30%；
- Precision >= 50%；
- reproduction rate >= 90%；
- held-out macro industry Recall >= 25%，且任何行业不低于 15%；
- clean target P0/P1 false positives = 0；
- unit cost per true positive 相对冻结 baseline 至少降低 40%。

达到 Gate D 后只能进入“人工复核的发现加速器”私有 Alpha，不得使用全自动商业主张。

### Controlled commercial pilot gate

沿用 Goal 文档的 commercial exit 指标：

- held-out macro industry Recall >= 50%；
- Precision >= 70%；
- reproduction rate >= 95%；
- 每个客户可见 defect 有可重放真实证据和 audit receipt；
- cleanup success = 100%；production writes = 0；
- 测量不完整时所有能力主张显式阻断。

达到该门槛可进行受控私有试点，但所有报告仍需人工 release approval。

### Autonomous GA gate

- held-out macro industry Recall >= 70%；
- Precision >= 80%；
- P0/P1 Recall >= 80%；
- 任一 held-out 行业 Recall 不低于 50%；
- clean target P0/P1 false positives = 0；
- reproduction rate >= 97%；
- execution success >= 95%；engine success >= 95%；
- evidence completeness、write audit coverage、cleanup success 均为 100%；
- 至少三个连续冻结数据集版本或三次独立 evaluation window 无回归；
- 无 secret 泄漏、生产写入、安全事故和 dirty environment。

只有达到该门槛，产品才能使用“全行业自主执行并发现 Bug”的 GA 级主张。

## 14. Grok 4.5 执行协议

Grok 4.5 必须按以下规则实施：

1. 先阅读根目录 `AGENTS.md`、本 Spec 和 `DISCOVERY_HARNESS_EVOLUTION_GOAL.md`。
2. 先完成 Phase 0，不得在度量不可信和 artifact 泄密的情况下继续宣称能力提升。
3. 每个 Phase 都要形成可独立审计的 vertical slice，不得一次性堆出未接入主链的大量新模块。
4. 新模块必须由 `run_v12_pipeline(...)` 或其统一后继主链真实调用；只有单元测试调用不算接入。
5. 不得读取隐藏真值内容来编写 prompt、detector、oracle、fixture 或 endpoint 规则。
6. 不得把 benchmark ID、endpoint、账号、业务名称写入可复用产品代码。
7. 不得降低 customer-delivery gate 或 evaluator match threshold 来提高指标。
8. 每次修改 Python 文件立即做 AST syntax check。
9. 每个 Gate 结束都运行完整测试、live target、外部 evaluator 和 clean target，并保留 receipt。
10. 如果外部 evaluator、target、模型或凭证不可用，状态必须是 `BLOCKED/NOT_MEASURED`，不得使用模拟结果补齐。
11. 不得覆盖或删除现有 baseline；新运行写入新的 content-addressed/immutable 目录。
12. 不得修改本 Spec 的验收阈值来适配实现结果。

## 15. 最终审计包

实施完成后必须提交一个不含 secret 的审计包，至少包含：

- 代码版本、分支、commit SHA、worktree 状态；
- changed-files manifest；
- Behavior IR / obligation / experiment schema version；
- policy、manifest、target、source、fixture fingerprints；
- 所有 syntax/test/lint/build 命令及退出码；
- champion/challenger replay/shadow report 路径与 hash；
- held-in、held-out、clean target 指标；
- baseline vs candidate 的 TP/FP/FN、Recall、Precision、F1、reproduction、duplicate、execution、cost 对比；
- production request、write audit、cleanup、dirty environment、安全事件统计；
- secret scan 结果；
- 所有未完成项、blocking reasons 和已知风险。

没有外部 evaluator receipt、clean target receipt 或真实执行证据时，最终状态只能写 `INCOMPLETE` 或 `NOT_MEASURED`，不得写“已完成能力突破”。
