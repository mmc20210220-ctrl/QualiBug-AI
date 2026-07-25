# 审计报告:主线接线验收 + 上游实体抽取断点诊断

> 审计日期:2026-07-25
> 审计对象:commit `9487f8f`(Binding 闭环 + 空间探索层接入发现主线)
> 审计人:产品大脑(独立于执行方)
> 上位约束:`AGENTS.md`、`docs/PROJECT_G_BINDING_SPACE_MAINLINE_INTEGRATION_SPEC.md`

---

## 第一部分:接线验收判定

**结论:NOT PASSED。不放行冻结 Project G 候选版本。**

### 门禁逐条

| 门禁 | 判定 | 独立证据 |
|---|---|---|
| 1 语法 + 导入无副作用 | PASS | `ast.parse` 通过;`import ai_test_asset_center` 无报错 |
| 2 既有测试无新增失败 | PASS | 父提交 `f6c741f` 独立 worktree 对照:基线 101 failed / 1605 passed,当前 101 failed / 1733 passed,失败集合逐条比对**完全一致** |
| 3 能力层 128 测试 | PASS | 通过数 +128;根目录无重复副本 |
| 4 主线接线证据 | **FAIL** | 4 个 SPEC 必需模块全仓库零调用 |
| 5 mes_f 端到端冒烟 | **未提供** | 执行方未执行;审计方自测发现实质失效 |
| 6 A–F 回归 | **未提供** | 提交中无任何回归产物 |
| 7 纪律审计 | 部分 | 无 GT 引用 / 端口 / 预算改动;但存在伪造 receipt 与吞异常 |
| 8 干净提交 | PASS | 9 文件,无运行产物垃圾 |

既有技术债(非本次引入,单独记录):`_funnel_benchmark_prep.py` 早在 `de50821` 被删除,导致 4 个测试模块无法收集;101 项失败为长期存量。

### 阻断性缺陷

| 编号 | 缺陷 | 证据 |
|---|---|---|
| B1 | `probe_status` 硬编码为常量 `PROBES_SKIPPED_CONTRACT_NOT_APPROVED`;`binding_runtime_probe.run_probes_for_ledger` 从未被调用 → 合约已批准时该字段说谎 | grep 全仓库零调用 |
| B2 | `ExperimentPortfolio` / `validate_portfolio_quotas` 未接线(SPEC Phase 2 要求 2) | 仅模块内定义 |
| B3 | `multi_surface_adapter.plan_cross_surface_execution` 未接线(Phase 3 要求 2) | 仅模块内定义 |
| B4 | `correlate_observations` 未接线(Phase 3 要求 4);`space_dimension_registry` 未接线(§5.1 维度覆盖摘要缺失) | 仅模块内定义 |
| B5 | 3 处 `except Exception` 静默吞错(空间探索、覆盖重排、多层观测),仅写入 receipt 后继续,运行仍显示健康 | 违反 AGENTS.md 第 1 条 |
| B6 | 空间坐标退化:85 条真实 mes_f 义务中 entity_ids 空 85/85、actor_id 空 85/85、state 空 85/85;`invariant_ids` 被无条件误填 `obligation_id`;`space_coordinate` 全仓库只写不读,未进入执行与 attempt ledger(违反 §5.1) | 真实数据实测 |
| B7 | 绑定门禁在真实义务上 0/60 通过(fixture / observer / operation 三维全 `no_binding`);未造成回归仅因代码对 COMPILED 实验直接跳过 | 真实数据实测 |
| B8 | 覆盖重排可丢行:budget=3 / 候选 5 条时 `select_next_batch` 只返回 3 条,而 `selected_count` 不变;`obligation_id` 重复或为空时 `_row_map` 使行丢失并重复(实测 3 行 → `[2,2,3]`)。触发即违反 `qualibug.obligation-attempt-ledger.v1` | 复现脚本 |

---

## 第二部分:上游实体抽取断点(本次审计的主要发现)

审计 B6/B7 时发现:mes_f 的 Behavior IR 建出 **70 个 operation、292 条 invariant,但 entities = 0**。追查后确认这是一个远在 Binding 与空间探索之上游的断点。

### 跨项目量化

