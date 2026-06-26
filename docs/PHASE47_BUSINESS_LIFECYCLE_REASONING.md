# Phase47：业务生命周期反例引擎

## 目标

Phase47 面向“接口都成功，但企业流程已经错了”的问题。它把 PRD 中的流程描述、OpenAPI 中的状态字段和写路径、运行中的集合数据及可选事件历史组合成可执行状态机，并主动寻找反例。

它覆盖两条路径：

- **只读事实验证**：在 `safe_live` 下只发送 GET，请求真实列表和事件历史，发现状态与事实矛盾。
- **隔离写入验证计划**：跳步、终态重入、重复推进、竞态推进等需要写入的场景只生成 `sandbox_required` 计划，绝不在企业生产环境执行。

## 可发现的流程类 Bug

- `created_at <= paid_at <= shipped_at <= completed_at` 等里程碑时间倒置。
- 状态已经是“已支付/已发货/已完成”，但支付时间、物流时间、完成凭证为空。
- 逻辑删除、归档、失效的数据仍出现在活跃列表。
- 超过有效期的内容、价格、授权、规则仍然显示为有效。
- 订单/审批/工单事件历史中跳过必须状态，例如 `created -> shipped`。
- 事件历史最终状态与当前记录状态不一致（需显式开启）。
- 写入沙箱候选：重复支付/确认、取消后重新推进、越过审批或发货前置状态。

## 自动推导与企业配置

引擎会优先从 OpenAPI 的 `status/state/phase` 字段、枚举和时间字段推导状态机；PRD 中的 `created -> paid -> shipped -> completed` 也可补全顺序。

企业应对核心流程补充显式契约，以获得更高置信度和更低误报：

```json
{
  "business_lifecycle_execution_mode": "safe_live",
  "business_lifecycle_reasoning": {
    "max_source_pages": 12,
    "lifecycle_rules": [
      {
        "path": "/orders",
        "resource": "order",
        "identity_field": "order_id",
        "state_field": "status",
        "states": ["created", "paid", "shipped", "completed", "cancelled"],
        "allowed_transitions": {
          "created": ["paid", "cancelled"],
          "paid": ["shipped", "cancelled"],
          "shipped": ["completed"],
          "completed": [],
          "cancelled": []
        },
        "terminal_states": ["completed", "cancelled"],
        "timeline_fields": [
          {"state": "created", "field": "created_at"},
          {"state": "paid", "field": "paid_at"},
          {"state": "shipped", "field": "shipped_at"},
          {"state": "completed", "field": "completed_at"}
        ],
        "required_fields_by_state": {
          "paid": ["paid_at", "payment_id"],
          "shipped": ["paid_at", "shipped_at", "tracking_no"],
          "completed": ["completed_at"]
        },
        "soft_delete_field": "is_deleted",
        "history_path_template": "/orders/{order_id}/events",
        "history_event_state_field": "to_status",
        "history_event_from_field": "from_status",
        "history_event_time_field": "created_at",
        "history_must_match_current": true,
        "write_actions": [
          {"path": "/orders/{order_id}/pay", "method": "POST", "action": "pay"},
          {"path": "/orders/{order_id}/ship", "method": "POST", "action": "ship"}
        ]
      }
    ]
  }
}
```

## 安全与学习

- 默认 `plan_only`；`safe_live` 仅发送 GET。
- 事件历史查询、分页和响应字节数受限。
- 原始业务数据不写入持久化报告；只保存脱敏字段、哈希和可复现请求形态。
- Phase46 的人工确认缺陷记忆会为相似生命周期契约提供排序加分；未确认发现永远保留为 `needs_human_review`。

## 命令

```bash
python -m ai_test_asset_center.business_lifecycle_reasoning --project <project> --mode plan_only
python -m ai_test_asset_center.business_lifecycle_reasoning --project <project> --mode safe_live
```
