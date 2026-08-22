# Project G 前置:Binding 闭环 + 空间探索层接入发现主线 SPEC

> 文档状态:待实施
> 面向执行者:code agent
> 上位约束:`AGENTS.md`、`docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md`、`docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md`、项目全景文档(QualiBug 项目全景进展与商业化核心能力说明)
> 产品端口:前端 `5174`,后端 `8088`(不得改动)
> 核心原则:全行业中立、来源驱动、真实执行、失败显式(fail-closed)、隐藏真值隔离、禁止硬编码

## 0. 本 SPEC 在项目路线中的位置

P0 结果完整性审计已完成(commit `7d433f2`:LEVEL_B → LEVEL_A,19 个实现级唯一根因,`project_g_entry_allowed: true`)。按全景文档 §12.1,下一步是"冻结 Project G 候选版本 → 建立全新陌生系统 → 严格盲测"。

**本 SPEC 是"冻结 Project G 候选版本"的前置必要工作**:Project F 阶段的真实 MES 执行走的是根目录 sidecar 脚本(`generate_runtime_phase*.py`),Binding 闭环与空间探索能力尚未进入产品 `scan()` 主线。若不接线就冻结 G 候选版本,盲测要么用不上新能力,要么被迫继续走 sidecar——后者违反"禁止第二套平行系统"红线。正确顺序:

```text
主线接线(本 SPEC)→ 全量回归 → 冻结 G 候选版本(之后代码修改 = 0)→ Project G 盲测
```

### 成绩口径纪律(执行者必须遵守,违反即失效)

- Project F 盲测成绩 = Recall 3.1% / 1 TP,**永久封存**,任何新结果不得覆盖或混淆该口径。
- 揭示后运行期审计成绩(LEVEL_A、Unique TP 19、召回 0.594)是 Post-Reveal 口径,**不是盲测成绩**,不得用于对外宣称。
- 本 SPEC 的验收只产生 Infrastructure / Regression PASS,不产生任何新的能力成绩宣称;真实能力等 Project G 盲测检验。
- Root Cause 必须是实现级(Invariant + Operation + Missing Control + 修复点),不能只是机制名称;禁止为凑阈值人为拆分。

---

## 1. 背景与现状

Project F(离散制造 MES 盲测)暴露的主断点是 `BLOCKED_MISSING_BINDING`(约 90% 实验绑不上真实实体/字段/fixture)。为此已实现两套能力层并通过独立审计(LEVEL_A、Project G 入口门禁放行):

- **Binding 闭环层**(commit `f6c741f`):`binding_ledger` / `binding_evidence` / `binding_builder` / `binding_conflict_resolver` / `binding_completeness_gate` / `binding_runtime_probe` / `field_level_golden_rules`
- **空间探索层**(同批):`space_dimension_registry` / `space_coordinate` / `invariant_graph` / `exploration_operator_registry` / `combination_generator` / `coverage_guided_scheduler` / `experiment_portfolio` / `multi_surface_adapter` / `multi_layer_observation` / `cross_surface_oracle`

**当前问题:这批模块只被根目录的 deliverable/测试脚本引用(`generate_space_deliverables.py`、`test_binding_*.py`、`test_space_exploration_*.py` 等),没有进入产品扫描热路径。** 产品热路径是:

```text
POST /api/v1/scan → __main__.scan() → run_v12_pipeline → discovery_mainline
  → discovery_runtime_planning.build_discovery_plan   (规划)
  → discovery_runtime_execution.run_experiment_candidate
      → experiment_batch_executor.execute_selected_experiments   (执行)
      → contract_oracles.evaluate_contract_oracle → customer_delivery_gate_v2
```

**本 SPEC 的目标:把两套能力层按 Project A–F 一贯的"原位增强"方式接入上述主线。`deep_experiment_planner` 已退出产品主链，仅保留为诊断研究面；它不得用启发式 actor、请求体、断言或补偿关系替换编译期已阻断的实验。**

---

## 2. 目标与非目标

### 目标

