# Spec: 统一主链发现能力 + 写探针沙箱 + 漏斗可观测（第一批）

> 执行者：grok-4.5 子代理。完成后由父代理（Fable）审核。
> 本 spec 自包含，包含所有必要的代码坐标、数据结构、验收标准与红线护栏。不要假设你读过之前的对话。

---

## 0. 背景与产品定位（必读）

QualiBug AI 是一个**全行业通用**的企业软件 AI 缺陷挖掘平台。客户导入多源资料（PRD、OpenAPI、DB schema、业务规则、历史 bug、测试账号），产品自动分析→执行探针→挖掘真实 bug→提供完整证据链→前端展示。

- 后端固定端口 **8088**，前端固定端口 **5174**。不要改端口。
- 正式后端入口：`ai_test_asset_center.private_pilot_entrypoint`（`backend/main.py` 只是兼容层，不要动）。
- 核心包：`ai_test_asset_center/`（约 367 个 py 文件）。
- 仓库根：`D:\QualiBug-AI\QualiBug-AI-main`。

### 已诊断的核心问题（本批要解决的）

产品"找不出更多 bug"的**根因是结构性的，不是某个参数太小**：

1. **三条发现管线并行，客户扫描只走最弱的一条。**
   - 管线 A（客户实际使用）：`__main__.scan()` → `v12_pipeline.run_v12_pipeline()` → BusinessStateGraph 行为切片 → 场景执行 → Oracle。
   - 管线 B（未接入）：`discovery_engine.AutonomousDiscoveryEngine.discover()` → `stage_reason_all_v2` 的 11 个 LLM 推理引擎 + 8 个本地分析器。**只有 `sweep_loop` / CLI 能触发，客户扫描根本不调用它。**
   - 管线 C（side job）：20+ PHASE 独立推理引擎（`business_invariant_mining`、`business_causality_conservation`、`metamorphic_differential_reasoning` 等），只挂在 `real_project_defect_discovery` 上。
   - **验证事实**：`v12_pipeline.py` 全文没有对 `stage_reason_all_v2`、`AutonomousDiscoveryEngine`、`build_analyzer_hypotheses` 的任何 import 或调用。

2. **写探针默认全跳过。** 高价值 bug（金额/库存守恒、状态机违规、Saga 补偿、幂等）依赖写操作或多步序列，但主链默认禁止未认证写操作，且缺少沙箱写入契约。

3. **漏斗不可观测。** 无法回答"这个项目为什么只找到 N 个 bug"——每一阶段（生成→入选→执行→验证→记账）损耗多少、Top 阻断原因是什么，都没有透出。

### 数据模型关键区别（务必理解，否则集成会错）

- **hypothesis（假设）**：分析器（`analyzers_adapter.build_analyzer_hypotheses`）和 LLM Reasoner（`stage_reason_all_v2`）的产出。是"推测某处可能有 bug"的文本+元数据记录，`status="unverified_hypothesis"`，**不能直接执行**。
- **behavior slice（行为切片）**：v12 主链消费的单位。是源绑定的可执行探测计划，带 `endpoints` 和 oracle。schema 见 §3.1。
- **本批的桥接思路**：把 hypothesis 转换成 source-grounded behavior slice（能绑定到真实 endpoint 的才保留），复用 `v12_pipeline.py` 现有的 supplementary slice 注入点，让新候选流经**同一套** 执行→oracle→证据→门控队列。**不要**另起一条执行路径，不要绕过门控。

---

## 1. 红线护栏（违反即判失败，审核会逐条检查）

1. **绝不硬编码行业/系统特化逻辑。** 全行业通用。不得出现 `/api/orders`、`ecommerce`、特定表名/字段名等针对某项目的硬编码。endpoint 目录必须来自 API 文档解析（`_api_facts`），actor 来自 `test_accounts.json` / `multi_service_config.json`。
   - 注：仓库现存 `v12_pipeline.py::_resolve_seed_bindings` L2032+ 有历史遗留的电商 endpoint 硬编码——**本批不要求你修，但你新增的代码绝不允许引入任何新硬编码**。
