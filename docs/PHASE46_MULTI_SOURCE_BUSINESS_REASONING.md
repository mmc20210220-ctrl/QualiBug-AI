# Phase46：多源业务推理与确认缺陷回灌

## 目标

Phase46 将测试对象从“接口是否返回成功”升级为“企业业务事实是否仍然成立”。引擎综合以下输入：

- PRD/需求文本：提取同步、异常、并发、历史兼容等业务要求。
- OpenAPI：识别集合读取接口、业务主键、枚举/范围、服务边界与写路径。
- 页面观测：绑定页面可见指标与 API 字段，验证看板/页面口径。
- 运行结果：在 `safe_live` 下对 GET 接口构造查询并寻找反例。
- 历史数据快照：验证迁移后历史记录和关键兼容字段。
- QA 确认反馈：只吸收明确确认的真实缺陷，形成企业专属优先级记忆。

## 五类推理路径

1. **跨系统 Oracle**：按业务主键比对订单中心、ERP、CRM、WMS、财务等系统的状态、金额、数量和覆盖集合。
2. **页面/API Oracle**：页面可见指标必须与绑定 API 的同口径字段一致。
3. **异常路径**：非法枚举、超范围数值等参数不得被静默忽略并回退到未过滤成功结果。
4. **并发路径**：从 PRD/OpenAPI 推导幂等、库存、支付、提交等写路径；默认只生成可审批的沙箱方案，不对企业环境发写请求。
5. **历史数据路径**：对比脱敏历史快照与当前查询结果，发现记录不可读取、兼容字段丢失或不应改变的字段漂移。

## 安全执行策略

- 默认：`plan_only`。
- `safe_live`：仅发送 GET 请求，所有分页、响应字节数和样本量均受配置限制。
- 并发/重放写请求：始终标记 `sandbox_required`；必须在可回收测试数据和隔离环境中由专用执行器运行。
- 原始业务响应、历史快照、确认反馈的证据不复制到学习记忆；记忆只保存脱敏元数据、风险类型、Oracle 族、关键词和人工确认结论。

## 推荐配置

在真实项目配置中加入：

```json
{
  "multi_source_reasoning_execution_mode": "safe_live",
  "multi_source_reasoning": {
    "max_source_pages": 12,
    "cross_system_oracles": [
      {
        "title": "订单中心与 ERP 状态金额一致",
        "left_path": "/order-center/orders",
        "right_path": "/erp/orders",
        "left_identity_field": "order_id",
        "right_identity_field": "order_id",
        "field_mappings": [
          {"left_field": "status", "right_field": "status"},
          {"left_field": "amount", "right_field": "amount"}
        ],
        "require_same_coverage": true
      }
    ],
    "page_oracles": [
      {
        "title": "订单看板总数",
        "page_url": "/dashboard/orders",
        "page_label": "订单总数",
        "observed_value": 120,
        "api_path": "/dashboard/orders/summary",
        "api_field": "total"
      }
    ],
    "concurrency_paths": [
      {
        "path": "/orders/{order_id}/confirm",
        "method": "POST",
        "idempotency_header": "Idempotency-Key",
        "safe_sandbox": true,
        "expected": "相同业务意图至多生效一次"
      }
    ],
    "historical_data_paths": [
      {
        "path": "/orders",
        "identity_field": "order_id",
        "require_presence": true,
        "compatibility_fields": ["legacy_code", "status"],
        "records": [{"order_id": "legacy-001", "legacy_code": "OLD-001", "status": "archived"}]
      }
    ]
  }
}
```

## 页面、历史与反馈输入

- `platform_inputs/<project>/ui_observations.json`：可写入页面/API 绑定的 `api_path`、`api_field`、`observed_value`。
- `platform_inputs/<project>/historical_snapshots/*.json`：写入脱敏历史记录、`path`、`identity_field`、`compatibility_fields`。
- `platform_inputs/<project>/confirmed_bug_feedback.jsonl`：每行一条人工结论；仅 `is_valid_bug=true` 且非误报的记录进入学习记忆。

确认 Bug 的相似新假设会得到排序加分，但不会自动被标记为已确认缺陷。

## 命令

```bash
python -m ai_test_asset_center.multisource_reasoning --project <project> --mode profile
python -m ai_test_asset_center.multisource_reasoning --project <project> --mode run --execution-mode safe_live
python -m ai_test_asset_center.multisource_reasoning --project <project> --mode learn
```
