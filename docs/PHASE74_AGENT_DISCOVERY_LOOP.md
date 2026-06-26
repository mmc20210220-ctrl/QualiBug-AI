# Phase74：Autonomous Business Bug Discovery Agent Loop

## 目标

QualiBug 的核心不是预设“这个行业有多少 Bug”，也不是堆叠行业规则。Phase74 建立一个可长期运行的、项目级的 Agent Loop：在不知道目标系统缺陷总数的情况下，持续管理业务假设、覆盖边界、执行证据、人工裁决、回归守卫与下一步实验。

## 单一权威状态

项目唯一可写状态存储为：

```text
platform_workspace/<project>/agent_discovery_loop/canonical_discovery_ledger.sqlite3
```

CSV 仅作为给业务负责人、QA 与安全审批人查看的电子表格投影：

```text
platform_outputs/<project>/agent_discovery_loop/canonical_discovery_ledger.csv
```

CSV 不会被 Agent 当作可写状态，因此并发运行、历史审计、幂等更新和回滚不依赖对话上下文或人工复制表格。

## 状态机

```text
HYPOTHESIS_NEEDS_REVIEW
  -> READY_FOR_READONLY
  -> EVIDENCE_CAPTURED
  -> CONFIRMED / REJECTED
  -> REGRESSION_GUARD

HYPOTHESIS_NEEDS_REVIEW
  -> BLOCKED_BY_APPROVAL
  -> EVIDENCE_CAPTURED
  -> CONFIRMED / REJECTED
  -> REGRESSION_GUARD
```

- `HYPOTHESIS_NEEDS_REVIEW`：来自 PRD、API、OpenAPI、源码语义或 LLM 的候选；不是 Bug。
- `READY_FOR_READONLY`：业务负责人已确认、可由既有 GET-only 执行器回放的契约。
- `BLOCKED_BY_APPROVAL`：写入、并发、重试或状态机实验；必须经过可销毁 Sandbox、项目开关、审批 ID 和既有安全门。
- `EVIDENCE_CAPTURED`：已有确定性运行时证据，等待人类最终裁决。
- `CONFIRMED`：人类确认的根因。
- `REGRESSION_GUARD`：从确认根因生成的长期回归守卫。

## 每一轮 Agent Loop

1. 从现有世界模型、文档约束编译器、并发沙箱计划和发现候选同步项目状态。
2. 以风险、未知度、证据强度和执行安全性计算信息增益与优先级。
3. 对同一根因簇只安排一个下一步实验，避免重复消耗预算。
4. 输出 `next_best_experiment_manifest.json`，由现有安全执行器消费。
5. 将运行时证据、人工确认/驳回和回归守卫写回同一 SQLite ledger。
6. 下一轮只读取该 ledger，而不是依赖 Agent 对话记忆。

## 安全与可信度

- Loop 本身不执行 HTTP 写请求。
- 生产环境保护、Sandbox 批准和执行器安全门不在本模块中放宽或复制。
- 静态证据与 LLM 候选永远不会变成正式 Bug。
- 只有确定性运行时证据加人工裁决才能进入 `CONFIRMED`。
- 事件链哈希可验证；账本被篡改后拒绝继续写入。
- 不记录已知 Bug 总数，也不读取 benchmark truth、oracle 或隐藏答案文件。

## MES 文档演示

给 MES 的 PRD 与 `API.md` 后，Loop 在未读取真值目录的前提下编译了 66 条文档约束实验：

- 3 条角色边界 GET 验证可安全回放；
- 63 条写入、幂等、库存、生产、质量和设备实验被标记为 Sandbox 审批阻塞；
- 未自动执行写请求，也没有把 66 条实验称为 66 个 Bug。

命令：

```bash
python -m aitestops.cli agent-loop --project mes_agent_loop_demo --root . --max-actions 12
```
