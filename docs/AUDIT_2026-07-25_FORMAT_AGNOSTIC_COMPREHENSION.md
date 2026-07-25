# 审计报告：格式无关企业资料认知能力（SPEC_FORMAT_AGNOSTIC_ENTERPRISE_MATERIAL_COMPREHENSION）

- 审计日期：2026-07-25
- 被审计范围：`42b3ec1 .. 441e861`（6 提交）
- 基线：`9487f8f`
- 审计结论：**未通过（NOT ACCEPTED）**。Phase 0 与 Phase 2 部分成立；Phase 3 / Phase 4 从未运行成功；SPEC §4.3 核心硬门禁未达成。
- **返工状态：已完成（见本文件 §6）。B1–B5 全部关闭，SPEC §4.3 硬门禁达成。**

---

## 1. 精确对照（基线 vs HEAD，独立复算）

方法：在 `9487f8f` 建立独立 git worktree，用同一脚本对两侧分别执行
`build_enterprise_business_knowledge_asset` → `build_behavior_ir_from_knowledge_asset`。

| 项目 | data_tables | data_fields | permission_matrix | **IR entities** | 判定 |
|---|---|---|---|---|---|
| benchmark_mall | 0 → 0 | 0 → 0 | 1 → 1 | **0 → 0** | **FAIL：硬门禁 A 要求 0→>0** |
| contractflow_c | 27 → 27 | 27 → 27 | 4 → 4 | 22 → 22 | PASS（无回归） |
| ticketsla_d | 8 → 8 | 8 → 8 | 1 → 1 | 8 → 8 | PASS（无回归） |
| warehouse_e | 12 → 23 | 12 → 23 | 0 → 0 | **11 → 11** | **FAIL：增量为重复+噪声，IR 零增益** |
| mes_f | 0 → 17 | 0 → 17 | **0 → 0** | **0 → 17** | 实体 PASS；权限 FAIL |

SPEC §4.3 要求「A 与 F 从 0 变为 >0，C/D/E 不回归」。**A 未达成**，因此该门禁整体不通过。

---

## 2. 阻断项

### B1｜benchmark_mall 实体仍为 0（SPEC §4.3 硬门禁）

benchmark_mall 是 131-bug 主评测靶标，全部 8 个源仍产出 0 表 0 字段。
根因是其 `DB_SCHEMA.md` 的文档形状与新抽取器实现的唯一形状不匹配：

```markdown
| 表 | 说明 |
|---|---|
| users | 用户与角色 |
| orders | 订单主表 |
...（13 张表）

关键字段：
- `orders.status`：订单状态；
- `inventory.available_qty`：可售库存；
```

存在两种未覆盖的常见形状：
1. **实体清单表**：表头为 `表 / 说明`（表名列 + 描述列），而非 `字段 / 类型 / 说明`。抽取器要求"名称类 + 类型或说明类"表头，`表` 不在名称类词表内，直接判为非结构化。
2. **限定字段列表**：`` - `orders.status`：订单状态；`` 这种 `table.field` 内联声明，完全未处理。

### B2｜Phase 3 从未运行成功一次（虚假交付）

`_semantic_extraction.py:131` 引用了不存在的符号：

```python
from ..llm_reasoning import get_reasoning_client   # 该函数不存在
client = get_reasoning_client()
```

`llm_reasoning` 实际导出的是 `_get_client` / `reason` / `reason_layered` / `ReasoningClient`。
实测：3 个项目 18/18 个源全部抛出
`LLM client unavailable: cannot import name 'get_reasoning_client'`，
`semantic_candidates` 恒为 `0`。

该 Phase 被作为"已交付"上报，实际从未产出过一个候选。

### B3｜Phase 4 从构造上不可能被验证

候选校验与晋级状态机的唯一输入是 Phase 3 的候选。B2 导致候选恒为空，
因此 Phase 4 的校验、去重、晋级、拒绝路径**一条都没有被执行过**。
其"实现完成"不具备任何运行证据。

### B4｜warehouse_e 的 12→23 是重复别名与噪声（数据污染）

新增的 11 行全部是既有实体的第二份别名，另有 1 行是小节标题被误当作表：

```
Product (产品)      ← 原有
Product             ← 新增，同一实体的第二身份
Warehouse (仓库) / Warehouse
InventoryBatch (库存批次) / InventoryBatch
... ×11 对
关键业务约束        ← 小节标题被当作表名
```