2. **绝不造假数据。** 不得生成 mock/synthetic/demo finding。没有真实执行找出的 bug 就不能标记为 bug。所有 finding 必须有真实探针回执支撑。
3. **Fail fast，错误不静默吞掉。** 不要用宽泛 `except: pass` 掩盖错误。新增代码的异常要么向上抛，要么记入可观测日志/证据。（现存代码里的 `except: pass` 不在本批修复范围，但你的新代码必须遵守。）
4. **保留关键配置地板**（`AGENTS.md` 强制）：
   - `discovery_engine.py` `__init__`：`timeout_seconds >= 300`，`max_tokens >= 32768`。
   - `stage_reason_all_v2.py`：`MAX_HYPOTHESES = 15`，`max_workers = 4`（默认并行 worker）。
   - **不得降低或删除这些值。** 本批不改这些常量。
5. **每次编辑 Python 文件后立即语法检查**（`AGENTS.md` 强制）：
   ```
   python -c "import ast; ast.parse(open('<file>', encoding='utf-8').read()); print('OK')"
   ```
   每改一个文件就跑一次，通过后才继续。
6. **加法优先，向后兼容。** 新能力默认不破坏现有扫描行为。新引擎/写探针通过开关控制；开关关闭时，扫描行为与现在完全一致。
7. **客户侧只看已验证 Bug。** "待确认发现"（needs_more_evidence，语义已确认但证据链不全）只透出到内部线索通道，绝不混入客户可见的"已验证 Bug"计数。
8. **端口**：后端 8088，前端 5174，不要改。

---

## 2. 交付目标（本批三件事）

### 任务 A：主链路统一 — 把分析器 + LLM Reasoner 候选并入 v12 扫描
### 任务 B：写探针沙箱 — 测试环境配置下允许写探针，带 cleanup 回滚与审计
### 任务 C：漏斗可观测 — 扫描输出各阶段数量 + Top 阻断原因，透出到 API/前端

三者都围绕 `v12_pipeline.run_v12_pipeline()` 这一主链函数展开。

---

## 3. 任务 A：主链路统一

### 3.1 behavior slice 数据结构（集成必须遵守）

v12 消费的 slice 是 dict，参考 `supplementary_behavior_slices.py` L196-214（permission slice 实例）。必备字段：

```python
{
    "slice_id": str,          # 用 business_state_graph.behavior_slice_id(kind, entity, ...) 生成，稳定去重
    "entity": str,            # 业务实体名（从 endpoint/规则解析，不硬编码）
    "kind": str,              # 切片类别，如 "invariant" / "permission" / "conservation" / "state_machine"
    "states": list,           # 状态列表，可为 []
    "endpoints": list[str],   # 必须是源绑定的真实路径（来自 API 文档）。为空则该 slice 不可执行，应丢弃
    "priority": float,        # 0..1
    "source_refs": list[dict],# [{"kind": ..., "quote": ...}] 溯源，必须非空（源绑定证据）
    "evidence_gaps": list,
    # oracle 绑定：命名 oracle 让 SemanticScenarioGenerator 生成对应场景
    "_<kind>_oracle": str,    # 如 "_invariant_oracle": "InvariantOracle"
    # 其余 oracle 特定的 _ 前缀字段
}
```

**关键约束**：`endpoints` 必须能绑定到 API 文档里真实存在的路径。无法绑定 endpoint 的 hypothesis 直接丢弃（记入漏斗的 `dropped_no_endpoint`，见任务 C），不要塞进 slice 队列——否则会产生不可执行的噪声场景。

### 3.2 新建桥接模块 `ai_test_asset_center/hypothesis_slice_bridge.py`

职责：把 hypothesis（分析器 / LLM Reasoner 产出）转换为 source-grounded behavior slice。

```python
def hypotheses_to_slices(
    hypotheses: list[dict],
    *,
    api_endpoints: list[dict],   # 来自 _api_facts(api_spec_text)，每项含 method/path/entity
    origin: str,                 # "analyzer" 或 "llm_reasoner"，用于溯源与漏斗归因
) -> tuple[list[dict], dict]:
    """
    返回 (slices, funnel_stats)。
    - 每个 hypothesis 尝试绑定到一个真实 endpoint（按 entity/关键词/路径匹配）。
    - 绑定成功 → 生成合规 behavior slice（schema 见 §3.1），slice_id 用 behavior_slice_id() 生成。
    - 绑定失败 → 不产出 slice，计入 funnel_stats["dropped_no_endpoint"]。
    - funnel_stats 至少包含: {"input": N, "bound": M, "dropped_no_endpoint": K, "by_origin": {...}}
    - source_refs 必须记录 hypothesis 的来源（engine 名 / rule / origin）。
    - oracle 选择：按 hypothesis 的 family/category 映射到已存在的 oracle 名称
      （参考 supplementary_behavior_slices 里用的 oracle：PermissionOracle / TenantIsolationOracle /
       ConcurrencyOracle，以及 oracle_engine 里已有的 InvariantOracle / StateOracle / IdempotencyOracle 等——
       先读 oracle_engine.py 确认可用 oracle 名称，只映射到真实存在的 oracle）。
    """
```

