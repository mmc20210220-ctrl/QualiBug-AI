# QualiBug AI 真实端到端根因优化报告

日期：2026-08-01
目标分支：`main`
优化原则：沿现有 Compiler → Lifecycle → Executor → Observer → Oracle → Cleanup → Finalizer 主链修复，不新增旁路引擎，不使用结果过滤掩盖根因。

## 1. 验证对象

本轮直接运行用户提供的完整项目源码，并使用正式编译器、正式执行器、正式观察器、正式 Oracle、正式清理器和正式 Finalizer。

真实网络回归使用本地 `ThreadingHTTPServer` 作为可控 SUT，HTTP transport 未被 mock。测试覆盖：

1. `POST /api/orders` 创建业务对象；
2. `GET /test-observers/events` 在完整观察窗口内轮询事件；
3. 确认业务缺陷：创建成功但没有产生 `OrderCreated`；
4. Oracle 输出 `VIOLATION / EVENT_DELIVERY_COUNT_BELOW_MINIMUM`；
5. `DELETE /api/orders/{id}` 执行补偿；
6. readback 证明环境恢复；
7. Finalizer 生成完整 Execution Receipt Bundle；
8. 生命周期进入 `TRUE_COMPLETED`，Finding 保留为真实业务问题。

这里的 `TRUE_COMPLETED` 表示测试协议、证据和环境恢复均完整完成，并不表示业务断言通过。业务断言失败仍可以是一次完整且有效的测试执行。

## 2. 根因与修改

### 2.1 服务存活与业务就绪被混为一个状态

原问题：未配置 LLM 或尚未导入企业资料时，唯一健康端点可能返回 offline/503，容器编排和反向代理会把可正常提供配置界面的进程误判为宕机。

根因修复：

- `live/ok` 只表达服务进程和私有根目录是否可服务；
- `ready` 独立表达 LLM、System Behavior 和企业配置是否满足业务扫描条件；
- 未配置 LLM 返回 `HTTP 200 + live=true + ready=false + status=degraded`；
- 私有根目录不存在仍保持 `live=false/status=offline`。

### 2.2 Runtime 插件替换公开执行入口

原问题：Operation Causality、Attachment 和 Async Job 插件直接覆盖 `experiment_plan_executor.execute_non_barrier_plans`，从而绕过 Lifecycle Adapter。内层执行器创建的新 Ledger 会替换实验入口 Ledger，导致 Fixture、Protocol、步骤和 Finalizer 身份不一致。

根因修复：

- Lifecycle Adapter 新增唯一 raw transport delegate 安装入口；
- Runtime 插件只包装私有 raw delegate；
- `experiment_plan_executor`、`experiment_executor_core`、`experiment_executor_governance` 和公开 Executor 别名始终重新发布 Lifecycle Adapter；
- 入口 Ledger 始终是主权 Ledger，内层 Ledger 只能通过现有公共复制方法合并事实。

### 2.3 Fixture 适用性由表面 setup 行误判

原问题：只有 `resolve_bindings` 或 `query_entity_binding` 的 setup 被误判为需要业务 Fixture，导致空 Fixture ID 进入 Bundle。

根因修复：

- 已冻结的 FlowData Requirement 成为 Fixture 需求权威；
- 零物化目标明确输出 `fixture_required=false` 和 `fixture_id=NOT_APPLICABLE`；
- binding-only setup 不再凭空创建 Fixture；
- 缺少显式协议身份时使用正式 `NOT_APPLICABLE`，不保留空坐标；
- 零目标 FlowData 仍生成一份“无需物化但已验证”的 provenance receipt。

### 2.4 Transport Response 自证 Observer 完成

原问题：HTTP response body fingerprint 同时被写入 Observer receipt 列表，传输响应可以自证观察完成，破坏观察层独立性。

根因修复：

- Transport response 只证明请求到达与响应事实；
- Observer receipt 只能来自正式 Observer；
- HTTP per-step 审计仅在真正声明 HTTP Observer Authority 时启用；
- Event、Database 等非 HTTP Observer 不再被误判为缺失 `http_response/per_plan_step`。

### 2.5 多步骤同类 Observer 被 `observer_id` 去重

原问题：多个步骤都使用 `http_response` Observer 时，合并器按 `observer_id` 去重，只保留第一步，隐藏后续步骤证据缺口。

根因修复：

- 去重身份改为 `receipt_id` 优先；
- 同一 Observer、不同精确 step scope 可以共存；
- 同 Observer、同 step 才视为重复；
- 当存在精确逐步骤回执时，无 scope 聚合回执不再参与步骤证据门。

### 2.6 Oracle 结果与协议是否完整评估混用

原问题：Event Oracle 输出 `VIOLATION` 时，步骤被视为“语义未完成”，导致真实发现 Bug 的执行落入 `PROCESS_PARTIAL`。

根因修复：

- `PROPERTY_HELD` 和 `VIOLATION` 都表示协议已经完整评估；
- Event Outcome Bridge 独立投影 `target_reached=true`；
- 业务断言 truth 保留在 Oracle verdict，不再决定 transport/semantic completion；
- 因此可以同时得到 `VIOLATION` Finding 与 `TRUE_COMPLETED` 生命周期。