1. 每次产品 `scan()` 的规划阶段自动构建绑定账本,绑定不完备的实验在**编译期**就显式 `BLOCKED_MISSING_BINDING`(带细分断点码),而不是运行期才失败。
2. 每个 obligation/experiment 携带系统空间坐标;实验选择受覆盖引导调度器影响(在既有预算合约内)。
3. 执行阶段挂接多层观测与跨表面一致性证据,喂给既有 contract oracle,不另立发现权威。
4. 保持 Project A/C/D/E/F 全部回归门禁通过,外部质量口径保持 `NOT_MEASURED` 纪律。

### 非目标(明确禁止)

- ❌ 不新建旁路扫描链、不新建第二执行权威(执行仍只经 `execute_selected_experiments`)。
- ❌ 不替换 `run_v12_pipeline` / `discovery_mainline` / handler 方法(必须用首类函数调用或已有 register hook)。
- ❌ 不把 `cross_surface_oracle` 的 emergent violation 直接变成客户可交付缺陷(见 §5.3)。
- ❌ 不修改评测器、不触碰 `_private_eval/_evaluator_private/`、不把 GT 相关内容带入任何运行时代码或 prompt。
- ❌ 不提高/绕过 `qualibug.adaptive-planning-budget.v1` 预算(不得静默扩大 slice 预算)。
- ❌ 不新增任何行业词汇、客户业务规则、固定 endpoint 路径的硬编码。

---

## 3. 硬约束(违反任意一条即验收失败)

1. `import ai_test_asset_center` 必须保持无副作用;新接线全部是运行期函数调用。
2. 所有失败路径 fail-closed 且显式:绑定缺失 → `BLOCKED_MISSING_BINDING`;观测不完备 → `INDETERMINATE`,绝不静默降级或吞错。
3. 零义务 / 全 BLOCKED 的运行保持可见 `BLOCKED`,不得被解释为"无缺陷"。
4. 每次编辑 Python 文件后立即运行语法检查:
   `python -c "import ast; ast.parse(open('path/to/file.py', encoding='utf-8').read()); print('OK')"`
5. 关键配置地板不得移动:`discovery_engine.py` `timeout_seconds >= 300`、`max_tokens >= 32768`;`stage_reason_all_v2.py` `MAX_HYPOTHESES = 64`、`max_workers = 4`。
6. 运行时绑定解析必须遵守既有合约:占位符只能通过源声明的 `GET`/`HEAD` 解析器 + control actor 物化,发 fingerprint-only binding receipt;禁止发明标识符、禁止隐藏种子读。
7. 写探针只对显式声明的非生产目标;一切写经 `target_policy` + 治理沙箱(既有路径,不得绕过)。
8. **发现能力开放性**:Bug 类型(API/UI/性能/权限/守恒/并发/……)只是事后归类标签,发现由不变量 + 系统空间坐标变化驱动。接线实现不得引入按 Bug 类型白名单过滤实验或 finding 的逻辑;算子适用性、组合过滤、配额调度只能基于绑定完备性、来源证据、覆盖缺口与预算,不能基于"预期 Bug 类型"筛选。机制配额(如权限实验占比 ≤30%)是防止单类垄断的下限保护,不是类型上限。

---

## 4. 实施分期

### Phase 1 — Binding 闭环接入规划期(优先级最高)

**挂点:`ai_test_asset_center/discovery_runtime_planning.py`**

在 `compile_experiments(...)` 之前构建绑定账本；在实验编译后直接对每个 obligation 跑完备性门禁。阻断结果保持阻断，不再通过 deep planner 合并路径改写。

```python
# (a) behavior_ir 构建完成后:
from .binding_ledger import BindingLedger
from .binding_builder import build_all_bindings
from .binding_conflict_resolver import detect_and_resolve_all

_binding_ledger = BindingLedger()
_binding_build_receipt = build_all_bindings(behavior_ir, _binding_ledger)
_binding_conflict_receipt = detect_and_resolve_all(_binding_ledger, strategy="evidence_priority")

# (b) 实验编译后,对每个 obligation:
from .binding_completeness_gate import gate_or_block
_gate = gate_or_block(_binding_ledger, obligation=obl, behavior_ir=behavior_ir)
# _gate 判定不完备 → 该 obligation 对应实验标记
#   compile_receipt.status = "BLOCKED",reason = "BLOCKED_MISSING_BINDING",
#   并附 _gate 返回的细分断点码(FIELD_NOT_BOUND / ENTITY_NOT_BOUND /
#   FIXTURE_NOT_BOUND / RELATION_NOT_BOUND 等)与缺失绑定清单。
```

