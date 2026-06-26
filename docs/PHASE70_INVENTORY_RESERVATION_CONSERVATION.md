# Phase70：库存预占与可用库存守恒 Oracle

## 目标

Phase70 不增加仓储系统、库存数据库、写入测试或独立 UI。它复用既有
`business_causality_conservation` 引擎，把已显式映射的库存快照与预占明细变成
只读、可回放的业务 Oracle，用来发现“接口均返回成功，但库存已经失真”的高价值
企业缺陷。

覆盖的核心关系为同一显式库存键（通常为 `SKU + 仓库`）：

```text
active reservation quantity sum == snapshot reserved quantity
available quantity == on-hand quantity - reserved quantity
available quantity >= 0   (除非企业显式允许负可用库存/预售口径)
```

## 可发现的高价值缺陷

- 下单、重试、消息延迟或取消回滚后，库存快照 `reserved` 与有效预占明细之和不一致。
- 可用库存没有按在库减预占更新，导致前台售卖量、补货计划或报表错误。
- 可用库存为负，提示可能已超卖、重复扣减或预占未释放。
- 有效预占找不到完整库存快照，提示库存主数据删除、跨仓映射错误或残留预占。

## 显式配置优先

库存语义不能由字段名推断：有些企业允许预售负库存，有些系统将安全库存、冻结库存、
质检库存或锁定库存纳入不同字段。因此 Phase70 只执行企业明确提供的映射，不会看到
`sku`、`reserved` 或 `available` 就自动生成正式缺陷。

```json
{
  "business_causality_execution_mode": "safe_live",
  "business_causality_conservation": {
    "contracts": [
      {
        "type": "inventory_reservation_balance",
        "source_path": "/inventory/snapshots",
        "dependent_path": "/inventory/reservations",
        "inventory_identity_fields": ["sku_id", "warehouse_id"],
        "reservation_identity_fields": ["sku_id", "warehouse_id"],
        "on_hand_field": "on_hand_quantity",
        "reserved_field": "reserved_quantity",
        "available_field": "available_quantity",
        "reservation_quantity_field": "quantity",
        "reservation_status_field": "status",
        "active_reservation_states": ["reserved", "allocated"],
        "allow_negative_available": false,
        "tolerance": 0.001
      }
    ]
  }
}
```

`inventory_identity_fields` 与 `reservation_identity_fields` 都是强制的显式连接键。若同一
SKU 在多个仓、货主、批次、渠道或租户下分别计数，必须把这些维度全部包含在键中。

## 证据与安全边界

- 仅访问 OpenAPI 已声明的集合型 `GET` 接口；不下单、不锁库、不扣库存、不释放预占。
- 只有库存快照与预占明细均被判定为完整分页集合时才出具反例；不完整时记录跳过原因。
- 重复库存键、缺字段或不合法数量会降低可验证范围，不被伪造为业务缺陷。
- 证据保存字段映射、数量聚合、容差和哈希后的库存键，不保存 SKU、仓库、订单号、预占号、请求头或令牌。
- `safe_live` 执行继续受共享 `execution_safety_verdict` 约束；生产、未声明或不安全目标会在首次 GET 前阻断。
- LLM 只能提出待确定性回放的候选，不能直接进入正式 findings、学习记忆、验证队列或发布门禁。

## 主链路接入

Oracle 复用既有契约画像、风险探针计划、证据指纹、确认学习和真实项目发现链。它会生成：

- `inventory_reservation_quantity`：预占明细与库存预占守恒；
- `inventory_available_balance`：在库、预占、可用数量公式守恒；
- `inventory_negative_available_stock`：未允许负库存时的超卖风险。

发现以 P1（守恒/公式/孤儿预占）或 P0（负可用库存）进入既有风险编排，但仍需企业人工确认后才可提升为确认缺陷。