### 2.7 Cleanup Verification 多权威与指数膨胀

原问题：诊断性环境摘要、原始等价性对象、聚合回执和逐步骤回执混入同一个正式列表；无 ID 对象在原地列表发布和重复同步中呈 `1→3→7→15→31→63` 增殖，真实运行一度生成约 95 条 verification。

根因修复：

- Cleanup Executor 不再把诊断摘要双写到正式 verification 列表；
- `process_step_receipt_scope` 成为清理回执唯一投影权威；
- 无正式 ID、无 sealed fingerprint 的诊断对象不能升级为正式证据；
- 单写等价性通过确定性 exact-step projection 进入 Ledger；
- Graph aggregate 与逐步骤 receipts 同时进入 Bundle，但只有精确逐步骤 receipts 绑定 Ledger；
- 重复 receipt ID 若声明多个步骤，在任何绑定前整体拒绝；
- 重复 scope sealing 保持幂等，不再增长列表。

### 2.8 Wait Contract 错误原因优先级错误

原问题：声明中的 method/path/system 等合同错误会被次级“异步边未覆盖”错误吞掉，用户无法看到根因。

根因修复：

- 先返回明确的声明合同错误；
- 只有合同本身有效后，才检查 derived wait coverage；
- 不改变既有 Wait Engine 或轮询算法。

### 2.9 显式 Observer 所有权泄漏

原问题：已经被某个步骤显式认领的 Secondary Observer 仍进入默认主步骤 projection，造成跨步骤证据串联。

根因修复：

- Compile Freeze 阶段收集所有显式 observer claim；
- 默认/主步骤 projection 排除已被其他步骤显式认领的 Observer；
- 不新增运行时猜测。

## 3. 权威边界

优化后的主线权威如下：

```text
Source Contracts
  → Experiment Compiler / Freeze
  → FlowData & Fixture Authority
  → Experiment Lifecycle Ledger
  → Lifecycle Adapter
      → private raw transport delegates
          → graph / causality / async-job specialization
  → Exact Observer Receipts
  → Contract Oracle
  → Cleanup Execution
  → Cleanup Equivalence
  → Exact Step Receipt Scope
  → Execution Receipt Bundle
  → Execution Finalization Receipt
```

关键不变量：

- Runtime 插件不能替换 Lifecycle Adapter；
- Transport receipt 不能充当 Observer receipt；
- Aggregate receipt 不能推断单一步骤身份；
- 同一 receipt ID 不能跨步骤复用；
- 诊断对象不能进入正式 Bundle；
- 发现业务 Bug 不等于执行协议未完成；
- 环境恢复必须由 Cleanup Equivalence 证明。

## 4. 正式回归

新增或迁移的回归覆盖：

- liveness/readiness 分离；
- Runtime 插件安装后公开入口仍为 Lifecycle Adapter；
- 零目标 FlowData 与 binding-only setup 的 Fixture 适用性；
- 清理回执 projection 的确定性和幂等性；
- Graph aggregate + exact step 双层 Bundle；
- 跨步骤重复 receipt ID 零绑定；
- 多步骤同类 Observer 精确共存；
- Wait Contract 精确错误原因；
- Event `VIOLATION` + Cleanup `EQUIVALENT` + Lifecycle `TRUE_COMPLETED`；
- 使用真实 HTTP transport 的完整网络端到端回归。

推送前门禁：

1. 本报告列出的 61 项权威回归全部通过；
2. 项目 Python 文件全量 `compileall` 通过；
3. 官方健康契约相关回归通过；
4. GitHub `main` 采用非强制 fast-forward 更新；
5. 若远程并发前进，必须基于最新 `main` 三方合并，禁止 force push。

## 5. 未通过旧测试的处理原则

仓库历史测试中存在互相冲突的旧语义，例如：

- 一组要求步骤执行事实与完成事实分离；
- 另一组要求缺少正向语义回执时执行事实直接消失；
- 一组要求 Graph Bundle 只保留聚合回执；
- 另一组要求逐步骤 evidence 可被 Bundle 审计；
- 一组认为零目标 FlowData 不应有任何 provenance；
- 当前正式 Bundle 契约要求“无需物化”也必须有可审计证明。

本轮没有为了旧断言变绿而回退真实 E2E 已验证的正式权威。迁移只发生在与当前主线合同明确冲突的测试中。

## 6. 结论

本轮瓶颈不在 HTTP 执行速度，也不在 LLM 推理，而在跨阶段证据身份和权威边界：

- 主权 Ledger 曾被插件替换；
- Observer、Oracle、Cleanup 和 Bundle 曾混用同一列表；
- “发现 Bug”曾被误判为“测试未完成”；
- 诊断摘要曾被升级为正式证据并指数增殖。

优化后，正式主链可以在发现业务违反的同时，证明执行完整、补偿成功、环境恢复、证据 Bundle 完整，并进入 `TRUE_COMPLETED`。这才是 QualiBug 后续提升真实 Bug 发现率可以依赖的稳定运行基础。