**实现前必读**：
- `ai_test_asset_center/business_state_graph.py` 里的 `_api_facts` 和 `behavior_slice_id` 函数签名（`supplementary_behavior_slices.py` L30 有 import 示例）。
- `ai_test_asset_center/oracle_engine.py`：确认实际存在哪些 oracle 类/名称，只能映射到真实存在的。
- `ai_test_asset_center/analyzers_adapter.py` L415 `build_analyzer_hypotheses(prd_text, api_spec, max_hypotheses_per_analyzer=15)` 的返回结构：`Dict[engine_name, List[hypothesis_dict]]`。看 `_convert_to_hypothesis`（L76 附近调用）了解 hypothesis 字段。
- `ai_test_asset_center/stage_reason_all_v2.py`：LLM Reasoner 产出的 hypothesis 字段（`status`、`family`、`engine`、`severity_potential`、`verification_method` 等）。

### 3.3 在 v12_pipeline 注入（集成点）

在 `ai_test_asset_center/v12_pipeline.py` 的 `run_v12_pipeline()` 中，**紧跟现有的 supplementary slice 注入块之后**（当前 L1521-1532，即 `generate_supplementary_slices` 之后、`if runtime_contract.get("status") == "approved":` 之前）注入新的分析器/Reasoner 切片：

```python
# ── 主链统一: 分析器 + LLM Reasoner 候选并入同一执行队列 ──
# 加法、可开关、源绑定；关闭时行为与现状一致。
try:
    from .hypothesis_slice_bridge import hypotheses_to_slices
    from .business_state_graph import _api_facts

    _endpoints = _api_facts(graph_api_doc)  # 复用已解析的 API 文档
    _unify_stats = {}

    # 8 个本地分析器（无需 LLM，默认开启）
    if os.environ.get("QUALIBUG_UNIFY_ANALYZERS", "1") == "1":
        from .analyzers_adapter import build_analyzer_hypotheses
        _ana = build_analyzer_hypotheses(prd_text, graph_api_doc)
        _ana_flat = [h for hs in _ana.values() for h in hs]
        _ana_slices, _ana_funnel = hypotheses_to_slices(_ana_flat, api_endpoints=_endpoints, origin="analyzer")
        ranked_behavior_slices = list(ranked_behavior_slices) + _ana_slices
        _unify_stats["analyzer"] = _ana_funnel

    # LLM Reasoner（需要 LLM provider，默认关闭，避免无 key 环境报错/成本）
    if os.environ.get("QUALIBUG_UNIFY_LLM_REASONER", "0") == "1":
        # 调用 stage_reason_all_v2 的引擎产出 hypotheses（读该模块确认正确的入口函数），
        # 转 slice 同上，origin="llm_reasoner"
        ...

    if _unify_stats:
        behavior_contract["slices"] = ranked_behavior_slices
        behavior_contract["summary"]["total_slices"] = len(ranked_behavior_slices)
        behavior_contract["summary"]["unified_slices"] = sum(f.get("bound", 0) for f in _unify_stats.values())
        result["mainline_unification"] = _unify_stats   # 供任务 C 漏斗使用
except Exception as exc:
    # Fail fast 原则：记入可观测字段，不静默吞
    result.setdefault("mainline_unification", {})["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
```

**约束**：
- `QUALIBUG_UNIFY_ANALYZERS` 默认 `"1"`（分析器不需要 LLM，可默认开）。
- `QUALIBUG_UNIFY_LLM_REASONER` 默认 `"0"`（需要 LLM provider，无 key 时不得报错、不得阻塞扫描）。
- 注入的 slice 走**完全相同**的下游：`_rank_behavior_slices_for_selection` → `_schedule_behavior_slices` → `SemanticScenarioGenerator` → 执行 → Oracle → 门控。不要新增旁路。
- `_schedule_behavior_slices` 每轮有 slice_budget 上限（默认 15）。这是刻意的成本控制，**不要改上限**；统一后的价值在于让高价值分析器/Reasoner 切片进入排序竞争，由 `_rank_behavior_slices_for_selection` 择优。

