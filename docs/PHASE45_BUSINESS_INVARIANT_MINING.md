# Phase45：业务不变量挖掘与反例搜索（Business Invariant Mining）

## 目标

这一阶段不把 Bug 能力做成“预置案例库”。系统从 **PRD + OpenAPI + 实际只读响应** 自动推导企业系统中应始终成立的业务约束，并主动寻找反例。

导出重复只是其中一种反例。相同框架可发现：

- 页面/接口接受了筛选参数，但返回记录并不满足筛选条件；
- 同一订单、员工、客户、工单等业务主键在同一事实集合中重复；
- OpenAPI 标为必填的业务字段在真实数据中为空；
- 实际状态值超出文档枚举；
- 数量、额度、比例等超出 API 规定的最小/最大边界；
- 开始时间晚于结束时间、生效时间晚于失效时间、创建时间晚于更新时间；
- 订单 `customer_id`、工单 `assignee_id`、合同 `vendor_id` 等外键指向不存在资源；
- 有效筛选后的记录并不属于完整未筛选事实集合。

## 发现方法

### 1. 自动挖掘不变量

对每个 OpenAPI 的 `GET collection`（例如 `/orders`、`/employees`、`/tickets`）生成候选契约：

1. **业务主键**：优先使用 `{resource}_id`、`id`、`uuid`、`code`；可显式配置。
2. **运行时字段约束**：从 `required`、`enum`、`minimum`、`maximum` 生成检查。
3. **筛选语义**：仅当 Query 参数能映射到返回字段、且有有效枚举值或显式配置时，生成“筛选结果必须满足字段条件”的变形测试。
4. **时间关系**：识别 `start/end`、`effective_from/effective_to`、`created/updated` 等字段对；也可显式配置。
5. **引用完整性**：识别如 `customer_id` 的字段，并匹配 `/customers/{customer_id}` 这类只读详情接口；也可显式配置。

### 2. 主动证伪

在 `safe_live` 中只发 GET：

- 拉取受限页数的未筛选事实集合；
- 请求一个合法筛选变体；
- 对返回记录执行业务不变量；
- 抽样验证外键目标是否存在；
- 将命中的反例生成脱敏证据、稳定指纹和跨运行观测次数。

因此，系统不是猜“哪个接口可能有 Bug”，而是在验证“哪些企业业务事实不可能同时为真”。

## 安全边界

- 默认：`plan_only`。
- `safe_live`：只允许 GET，不创建任务、不写入、不删除、不回放写操作。
- 数据拉取、分页和外键查询均有上限。
- 证据持久化前脱敏，业务引用值以 hash 摘要保存。
- 所有发现均为 `needs_human_review`；重复观测只提高置信度，不直接替代人工业务确认。

## 配置示例

见：`examples/business_invariant_config.example.json`。

```json
{
  "business_invariant_execution_mode": "safe_live",
  "business_invariant_mining": {
    "max_source_pages": 12,
    "max_relation_samples": 12,
    "collections": [
      {
        "path": "/orders",
        "identity_fields": ["order_id"],
        "filters": [{"parameter": "status", "field": "status", "value": "paid"}],
        "foreign_keys": [{"field": "customer_id", "target_path": "/customers/{customer_id}"}],
        "temporal_pairs": [{"start": "valid_from", "end": "valid_to"}]
      }
    ]
  }
}
```

显式配置并不替代自动挖掘；它用于承载企业独有命名、复杂外键和口径例外。

## 产物

```text
platform_outputs/<project>/business_invariant_mining/
  business_invariant_profile.json
  business_invariant_profile_report.html
  business_invariant_run.json
  business_invariant_run_report.html

platform_workspace/<project>/defect_discovery/
  business_invariant_evidence_registry.json
```

## 与既有能力的协作

- **Phase41**：从规格生成输入、边界和结构探针。
- **Phase42**：跨接口语义反例，如列表/详情投影和分页关系。
- **Phase43**：导出/文件类业务结果审计。
- **Phase44**：统计、看板与明细的业务口径对账。
- **Phase45**：将每个事实集合提升为业务不变量集合，直接从实时数据中发现更细粒度的企业质量问题。
