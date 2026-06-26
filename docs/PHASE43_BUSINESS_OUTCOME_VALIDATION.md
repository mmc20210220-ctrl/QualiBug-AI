# Phase43：业务结果审计引擎（Business Outcome Validation）

## 解决的问题

传统接口自动化通常只验证：

```text
GET /orders/export -> 200 OK
```

这不足以说明业务正确。真实企业缺陷常发生在“接口成功以后”：

- 导出成功，但同一订单/员工/客户重复出现；
- 列表有 100 条，导出只有 98 条或多出范围外数据；
- 页面筛选为 `已支付`，导出仍混入 `待创建` 数据；
- 源数据与导出金额、数量汇总不一致；
- CSV / Excel 文件能下载但关键标识为空、文件格式异常。

Phase43 将导出、下载、报表接口视为**可验证的业务结果**，而不是单次 HTTP 请求。

## 自动发现能力

从 OpenAPI 的 path、operationId、summary、响应 Content-Type 识别：

- `export` / `download` / `report` / `extract` / `dump`
- `导出` / `下载` / `报表` / `明细` / `文件`
- `text/csv`、Excel MIME、二进制下载响应

随后自动匹配同一资源的只读列表接口，并生成五类业务 Oracle：

1. **唯一性**：业务唯一键（如 `order_id`）不能重复；没有可靠唯一键时，检测完整非动态业务行重复。
2. **数据源覆盖**：同筛选条件下，源列表 total、标识集合与导出结果应一致。
3. **筛选生效**：合法筛选条件必须反映在导出行中，不能被静默忽略。
4. **汇总一致性**：金额、数量、费用等可加总字段在源数据与导出中必须一致。
5. **文件/行质量**：CSV、XLSX、JSON/JSONL 可解析，关键标识不应为空。

## 安全执行边界

- 默认 `plan_only`：只生成高价值审计契约和探针。
- `safe_live`：仅调用 `GET` 导出与 `GET` 源列表接口。
- 不会调用 `POST/PUT/PATCH/DELETE`，不会创建导出任务。
- 对异步导出：仅在已有 GET 响应返回下载 URL 时继续 GET 下载文件。
- 不持久化原始导出文件，只保留文件 hash、行数、字段名、脱敏样例和稳定指纹。

## 企业项目配置

复杂项目建议显式配置导出契约，避免字段命名差异造成误判：

```json
{
  "business_outcome_execution_mode": "safe_live",
  "business_outcome_validation": {
    "max_export_bytes": 12000000,
    "export_contracts": [
      {
        "export_path": "/api/orders/export",
        "export_method": "GET",
        "source_path": "/api/orders",
        "source_method": "GET",
        "resource": "order",
        "identity_fields": ["order_id"],
        "sample_query": {"status": "paid"},
        "field_mappings": {"order_id": "订单号"},
        "aggregate_fields": ["total_amount", "paid_amount"]
      }
    ]
  }
}
```

`sample_query` 必须使用测试环境中已知存在数据的**合法**筛选条件。引擎用同一条件请求导出和源列表，避免因为默认范围不同而制造误报。

## 输出

每次运行写入：

```text
platform_outputs/<project>/business_outcome_validation/
  business_outcome_profile.json
  business_outcome_profile_report.html
  business_outcome_validation_run.json
  business_outcome_validation_run_report.html

platform_workspace/<project>/defect_discovery/
  business_outcome_evidence_registry.json
```

相同业务结果异常跨运行会通过稳定指纹累积 `observations`，持久问题会提升置信度；所有发现仍保留 `needs_human_review`，方便企业 QA 按业务规则确认。
