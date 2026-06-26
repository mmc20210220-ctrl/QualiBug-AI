# Phase44：跨视图业务对账引擎（Business Reconciliation）

## 解决的问题

很多企业系统的高价值缺陷不在接口可用性，而在**业务口径**：

```text
GET /orders/summary -> 200 OK
```

接口成功，仍可能存在严重质量问题：

- 运营看板显示订单总数 1,002，但明细只有 998；
- 销售额因为重复关联、重复聚合或错误过滤被多算；
- 各状态订单数加总和总订单数互相矛盾；
- 同一个日期、租户、部门筛选条件在统计接口与列表接口的解释不同；
- 列表分页正常，但 KPI 只统计了首页或重复计算了某页。

Phase44 把统计、看板、报表、概览 API 看成**需要被证明的业务断言**：用同一筛选口径读取底层明细，自动分页复算，再与统计结果逐项对账。

## 自动发现与执行

从 OpenAPI 自动识别：

- 统计/看板/报表路径与描述：`summary`、`statistics`、`dashboard`、`metrics`、`overview`、`report`、`统计`、`看板`、`汇总`；
- 同资源的只读集合接口；
- 可对账指标：总数、数量、金额、费用、余额、收入等。

可验证的指标包括：

1. `count`：统计总数与同口径列表 total/全量记录数一致；
2. `sum`：金额、数量、费用等字段从明细重新求和后与 KPI 一致；
3. `group_count`：按状态、部门、租户、渠道等维度的分组数量一致；
4. `group_sum`：按维度的分组金额/数量一致。

当 OpenAPI 命名不够明确时，使用显式对账契约，避免猜测业务口径。

## 企业配置示例

```json
{
  "business_reconciliation_execution_mode": "safe_live",
  "business_reconciliation": {
    "max_source_pages": 12,
    "max_response_bytes": 3000000,
    "metric_contracts": [
      {
        "summary_path": "/api/orders/summary",
        "source_path": "/api/orders",
        "sample_query": {
          "tenant_id": "test-tenant",
          "date_from": "2026-06-01",
          "date_to": "2026-06-30"
        },
        "pagination": {
          "page_param": "page",
          "size_param": "page_size",
          "page_size": 200
        },
        "metrics": [
          {
            "summary_field": "data.total_count",
            "kind": "count"
          },
          {
            "summary_field": "data.total_amount",
            "kind": "sum",
            "source_field": "total_amount",
            "tolerance": 0.01
          },
          {
            "summary_field": "data.status_counts",
            "kind": "group_count",
            "group_field": "status"
          },
          {
            "summary_field": "data.department_amounts",
            "kind": "group_sum",
            "group_field": "department_id",
            "source_field": "total_amount",
            "tolerance": 0.01
          }
        ]
      }
    ]
  }
}
```

`sample_query` 应使用测试环境中已知存在数据且适合对账的业务范围。它会同时传给统计和明细接口，确保双方使用同一口径。

## 防误报边界

- 默认 `plan_only`；`safe_live` 只允许 GET。
- 明细接口分页会被有上限地读取；达到总数后停止。
- 金额/分组等需要明细全量的数据，如果只拿到不完整页会被标记为 `skipped_incomplete_source`，不会直接报缺陷。
- 只保存脱敏请求、汇总数字、分页覆盖情况和稳定指纹；不持久化完整业务明细。
- 同一对账偏差跨运行重复出现会提高置信度，但始终保留 `needs_human_review`。

## 输出

```text
platform_outputs/<project>/business_reconciliation/
  business_reconciliation_profile.json
  business_reconciliation_profile_report.html
  business_reconciliation_run.json
  business_reconciliation_run_report.html

platform_workspace/<project>/defect_discovery/
  business_reconciliation_evidence_registry.json
```

该能力已接入风险排序与真实项目主发现链路。对账类探针优先级高于常规模板探针，发现后会进入发布拦截候选。
