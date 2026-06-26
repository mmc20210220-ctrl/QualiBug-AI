# Phase56 · 业务质量保障覆盖与测试有效性引擎

## 目标

Phase56 不再单独新增某一种 Bug 模板，而是回答一个更核心的问题：

> 对当前企业的关键业务规则、关键接口和已确认缺陷，我们到底**有多少可执行 Oracle 覆盖**？哪些失败模式即使发生，系统也还发现不了？

它把“自动保障企业产品质量”转成可量化、可审计、可持续收敛的工程闭环，而不是不可证明的零缺陷承诺。

```text
PRD / 业务规则 / OpenAPI / 已确认缺陷
        ↓
关键业务单元（Requirement / API / Regression）
        ↓
模型化业务失败模式（Mutation Models）
        ↓
现有 Oracle 与探针覆盖匹配
        ↓
未被捕获的 Mutation Survivor = 质量覆盖缺口
        ↓
高优先级 Probe 候选 + 发布治理结论
```

## 关键能力

### 1. 业务质量保障图谱

系统自动建立三类保障单元：

- **业务需求单元**：PRD 中的幂等、权限、状态、范围、一致性等规则。
- **接口与关键路径单元**：OpenAPI 的读写接口、金额、支付、库存、租户、审批、事件、补偿等核心路径。
- **确认缺陷回归单元**：Phase55 中已经审批的真实 Bug 回归候选。

每个单元都会被映射为一组必须能够被发现的失败模式。

### 2. 模型化业务 Mutation 测试

Mutation 并不向生产系统注入故障。它是在质量模型中模拟“如果系统发生这种错误，当前 Oracle 能否捕获”的问题。

已覆盖的业务失败模型包括：

- 响应契约被破坏、必填业务字段静默缺失。
- 筛选失效、分页重复/漏数、排序错误。
- 列表/详情/统计/导出数据漂移。
- 未授权访问、跨租户数据泄漏。
- 状态跳步、时间线倒置、历史数据回归。
- 重复副作用、缺失副作用、金额/数量不守恒。
- 跨记录额度、资源冲突、审批阈值绕过。
- 事件丢失、重复、乱序、消费者缺失。
- Saga 补偿、退款、释放、回滚未收敛。

### 3. 质量覆盖缺口自动生成

没有被任何匹配 Oracle 覆盖的 Mutation 会生成：

- `assurance_coverage_gap` 风险项。
- 对应路径、方法、风险等级、预期 Oracle 家族。
- 可执行的 `business_assurance_coverage` Probe 候选。
- 只读路径默认 `safe_read_only`；写路径固定 `sandbox_required`。

覆盖缺口不是“已确认线上 Bug”，而是高优先级发布治理问题：它说明当前质量无法被证据证明。

### 4. 可证明的产品主张边界

Phase56 强制输出 `claim_guard`：

允许使用：

> 基于业务 Oracle、反例搜索、回归证据和覆盖缺口治理的持续质量保障。

禁止使用：

- 自动保证零缺陷。
- 覆盖所有业务 Bug。
- 无需人工复核即可保证生产质量。

只有当关键路径覆盖、模型化 Mutation 杀伤率、需求规则覆盖和已确认缺陷回归均达到企业阈值时，才进入：

`evidence_backed_continuous_assurance`

## 质量保障分

保障分由五类可审计证据组成：

| 指标 | 含义 |
|---|---|
| 模型化 Mutation 杀伤率 | 当前 Oracle 是否能捕获已建模业务失败模式 |
| P0/P1 关键路径覆盖率 | 核心业务路径是否被完整保护 |
| PRD 规则覆盖率 | 规则是否已转为可执行 Oracle |
| 已确认缺陷回归覆盖率 | 历史真实 Bug 是否持续受到保护 |
| 结构覆盖率 | 单元是否至少有部分 Oracle 覆盖 |

该分数代表当前证据范围内的**持续保障能力**，不是“无缺陷概率”或“全 Bug 覆盖率”。

## 配置示例

```json
{
  "quality_assurance_coverage": {
    "minimum_assurance_score": 85,
    "critical_paths": [
      {"path": "/orders", "method": "POST", "severity": "P0"},
      {"path": "/orders/{order_id}", "method": "GET", "severity": "P1"}
    ],
    "required_controls": [
      {
        "title": "订单付款后必须收到 ERP 账务确认",
        "path": "/orders",
        "method": "POST",
        "mutation": "event_chain_break",
        "severity": "P0",
        "required_oracle_sources": [
          "business_event_chain_reasoning",
          "business_reconciliation"
        ]
      }
    ]
  }
}
```

`required_controls` 用于表达企业独有、不能只靠字段命名推断的核心质量口径。

## 运行方式

```bash
python -m ai_test_asset_center.business_assurance_coverage --project <project_id>
python -m ai_test_asset_center.business_assurance_coverage --project <project_id> --run
```

输出位置：

- `platform_outputs/<project>/business_assurance_coverage/business_assurance_coverage.json`
- `platform_outputs/<project>/business_assurance_coverage/business_assurance_coverage_report.html`
- `platform_workspace/<project>/defect_discovery/business_assurance_coverage_gaps.json`

## 安全边界

- Phase56 不向企业环境注入故障。
- 不发送 POST/PUT/PATCH/DELETE。
- 生产环境只依赖已有 GET-only 证据执行能力。
- 写路径、并发、重放、补偿等验证只能生成 `sandbox_required` 任务。
- 质量覆盖缺口需要人工/流程确认后才能成为发布阻断策略。
