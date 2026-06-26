# Phase76：Agent Business-Flow Orchestrator

## 目标

Phase74 建立了未知缺陷探索账本，Phase75 将单条文档约束编译成实验包。Phase76 把这两层推进为可复现的**多步骤业务流实验**：Agent 可以在同一权威账本里管理一个业务流的前置数据、状态迁移、异常动作、前后快照、守恒验证和运行时证据。

它不以“某系统有多少已知 Bug”为输入。已知缺陷目录、基准答案和隐藏 Oracle 均不参与运行时计划。

## 单一权威状态

Phase76 不新增第二个工作流数据库。每个流仍存为 Phase74 SQLite 账本中的一条 `loop_experiments` 记录：

```text
platform_workspace/<project>/agent_discovery_loop/canonical_discovery_ledger.sqlite3
```

- `experiment_type=multi_step_business_flow`
- 一条流有一个稳定场景指纹、步骤包、执行收据和证据状态。
- 流内发现只会将父账本项推进到 `EVIDENCE_CAPTURED`。
- 人工确认仍是 `CONFIRMED` 与回归守卫的唯一入口。

## 业务流来源

### API/PRD 推断

API 文档中出现“创建资源 + 带资源标识的后续动作”时，系统可生成候选流，例如：

```text
POST /orders
POST /orders/{orderNo}/release
POST /orders/{orderNo}/cancel
```

这类候选只回答“此处存在状态化流程面”，不会猜测合法状态、有效请求体、角色或清理方式，因此保持 `candidate_only`。

### 企业显式流映射

只有项目配置中的 `agent_discovery_loop.business_flow_catalog.flows` 才能编译为可执行流。每条流必须明确：

- `flow_id`、风险类型、严重度；
- 每一步的角色、HTTP 方法、路径、请求体、捕获字段和预期；
- 必要时的 `fixture_ids`；
- 前后快照断言，例如失败操作后库存数量保持不变。

## 通用步骤语义

支持两类步骤：

- `request`：执行一个已映射的 GET/POST/PUT/PATCH/DELETE。
- `snapshot`：执行 GET 并保存数据哈希供后续守恒或补偿验证。

模板值可由同一流中的前置输出提供：

```text
${run_key}
${fixture.materialCode}
${flow.orderNo}
```

例如，一个订单状态机流可写为：

```json
{
  "flow_id": "cancelled_order_cannot_report",
  "severity": "P1",
  "risk_type": "state_transition",
  "steps": [
    {
      "step_id": "create",
      "method": "POST",
      "path": "/orders",
      "role": "PLANNER",
      "body": {"orderNo": "ORD-${run_key}"},
      "captures": {"orderNo": "data.orderNo"},
      "expect": {"accepted": true}
    },
    {
      "step_id": "cancel",
      "method": "POST",
      "path": "/orders/${flow.orderNo}/cancel",
      "role": "PLANNER",
      "expect": {"accepted": true}
    },
    {
      "step_id": "report_after_cancel",
      "method": "POST",
      "path": "/orders/${flow.orderNo}/report",
      "role": "OPERATOR",
      "expect": {"accepted": false, "description": "cancelled order rejects reporting"}
    }
  ]
}
```

## 前后快照验证

Phase76 支持：

- `snapshot_path_unchanged` / `snapshot_path_equal`：失败或禁止动作后字段必须保持不变；
- `snapshot_numeric_delta`：例如失败调拨后来源库存变化必须为 `0`；
- `flow_value_present`：前置步骤必须产生后续流程所需标识。

断言不通过时才产生 `runtime_strong` 证据。证据保存步骤、状态码、字段路径、数值差异和负载哈希；不保存认证头、密码、Token 或原始业务数据。

## 安全边界

- 编译阶段零网络请求。
- 推断流永远不会执行。
- 写流必须由既有 `document_contract_fuzzing` 的 Sandbox 门禁批准：
  - 环境为 `sandbox`；
  - `disposable_sandbox=true`；
  - 调用时显式 `execute=true`；
  - 调用时显式 `approved_sandbox_execution=true`；
  - 有人工 `approval_id`；
  - 共享安全门通过。
- 不执行直接 DELETE 清理；写流只允许可销毁 Sandbox 重置。
- 正向前置步骤失败默认作为环境/Fixture 阻塞，不会被伪报为产品缺陷。

## 命令

只编译：

```bash
python -m aitestops.cli agent-flows --project <project_id> --root .
```

仅在可销毁 Sandbox、项目配置和人工批准均完备后执行：

```bash
python -m aitestops.cli agent-flows \
  --project <project_id> --root . --execute --approval-id <approval_id>
```