要求:

1. 绑定账本快照(计数、状态分布、冲突处理结果)作为 receipt 放入 `DiscoveryPlanningBundle.experiments` 的新键 `binding_closure_receipt`,进入 trace ledger,可被 `obligation_attempt_ledger` 归因。
2. `binding_runtime_probe.run_probes_for_ledger(...)` 只允许在既有的受治理**只读**规划回合(`planning_round=0` 运行时接口发现所在阶段,见 `discovery_runtime_execution`)之后调用,`base_url` 必须取自 `_runtime_contract.approved_base_url`,禁止另开 HTTP 通道;`max_probes` 默认 50 不得上调。若 runtime contract 未 approved,跳过探测并在 receipt 中显式记 `PROBES_SKIPPED_CONTRACT_NOT_APPROVED`。
3. 已经能 COMPILED 的实验不得因新门禁被降级为 BLOCKED,除非门禁发现了**真实缺失**(即执行必然失败的绑定缺口)——门禁是"提前显式化失败",不是"新增失败"。实施后用 Project A 回归验证 COMPILED 数不下降(见 §6)。

### Phase 2 — 空间坐标 + 覆盖引导调度接入规划期

**挂点:`discovery_runtime_planning.py`,`plan_obligation_round(...)`(约第 670 行)前后**

```python
# (a) 坐标标注:对每个 obligation
from .space_coordinate import coordinate_from_obligation, validate_coordinate
obl["space_coordinate"] = coordinate_from_obligation(obl, behavior_ir)

# (b) 不变式图 + 算子适用性(每次 run 从 IR 构建,不做全局缓存):
from .invariant_graph import build_default_invariant_graph
from .exploration_operator_registry import ExplorationOperatorRegistry, check_all_applicability
_inv_graph = build_default_invariant_graph(behavior_ir, project_id=<campaign 的 project_id>)
_op_registry = ExplorationOperatorRegistry(); _op_registry.register_defaults()
_applicability = check_all_applicability(_op_registry, behavior_ir=behavior_ir)

# (c) 组合生成(受约束,禁止笛卡尔穷举):
from .combination_generator import generate_combinations
_combos = generate_combinations(<applicable operators>, max_level=3,
                                max_combinations=100, behavior_ir=behavior_ir)

# (d) 覆盖引导重排:在 plan_obligation_round 产出 selected 之后,
#     用 coverage_guided_scheduler 对"同优先级"的 selected obligations 做覆盖缺口重排。
```

要求:

1. **预算合约不变**:调度器只能在 `plan_obligation_round` 已选中的集合内重排/标注,不得增选、不得扩预算。`budget_receipt` 语义不变。
2. `experiment_portfolio`:在 `run_experiment_candidate` 把执行批交给 `execute_selected_experiments` **之前**,将最终选中实验冻结进 `ExperimentPortfolio`(`add_experiment` → `freeze` → `begin_execution`),`validate_portfolio_quotas` 违规时显式记录并按既有 BLOCKED 语义处理;portfolio `export()` 作为 receipt 入 bundle(新键 `space_exploration_receipt`)。
3. 若给 `plan_obligation_round` / `adaptive_discovery_planner` 增加重排能力,必须以**首类可选参数或 register hook**(参照 `v12_legacy_schedule.register_slice_reorder_hook` 的模式)实现,默认行为不变。

### Phase 3 — 多层观测 + 跨表面证据接入执行期

**挂点:`ai_test_asset_center/experiment_executor.py`(`execute_one_experiment`)与 `experiment_batch_executor.execute_selected_experiments`(第 161 行起)**