两个后果：
- **能力零增益**：IR 实体仍为 11，抽取计数 +92% 是虚假指标。若以此对外表述能力提升即为假数据（违反 AGENTS.md 第 7 条）。
- **身份分裂被静默吞掉**：IR 构建对重复实体别名直接保留首次出现、丢弃后者，未发出任何 conflict receipt。AGENTS.md 明确要求「重复源别名必须保留为 source reference 并发出显式冲突回执，不得静默互相覆盖」——当前实体侧违反了该契约。

### B5｜mes_f 权限矩阵仍为 0

`USER_ROLES.md` 与 `real_project_config.json` 两个源均已正确分类为 `permission_matrix`，
但产出仍为 0 条。SPEC §4.3 要求该项 0→>0，未达成。
（可见性正常：`permission_space_empty` 缺口已如实发出。）

---

## 3. 成立项（应予保留）

### P1｜Phase 0 可见性：真实生效，且正是它暴露了 B1

```
benchmark_mall  gaps=11  entity_space_empty ×1 / field_space_empty ×1
                         structured_extraction_empty ×1 / semantic_extraction_error ×7
mes_f           gaps=17  permission_space_empty ×1 / structured_extraction_empty ×5
extraction_outcome 语义拆分生效：{EMPTY_NO_STRUCTURE_FOUND, EXTRACTED}
```

`fidelity: full` 与零产出并存的旧假象已消除；LLM 失败也如实转成
`semantic_extraction_error` 缺口而非静默吞掉——**Fail Loud 原则被正确落实**。

### P2｜回归门禁通过

全量 pytest（排除 4 个既有 `_funnel_benchmark_prep` 缺模块用例）：
基线 101 失败 → HEAD 101 失败，**失败集合完全一致，零新增、零修复**。
（101 个既有失败是 Project G 之前就存在的独立技术债，不在本次范围。）

### P3｜mes_f 实体抽取：本次唯一真实能力收益

`DB_SCHEMA.md` 由 0 变为 17 表 / 208 字段 / 17 IR 实体，来自 Phase 2 的确定性
markdown 管道表抽取（与 LLM 无关）。这是真实且有价值的进展。

---

## 4. 对「格式无关」结论的判定

**证伪。** 当前只实现了一种文档形状：字段定义表（字段名列 + 类型/说明列）。
已确认未覆盖：

| 未覆盖形状 | 实例 | 现状 |
|---|---|---|
| 实体清单表（表名 + 说明） | benchmark_mall `DB_SCHEMA.md` | 0 |
| 内联限定字段（`table.field`：说明） | benchmark_mall `DB_SCHEMA.md` | 0 |
| 枚举式数据字典 | mes_f `DATA_DICTIONARY.md` | 0 |
| 权限矩阵表 | mes_f `USER_ROLES.md` | 0 |

在 A 靶标为 0、且 4 类常见形状未覆盖的情况下，不能宣称格式无关认知能力已上线。

---

## 5. 返工要求（按优先级）

1. **B2 先修**：改用 `llm_reasoning` 的真实入口，并补一条"LLM 层可用性"启动自检，
   使该层不可用时以显式失败暴露，而不是每源静默降级为 0 候选。修复后必须给出
   **至少一次真实候选产出的运行证据**，Phase 3/4 才算存在。
2. **B1**：将抽取从"单一表形状"扩展为按列语义判定——识别表名列（表/表名/table/entity）
   产出实体、识别字段列产出字段，并支持 `table.field` 内联声明。
   禁止针对 benchmark_mall 做任何专用分支或关键词硬编码。
3. **B4**：实体身份规范化 + 别名冲突回执，参照 AGENTS.md 对 operation identity 的既有约定；
   过滤非表名行（小节标题、约束段落）。
4. **B5**：权限矩阵抽取补齐。
5. 交付时必须附**基线 vs HEAD 五项目对照表**（本文件 §1 格式）作为验收证据，
   而非单点微型测试数字。

---

## 6. 返工结果（2026-07-25）

### 6.1 五项目对照（确定性层单独测量，未启用 LLM）

测量时在脚本内注入空 `ReasoningConfig` 关闭模型，确保门禁结论不依赖任何模型输出。