### 3.4 LLM Reasoner 接入细则

- 先读 `stage_reason_all_v2.py`，找到能返回 hypothesis 列表的入口（可能需要组合 `_run_reasoner_engine` L608 或已有的更高层函数）。若没有干净的单入口，在 `stage_reason_all_v2.py` 里新增一个薄封装函数 `collect_reasoner_hypotheses(prd_text, api_spec, ...) -> list[dict]`，内部复用现有引擎逻辑，**遵守 MAX_HYPOTHESES=15 / max_workers=4 地板**。
- LLM 不可用（无 key / 健康检查失败）时：函数返回 `[]`，并在 funnel 里标 `llm_reasoner: {"status": "provider_unavailable"}`。**遵守 `AGENTS.md`：未验证的 provider 视为 offline，显示为 unavailable 而不是假装成功。** 绝不因为无 LLM 而让扫描崩溃。

---

## 4. 任务 B：写探针沙箱

### 4.1 目标

在客户明确配置了"测试环境 + 测试账号"的前提下，允许对写操作端点（POST/PUT/PATCH/DELETE）执行探针，用于挖掘守恒/状态机/Saga/幂等类 bug。每个写探针必须：
1. 只在沙箱开关开启且 base_url 被批准时执行；
2. 执行前采集 before 快照（GET），执行后采集 after 快照（GET）；
3. 执行后做 cleanup（回滚/删除/置回），并记录 cleanup 状态；
4. 全程留审计记录（谁、什么时候、对哪个 endpoint、做了什么写操作）。

### 4.2 现状与约束

- 现有 `v12_pipeline.py` 场景执行支持 `execution_policy` 取值：`"safe_read_only"`、`"approved_test_write"`、`"approved_sandbox_write"`（见 L1677-1679）。说明写探针的框架**已部分存在**——你要做的是把它接通、加 cleanup 与审计，而非从零造。
- 先读 `grounded_probe_executor.py` 里的写探针安全门（README 提到 `QUALIBUG_ALLOW_TEST_WRITE` 概念、`match_production_data_exclusion` 生产数据排除）和 `_load_execution_safety_boundary`（`enterprise_project_config.py`）。
- 沙箱写入的开关与批准链路：读 `v12_pipeline.py::_execution_approval_contract` 和 `_runtime_contract`，理解 `runtime_contract.status == "approved"` 的条件。

### 4.3 实现要求

1. **开关**：新增环境开关 `QUALIBUG_ENABLE_SANDBOX_WRITE`（默认 `"0"`）。仅当：
   - `QUALIBUG_ENABLE_SANDBOX_WRITE == "1"` **且**
   - runtime_contract 批准（approved base_url）**且**
   - 项目配置显式声明该 base_url 是测试/沙箱环境（在 `multi_service_config.json` 或 `real_project_config.json` 里有 `environment: test|sandbox|staging` 标记，读现有配置结构确认字段名，不要臆造）**且**
   - 有可用测试账号 token
   四者同时满足，写探针场景才允许执行（`execution_policy = "approved_sandbox_write"`）。任一不满足 → 写探针保持 skip，行为同现状。

2. **生产数据保护**：复用 `_load_execution_safety_boundary` / `match_production_data_exclusion`。命中生产数据排除规则的请求绝不发送，计入 `production_data_blocked`（现有字段，L1828）。

3. **before/after 快照 + cleanup**：写探针执行序列应为 `GET(before) → WRITE → GET(after) → cleanup`。cleanup 策略按 HTTP 方法：
   - POST（创建）→ 记录返回的资源 id，尝试 DELETE 回滚；
   - PUT/PATCH（更新）→ 用 before 快照数据写回；
   - DELETE → 若不可逆则标记 `cleanup.status = "not_reversible"` 并在审计中告警（不要伪造 completed）。
   - cleanup 结果写入 finding 的 `evidence.cleanup = {"status": "completed"|"verified"|"failed"|"not_reversible", "receipt_ref": ...}`。这直接影响 §5 的 Business Evidence Gate（写操作需 cleanup ∈ {completed, verified}）。
   - **Fail fast**：cleanup 失败必须如实标 `failed`，不得吞掉、不得谎报 completed。