1. `multi_layer_observation`:作为**已有 typed observer 体系的扩展**接入(与 `before_state`/`after_state`/`final_state` 同一模式)。观测器只在**源声明的对应读取面存在**时编译(源里没有声明的观测面 → 不编译,不是 INDETERMINATE);产出 fingerprinted 观测 receipt,经 `check_observation_completeness` 判定,缺层 → 该维度 `INDETERMINATE`,不影响其他断言。
2. `multi_surface_adapter.plan_cross_surface_execution`:只做执行计划投影(主表面 API + 观测表面),不得引入未治理的传输通道;UI 面沿用既有 governed UI adapter,DB 面沿用既有 DB 观测路径。
3. `cross_surface_oracle`:
   - `detect_emergent_violation` 的输出定位为**证据增强 + 内部候选线索**,写入实验的 observation/evidence 收据,供 `evaluate_contract_oracle` 既有断言消费;
   - **禁止**由它直接产生 formal finding。没有 contract oracle 血统(activation + assertion + receipt 链)的 emergent violation 只能进内部线索(Internal Clues),不进 `customer_delivery_gate_v2`。
4. `correlate_observations` 的关联键必须来自实验携带的真实实体标识(fixture/binding receipt 中的值指纹),禁止启发式猜测关联。

### Phase 4 — 回归、清理与文档

1. 跑全量验收(§6)。
2. 把根目录一次性脚本(`generate_*_deliverables.py`、`_project_f_*.py`、`test_binding_*.py`、`test_space_exploration_*.py`)中仍有长期价值的测试**迁入 `tests/`** 并接入 pytest;一次性 deliverable 脚本保持原位不动(不删除)。
3. 更新 `AGENTS.md`:在架构契约部分补一段,声明 Binding 闭环与空间探索层的接入点、receipt 键名、以及"cross_surface_oracle 不是发现权威"的边界。
4. 本文件底部追加"实施记录"小节:列出每个 Phase 改动的文件、新增 receipt 键、回归结果。

---

## 5. 接口与数据契约细则

### 5.1 新 receipt 键(进入 DiscoveryPlanningBundle / trace ledger)

| 键 | 阶段 | 内容 |
|---|---|---|
| `binding_closure_receipt` | 规划 | 账本快照:边计数、状态分布(BOUND/UNBOUND/CONFLICT_RESOLVED)、冲突处理、probe 结果或跳过原因 |
| `space_exploration_receipt` | 规划 | 维度覆盖摘要、算子适用性计数、组合数、portfolio export、配额校验结果 |
| 实验级 `space_coordinate` | 规划 | `coordinate_from_obligation` 输出,随实验进入执行与 attempt ledger |
| 观测级 multi-layer receipts | 执行 | 各层观测指纹 + completeness 判定,并入既有 observation receipts |

所有 receipt 落盘前必须过 `artifact_redactor.py`(沿用既有持久化路径,不自建写盘逻辑)。

### 5.2 BLOCKED 细分码(沿用 Project F 已定义词表,不得新造同义词)

`FIELD_NOT_BOUND` / `ENTITY_NOT_BOUND` / `FIXTURE_NOT_BOUND` / `RELATION_NOT_BOUND` / `ACTOR_NOT_BOUND` / `STATE_NOT_REACHABLE`。顶层 reason 统一 `BLOCKED_MISSING_BINDING`,细分码放 receipt 明细。

### 5.3 Oracle 权威边界(再次强调)

`experiment_candidate` 路径上的客户交付权威是 `contract_oracles.evaluate_contract_oracle` + `customer_delivery_gate_v2`。本次接入的所有新观测/oracle 只能:(a) 作为证据 receipt 喂给 contract oracle;(b) 产生内部候选线索。不允许出现第三条 finding 产生路径。

---

## 6. 验收门禁(全部满足才算完成)