| 项目 | data_tables | data_fields | permission_matrix | IR entities | IR operations | 历史发现表现 |
|---|---|---|---|---|---|---|
| contractflow_c | 27 | 27 | 4 | **22** | 70 | Unique TP 57.7% |
| warehouse_e | 12 | 12 | 0 | **11** | 47 | 4 个深层 Bug |
| ticketsla_d | 8 | 8 | 1 | **8** | 36 | 盲测 48% |
| **mes_f** | 0 | 0 | 0 | **0** | 70 | **盲测 3.1%** |
| **benchmark_mall** | 0 | 0 | 1 | **0** | 50 | 131-bug 长期低发现率 |

实体空间非空的三个项目发现表现明显更好;实体空间为零的两个项目表现最差。**Project F 盲测 3.1% 与 131-bug 基准的长期低发现率,都能被"实体空间为空"直接解释。**

### 根因分解

**D1 源分类错误** — `DB_SCHEMA.md` 在 mes_f 与 benchmark_mall 中均被判为 `collaboration_document`,从未进入表/字段抽取器。该文件内容是标准 markdown 表定义:

```markdown
## 1. products
| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: mat- | Material/product ID |
```

**D2 Markdown 字段抽取脆弱** — mes_f 的 `DATA_DICTIONARY.md` 已被正确分类为 `db_field_dictionary`,仍产出 `fields: 0`;而 contractflow_c 的同名文件产出 `fields: 35`。抽取器只认特定 markdown 形状。

**D3 零可观测性(最优先)** — 所有 7 个源的解析回执均为 `parser_status: parsed`、`fidelity: full`、`errors: []`,`coverage_gaps` 为 **0**,而实际 `tables=0 / fields=0 / permissions=0`。系统在结构化产出全空时报告"完全保真、无错误、无缺口"。这违反 AGENTS.md 第 1、3 条,也违反 Gate D 检查点"一个损坏的源必须作为可见 coverage gap 保留"。正是 D3 让 D1/D2 长期隐形。

### 能力真相

实体/字段抽取当前**实质依赖机器可读结构化文件**:

- 有效来源:`openapi.yaml`(contractflow_c 16 / ticketsla_d 8 表)、`schema.sql`(contractflow_c 11 表)
- 无效来源:纯 Markdown 的 DB_SCHEMA / DATA_DICTIONARY(A、F 全部为 0)

这与产品定位("面对陌生企业系统、资料可能不完整,自动建立可靠认知")存在实质差距:真实客户大多只提供 Markdown / Word / Excel 文档,不保证提供 openapi.yaml + schema.sql。

### 影响链

```text
源分类错误 + markdown 抽取失败(静默)
  → data_tables / data_fields / permission_matrix = 0
  → Behavior IR entities = 0,actor 空间为空
  → 空间坐标 entity / actor / state 维全空(B6)
  → 绑定账本无实体可绑 → 绑定门禁 0/60(B7)
  → 实验大面积 BLOCKED_MISSING_BINDING
  → Project F 盲测 recall 3.1%
```

Binding 闭环与空间探索层不是"实现得不对",而是**上游拿不到实体与字段**。在 D1–D3 修复前,继续打磨 Binding / 空间探索的边际收益极低。

---

## 第三部分:结论与建议

1. commit `9487f8f` **不予验收**,不得据此冻结 Project G 候选版本。
2. 下一个唯一最大断点应改为 **`ENTERPRISE_SOURCE_STRUCTURED_EXTRACTION_EMPTY`**(资料结构化抽取静默为空),而非接线质量缺陷。
3. 修复优先级:**D3(可观测性)→ D1(分类)→ D2(Markdown 抽取)**。先让失败可见,再修失败本身——否则无法验证修复是否真的生效。
4. B1–B8 的接线缺陷降级为随后一轮处理;其中 B1(伪造 receipt)与 B5(吞异常)因属红线性质,应与 D3 同批修复。
5. 成绩口径不变:本次审计不产生任何新的能力宣称;Project F 盲测 3.1% 仍为封存口径。

### 审计方法留痕

- 基线对照:`git worktree` 检出 `f6c741f`,同参数运行 pytest,导出并逐条比对失败集合(已清理 worktree)
- 真实数据实测:以 `projects/mes_f/input` 构建知识资产 → Behavior IR → 85 条真实义务,量化坐标空值率与绑定门禁通过率
- 跨项目对照:A / C / D / E / F 五个项目的 tables / fields / permissions / IR entities 计数
- 缺陷复现:`select_next_batch` 截断与 `_row_map` 塌缩均已脚本复现