4. **审计记录**：每个写探针执行落一条审计到 `platform_workspace/{project}/defect_discovery/sandbox_write_audit.jsonl`（每行一个 JSON：timestamp、actor_role、method、path、before_ref、after_ref、cleanup_status、campaign_id、slice_id）。目录若不存在则创建。

5. **加法兼容**：开关关闭时，`_execute_scenario` 与执行阶段行为与现状**逐字节一致**。

### 4.4 落点

- 写探针执行逻辑：`v12_pipeline.py` 执行阶段（L1711 起的 `else:` 块，即 executable 场景循环 L1739-1820）。before/after/cleanup 可抽成 `v12_pipeline.py` 内的辅助函数或新模块 `ai_test_asset_center/sandbox_write_executor.py`（推荐新模块，保持 v12_pipeline 不再膨胀）。
- 若新建模块，`run_v12_pipeline` 里在写探针场景上调用它。

---

## 5. 任务 C：漏斗可观测

### 5.1 目标

每次扫描输出五阶段漏斗 + Top 阻断原因，回答"为什么只找到 N 个 bug"。

五阶段（对齐 `.trae/specs/raise-validated-bug-discovery-rate/spec.md` 的口径）：
```
candidate_generation → probe_selection → execution → verification → formal_accounting
```

### 5.2 数据来源（已存在，需聚合）

- 候选生成：state graph slices + supplementary slices + §3 统一注入的 slices（`result["mainline_unification"]`）。
- 入选：`selection["selected_slice_ids"]` vs 总 slice 数。
- 执行：`result["phases"]["execution"]`（executed / failed / planned_only / production_data_blocked）。
- 验证：`result["phases"]["oracle"]`（total_evaluated / violations_found）。
- 记账：门控结果。**门控的缺失原因**是关键——读 `discovery_finding_gate.py` 的 `BusinessEvidenceGate.check()`（L126-154），它返回 `missing` 列表（如 `BEFORE_SNAPSHOT_MISSING`、`CLEANUP_PENDING`、`SEMANTIC_VERDICT_NOT_CONFIRMED`、`SOURCE_GROUNDING_MISSING` 等）。聚合每个缺失原因的出现次数 → Top 阻断原因。

### 5.3 实现要求

1. 新增 `ai_test_asset_center/discovery_funnel.py`：
   ```python
   def build_funnel(v12_result: dict, gate_results: list[dict] | None = None) -> dict:
       """
       返回:
       {
         "stages": [
           {"name": "candidate_generation", "input": N, "output": M, "conversion": M/N, "dropped": N-M},
           ... 五阶段 ...
         ],
         "top_blocking_reasons": [{"reason": "CLEANUP_PENDING", "count": 7}, ...],  # 降序
         "validated_bug_count": int,        # 客户可见的已验证 Bug
         "pending_finding_count": int,      # 待确认发现（内部线索，缺证据）
         "candidate_count": int,            # 候选信号
         "explanation": str,                # 低产出时的人话解释 + 下一步建议
       }
       """
   ```
   - `explanation`：当 validated_bug_count 低或为 0 时，输出可操作解释，例如"12 条语义已确认发现因缺少 before/after 快照未计入正式 Bug；建议开启沙箱写探针（QUALIBUG_ENABLE_SANDBOX_WRITE）以补齐写操作证据链"。**必须基于真实漏斗数据生成，不得套模板空话。**
2. 在 `run_v12_pipeline` 末尾（return 前）调用 `build_funnel`，写入 `result["discovery_funnel"]`。
3. 三层计数严格分离（红线 7）：`validated_bug_count` 只含通过完整门控 + 客户可交付的 finding；`pending_finding_count`（needs_more_evidence）单列，**不得**混入 validated。

### 5.4 透出到 API / 前端