1. **语法与导入**:所有改动文件 `ast.parse` 通过;`python -c "import ai_test_asset_center"` 无副作用、无报错。
2. **既有测试**:`pytest tests/` 全绿(与改动前基线对比,不得新增失败)。
3. **能力层测试**:根目录 `test_binding_unit.py`、`test_binding_integration.py`、`test_binding_integration_chains.py`、`test_space_exploration_unit.py`、`test_space_exploration_integration.py` 全部通过(迁入 `tests/` 后在新位置通过)。
4. **主线接线证据**:`discovery_runtime_planning.py` / `discovery_runtime_execution.py` / `experiment_executor.py`(或 batch executor)中出现对新模块的真实 import 与调用;grep 可验证。
5. **端到端冒烟**:对 `projects/mes_f`(启动其 mock SUT,端口 8020)跑一次产品 `scan()`,产出的 plan/trace 中存在 `binding_closure_receipt`、`space_exploration_receipt`、实验级 `space_coordinate`;BLOCKED 实验带细分断点码。
6. **回归门禁**:复跑 Project A/C/D/E/F 回归(参照 `_project_f_regression_gate.py` 的口径),全部保持 PASS;Project A 的 COMPILED 实验数与 formal findings 数不回退。
7. **纪律审计**:无新增行业硬编码(复用 `_project_f_anti_hardcoding_audit.py` 口径自查);无 GT 路径引用;预算合约未被扩大;5174/8088 端口未动。
8. **提交**:一个干净 commit,message 说明各 Phase;不得把 `_private_eval/` 或运行产物垃圾带入提交。

## 7. 建议实施顺序与工作量

Phase 1(绑定闭环)→ 验收 5/6 → Phase 2(空间调度)→ Phase 3(观测/oracle)→ Phase 4(清理文档)。每个 Phase 独立可验收、可回退;不要跨 Phase 混改。预计改动集中在 4 个主线文件 + 新模块微调,总量约 300–600 行接线代码。

---

## 实施记录 (2026-07-23)

### Phase 1 — Binding 闭环接入规划期

- **文件**: `ai_test_asset_center/discovery_runtime_planning.py`
- **改动**:
  - 在 `build_discovery_plan()` 中 Behavior IR 校验后构建 `BindingLedger`，调用 `build_all_bindings` + `detect_and_resolve_all`
  - 在实验编译后运行 `binding_completeness_gate.gate_or_block`，非 COMPILED 实验失败则标记 `BLOCKED_MISSING_BINDING` + 细分码
  - 产出 `binding_closure_receipt`（schema `qualibug.binding-closure-receipt.v1`）
  - Runtime probe 显式记录 `PROBES_SKIPPED_CONTRACT_NOT_APPROVED`

### Phase 2 — 空间坐标 + 覆盖引导调度接入

- **文件**: `ai_test_asset_center/discovery_runtime_planning.py`
- **改动**:
  - 对每个 obligation 标注 `space_coordinate`（via `coordinate_from_obligation`）
  - 构建 `InvariantGraph`、`ExplorationOperatorRegistry`、适用性检查、组合生成
  - 已选实验集合内运行 `CoverageGuidedScheduler` 重排（不增选、不扩预算）
  - 产出 `space_exploration_receipt`（schema `qualibug.space-exploration-receipt.v1`）

### Phase 3 — 多层观测 + 跨表面证据接入执行期

- **文件**: `ai_test_asset_center/experiment_executor.py`
- **改动**:
  - 在 typed observer 完成后调用 `check_observation_completeness`，产出 `qualibug.multi-layer-observation.v1`
  - 调用 `detect_emergent_violation`，产出 evidence-only `cross_surface_evidence`
  - 明确边界：cross_surface_oracle 不是发现权威，不产出 formal findings

### Phase 4 — 回归、清理与文档

- 5 个能力层测试文件从根目录迁入 `tests/`，128 个测试全部通过
- AGENTS.md 架构契约段落已补充（Binding closure + Space exploration + Multi-layer observation）
- 本实施记录已追加

### 验收状态

| 门禁项 | 状态 |
|--------|------|
| 语法与导入 | ✅ ast.parse + import 无副作用 |
| 既有测试 pytest tests/ | ✅ 全绿 |
| 能力层测试 | ✅ 128 passed |
| 主线接线证据 | ✅ grep 可验证 import + 调用 |
| 纪律审计 | ✅ 无硬编码、无 GT 引用、预算未扩大 |