| 项目 | IR 实体 基线 → 返工后 | 权限 | 判定 |
|---|---|---|---|
| benchmark_mall | **0 → 13** | 1 | 硬门禁 A **达成** |
| mes_f | **0 → 16** | **0 → 335** | 硬门禁 F **达成** |
| contractflow_c | 22 → 22 | 4 | 无回归 |
| ticketsla_d | 8 → **10** | 1 | 无回归（实体清单表带来净增） |
| warehouse_e | 11 → 11 | 0 | 无回归；表 12/23 → 11，重复身份合并、噪声消除 |

### 6.2 逐项关闭情况

| 编号 | 根因 | 修复 |
|---|---|---|
| B1 | 只识别"字段定义表"一种形状 | 新增实体清单表识别（`_is_entity_inventory_table`）与内联 `<entity>.<field>` 声明抽取，后者仅在限定符能解析到同源已声明实体时才接受，fail-closed |
| B2 | `get_reasoning_client` 不存在 | 改为真实入口 `_get_client`；新增 `semantic_extraction_availability()` 启动自检，层级不可用时发一条明确信号而不是每源重复失败 |
| B3 | Phase 4 无候选可验证 | B2 修复后取得真实候选：benchmark_mall `DB_SCHEMA.md` 单源 23 候选全部通过溯源校验；整项目 11 个候选 |
| B4 | 实体身份分裂 + 噪声表 | `_canonical_entity_name` 剥离标题序号与括号注释；`_merge_table_identities` 在源内与跨源合并声明，保留 `source_refs`/`aliases`/`declaration_count`，不再静默丢弃 |
| B5 | 权限矩阵未抽取 | 新增 `_permission_crosstab_entries`：按判定符号词表识别角色列，行=操作、列=角色。只固定符号词表，角色名与操作名全部来自源 |

补充修复的两处：`_infer_field_rows_from_markdown` 的标题归属是结构性错误（先全文扫完再贴标签，导致所有字段归到文档最后一个标题），改为位置感知；`source_types` 会泄漏空串标签。

### 6.3 抽取器泛化性证据

同一套识别逻辑在不同文档形状上生效，未针对任何项目开分支：

| 形状 | 实例 | 结果 |
|---|---|---|
| 实体清单表（表名 + 说明） | benchmark_mall `DB_SCHEMA.md` | 13 实体 |
| 内联限定字段 | benchmark_mall `DB_SCHEMA.md` | 9 字段 / 5 实体 |
| 分节字段定义表 | mes_f `DB_SCHEMA.md` | 16 实体 / 208 字段 |
| 权限交叉表（✓/✗ 取值） | mes_f `USER_ROLES.md` | 173 条 |
| 权限交叉表（YES/NO 取值） | mes_f `BUSINESS_RULES.md` | 162 条 |
| 带中文注释的实体标题 | warehouse_e `DATA_DICTIONARY.md` | 11 实体，注释保留为 alias |

### 6.4 语义层改为显式开启

修好 B2 后暴露出一个由此引入的新问题：Phase 3 会对每个零产出源同步发起一次提供方往返，
使 `build_enterprise_business_knowledge_asset` 从确定性离线函数变成带付费网络依赖的慢函数
（benchmark_mall 实测 0.44s → 约 5 分钟；测试进程 6 分钟内仅消耗 31 秒 CPU，其余全在等网络）。

处理方式：语义层改为调用方显式开启（`options['enable_semantic_extraction']` 或
`QUALIBUG_SEMANTIC_EXTRACTION=1`），默认保持确定性离线行为，并把
`not_requested` 如实写入 `semantic_extraction_availability` 与覆盖缺口，不做静默跳过。
同时加入每次构建的源数预算与 4 并发（与项目既有 LLM 并发默认一致），超预算的源发显式缺口。

实测：默认路径 0.44s / 13 实体 / 零网络；显式开启 87s / 11 个通过校验的候选。

### 6.5 回归与纪律

- 全量 pytest（排除 4 个既有缺模块用例）：基线 101 失败 → 返工后 101 失败，**集合完全一致，零新增**。
- 三个改动文件 `ast.parse` 全通过，`import ai_test_asset_center` 无副作用。
- Lint 无告警。
- 新增代码不含任何项目名、业务实体名、角色名、操作名；只固定与行业无关的表头语义词表和判定符号词表。
- 未改动 5174 / 8088 端口。