- **后端**：确认扫描结果 envelope（`__main__.scan()` 返回、`private_pilot_service.py` 的 `/api/v1/scan` handler、以及 `/api/v1/projects/{id}/command-center` 聚合接口）把 `discovery_funnel` 带出去。读 `private_pilot_service.py` 找到 command-center 组装处，加入 funnel 字段。
- **前端**：在 `frontend/src/pages/Dashboard.tsx` 加一个"发现漏斗"卡片，展示五阶段转化 + Top 阻断原因 + explanation。
  - 前端技术栈是 **Vite + React 19 SPA**（不是 Next.js，忽略 README/AGENTS 里的 Next.js 描述）。
  - 数据来自 `command-center` 接口（见 `frontend/src/api/client.ts` 的 `command-center` 调用、`frontend/src/api/data.ts`）。
  - **零 mock**：没有 funnel 数据时显示空状态，不要填假数字。
  - "待确认发现"计数可以展示（帮助理解漏斗），但要明确标注它**不是**已验证 Bug；已验证 Bug 计数只用 `validated_bug_count`。

---

## 6. 实现顺序（建议）

1. 读代码确认接口：`business_state_graph._api_facts` / `behavior_slice_id`、`oracle_engine` 可用 oracle、`analyzers_adapter.build_analyzer_hypotheses` 返回结构、`stage_reason_all_v2` 引擎入口、`discovery_finding_gate.BusinessEvidenceGate`、`_load_execution_safety_boundary`、runtime_contract 批准链。
2. 任务 C 的 `discovery_funnel.py`（纯函数，最容易独立测）。
3. 任务 A 的 `hypothesis_slice_bridge.py` + v12 注入。
4. 任务 B 的沙箱写探针。
5. 前端 Dashboard 漏斗卡片。
6. 每个 py 文件改完立即 `ast.parse` 语法检查。

---

## 7. 验收标准（父代理会逐条核对）

### 功能
- [ ] `hypothesis_slice_bridge.hypotheses_to_slices` 存在，能把分析器/Reasoner hypothesis 转成合规 behavior slice（schema §3.1），无法绑定 endpoint 的被丢弃并计入漏斗。
- [ ] `run_v12_pipeline` 在 supplementary slice 注入点之后接入统一切片，`result["mainline_unification"]` 有分阶段统计。默认开分析器、关 LLM Reasoner。
- [ ] `QUALIBUG_UNIFY_ANALYZERS=0` 时，扫描行为与改动前一致（回归）。
- [ ] 沙箱写探针：仅四条件同时满足才执行；有 before/after 快照 + cleanup + 审计 jsonl；开关关闭时执行阶段行为不变。
- [ ] cleanup 失败如实标记，绝不谎报 completed。
- [ ] `discovery_funnel.build_funnel` 输出五阶段 + Top 阻断原因 + 三层计数 + 真实 explanation；写入 `result["discovery_funnel"]`。
- [ ] funnel 透出到 command-center 接口；Dashboard 有漏斗卡片，无数据时空状态、零 mock。
- [ ] 客户可见"已验证 Bug"只用 validated_bug_count，待确认发现单列。

### 红线（§1 逐条）
- [ ] 无任何新增硬编码行业/endpoint/表名/字段。
- [ ] 无任何 mock/假 finding。
- [ ] 新代码无静默吞错（无裸 `except: pass`）。
- [ ] 未改 MAX_HYPOTHESES / max_workers / timeout_seconds / max_tokens 地板。
- [ ] 每个改动的 py 文件通过 `ast.parse` 语法检查。
- [ ] 端口未改（8088 / 5174）。

### 测试
- [ ] 为 `hypothesis_slice_bridge` 和 `discovery_funnel` 各写至少一个 pytest（放 `tests/`，命名 `test_mainline_unification_*.py` / `test_discovery_funnel_*.py`），用真实数据结构、不造假。
- [ ] 跑 `python -m pytest tests/ -q` 确认没有回归失败（若有既有失败与本改动无关，需在报告里说明）。
- [ ] 可行的话跑一次 `projects/benchmark_mall` 的扫描，报告优化前后漏斗数字对比（生成→入选→执行→验证→记账各阶段）。若环境无法起被测系统，如实说明并给出可复现命令。

---

## 8. 交付报告要求（子代理完成后必须输出）

用中文输出，包含：
1. 每个新建/修改文件的路径 + 一句话说明。
2. 三个开关的默认值与触发条件。
3. `ast.parse` 语法检查结果（每个文件）。
4. pytest 结果（贴关键输出）。
5. benchmark_mall 漏斗对比数字（或无法运行的诚实说明）。
6. 明确列出：哪些验收项已满足、哪些未满足及原因。**不得谎报完成。** 做不到的诚实说，不要糊纸。
