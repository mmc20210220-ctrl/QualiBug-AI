# Phase63：时间范围分区守恒（Temporal Partition Conservation）

## 解决的问题

企业系统里，日期范围查询、账期结算、历史数据、运营报表和审计导出经常存在一种高价值缺陷：

- 查询整段时间返回的数据看起来正常；
- 按天、按账期或按小时拆分时，边界记录被静默漏掉；
- 两个相邻窗口都包含同一条边界记录，导致重复结算、重复统计或重复导出；
- 单条记录满足自己的时间条件，因此普通筛选断言无法发现问题。

Phase63 不新建时间引擎，而是在 Phase49/62 的变形差分执行器中增加一个显式、只读的时间窗口 Oracle：**同一完整业务范围与其相邻的左闭右开窗口必须包含完全相同且互不重叠的业务主键集合。**

## 何时启用

该关系必须由企业显式声明，原因是 API 的时间边界语义不能靠字段名或 OpenAPI 猜测。

只有同时满足以下条件才会执行：

1. 配置了 `boundary_semantics: "left_closed_right_open"`；
2. 配置了至少两个严格相邻的窗口；
3. 配置了 `complete_response: true`，确认给定业务范围的响应完整返回；
4. 运行模式为 `safe_live` 且目标环境通过共享安全边界校验。

任何响应被截断、返回非 2xx、或 `total` 大于返回记录数时，QualiBug 会记为 `skipped_incomplete_*` 观察，不会报业务 Bug。

## 配置示例

```json
{
  "target_environment": "staging",
  "metamorphic_differential_execution_mode": "safe_live",
  "metamorphic_differential_reasoning": {
    "contracts": [
      {
        "path": "/orders",
        "identity_fields": ["order_id"],
        "sample_query": {"tenant_id": "qa-tenant", "limit": 2000},
        "temporal_partitions": [
          {
            "name": "daily_settlement_window",
            "from_parameter": "created_from",
            "to_parameter": "created_to",
            "boundary_semantics": "left_closed_right_open",
            "complete_response": true,
            "windows": [
              {"from": "2026-06-01T00:00:00Z", "to": "2026-06-02T00:00:00Z"},
              {"from": "2026-06-02T00:00:00Z", "to": "2026-06-03T00:00:00Z"}
            ]
          }
        ]
      }
    ]
  }
}
```

`sample_query` 会同时应用于整段范围和每个窗口，因此可限定到测试租户、固定账期或其他安全业务范围。

## 发现证据

当出现反例时，报告仅保存：

- 脱敏后的整段范围与窗口请求；
- 每个窗口的状态码、行数和 `total`；
- 缺失、仅窗口出现或重叠业务主键的稳定哈希；
- 可重复使用的证据指纹。

不会持久化原始业务行、令牌或凭证。

## 执行边界

- `safe_live` 仅发起有上限的 GET 请求；
- 生产环境、未声明环境或不安全目标在任何请求前被阻断；
- LLM 可以提出后续观察假设，但不能直接形成缺陷；
- 同一反例跨运行重复出现时提高稳定性，仍保持 `needs_human_review`，直至人工确认。
