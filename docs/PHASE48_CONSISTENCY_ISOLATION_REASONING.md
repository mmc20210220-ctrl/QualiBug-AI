# Phase48：最终一致性、异步结果与租户隔离反例引擎

Phase48 不把接口 `200 OK` 当成业务成功。它从 PRD、OpenAPI、企业配置和真实 GET 结果中构建四类 Oracle，并主动寻找反例：

1. **租户隔离 Oracle**：在两个显式、只读的租户上下文中读取同一集合，验证记录的 `tenant_id/org_id` 与当前上下文一致、私有业务身份不重叠，并对少量已知跨租户 ID 做 GET 详情访问验证。
2. **异步最终结果 Oracle**：任务进入 `succeeded/completed` 后必须存在结果、文件、输出或产物；任务进入 `failed` 后必须留下错误码、原因或诊断信息。
3. **读模型/缓存传播 Oracle**：将事实源和搜索索引、缓存、投影、看板读模型按业务主键对齐，检查关键字段漂移和更新时间是否超过最终一致性 SLA。
4. **短间隔读稳定性 Oracle**：对明确声明为稳定的只读视图做多次 GET，排除动态字段后检查价格、状态、标题等业务字段是否无故漂移。

## 为什么这类能力重要

很多企业线上事故并非单个 API 返回错误，而是：

- 导入、导出、结算、同步任务显示“成功”，但结果文件或业务产物为空；
- 消息消费成功，搜索/看板/缓存却没有及时刷新；
- 多租户列表或详情接口在某个权限上下文下混入其他客户数据；
- CDN、缓存键、读写分离或多副本路由导致用户短时间内看到不一致的价格、状态或库存。

这些问题往往在传统接口断言里全部为绿，因为每个接口都返回 200。

## 运行边界

- 默认 `plan_only`。
- `safe_live` 只执行 `GET`，包括租户详情的跨上下文读取检查；不创建任务、不修改数据、不触发回调。
- 任务重复完成、重复回调、缓存失效竞态等需要写入或并发的场景只生成 `sandbox_required` 计划。
- `token`、`headers` 仅在内存中使用。写入 profile、运行报告、证据包前会去掉值，仅保留“已配置认证/哪些 header 名称”。
- 发现均标记为 `needs_human_review`；同一稳定指纹跨运行重复出现才会提升置信度。

## 配置

参见：`examples/consistency_isolation_reasoning_config.example.json`。

- 租户上下文建议使用 `token_env`，不要把真实 token 写入项目配置。
- 对读模型配置时，优先显式声明 `compare_fields` 与 `staleness_tolerance_seconds`，避免将允许异步更新的字段误判为缺陷。
- 对短间隔稳定性，仅配置本应稳定的查询；实时指标、库存秒级刷新、时钟字段不要纳入 `stable_fields`。

## 输出

- `platform_outputs/<project>/consistency_isolation_reasoning/consistency_isolation_profile.json`
- `platform_outputs/<project>/consistency_isolation_reasoning/consistency_isolation_run.json`
- `platform_outputs/<project>/consistency_isolation_reasoning/consistency_isolation_run_report.html`
- `platform_workspace/<project>/defect_discovery/consistency_isolation_evidence_registry.json`

已接入风险驱动探针排序、主缺陷发现报告、发布阻断候选与确认缺陷记忆回灌。
