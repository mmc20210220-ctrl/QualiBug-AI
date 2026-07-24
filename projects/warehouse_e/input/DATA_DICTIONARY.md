# WMS 数据字典

## 实体关系

```
Supplier ──1:N──> RestockOrder
Warehouse ──1:N──> InventoryBatch
Product ──1:N──> InventoryBatch
Order ──1:N──> OrderLine
Order ──N:1──> Customer
Order ──N:1──> Warehouse
OrderLine ──N:1──> Product
Order ──1:N──> Reservation
Reservation ──N:1──> InventoryBatch
Order ──1:1──> PickList
PickList ──1:1──> Shipment
Order ──1:N──> Return
Return ──N:1──> Product
```

## 核心实体

### Product (商品)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 主键 |
| sku | string | 唯一库存编码 |
| name | string | 商品名称 |
| org | string | 所属组织 |
| unit_price | number | 单价 |
| weight_kg | number | 单位重量(kg) |
| category | enum | ELECTRONICS/MECHANICAL/PACKAGING/GENERAL |
| status | enum | ACTIVE/DISCONTINUED |

### Warehouse (仓库)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 主键 |
| name | string | 仓库名称 |
| org | string | 所属组织 |
| capacity | integer | 总容量 |
| used_capacity | integer | 已用容量(应实时计算) |
| status | enum | ACTIVE/INACTIVE/MAINTENANCE |

### InventoryBatch (库存批次)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 主键 |
| product_id | FK→Product | 商品 |
| warehouse_id | FK→Warehouse | 所在仓库 |
| org | string | 所属组织 |
| quantity | integer | 库存数量 |
| reserved_quantity | integer | 已预留数量 |
| status | enum | RECEIVED/AVAILABLE/RESERVED/PICKED/SHIPPED/EXPIRED |
| expiry_date | date | 过期日期 |
| version | integer | 乐观锁版本号 |

### Order (订单)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 主键 |
| order_ref | string | 唯一业务参考号(幂等键) |
| customer_id | FK→Customer | 客户 |
| org | string | 所属组织 |
| warehouse_id | FK→Warehouse | 履约仓库 |
| status | enum | CREATED/CONFIRMED/ALLOCATED/PICKING/PACKED/SHIPPED/DELIVERED/CANCELLED |
| total_amount | number | 订单总金额(自动计算) |
| created_by | string | 创建者ID |
| return_deadline | date | 退货截止日 |
| version | integer | 版本号 |

### OrderLine (订单行)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 主键 |
| order_id | FK→Order | 所属订单 |
| product_id | FK→Product | 商品 |
| quantity | integer | 数量 |
| unit_price | number | 单价(创建时快照) |
| line_total | number | 行金额 = quantity × unit_price |

### PickList (拣货单)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 主键 |
| order_id | FK→Order | 关联订单 |
| warehouse_id | FK→Warehouse | 拣货仓库 |
| org | string | 所属组织 |
| status | enum | CREATED/IN_PROGRESS/COMPLETED/CANCELLED |
| items | array | 拣货项[{product_id, quantity, batch_id}] |
| created_by | string | 创建者 |

### Shipment (发货单)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 主键 |
| order_id | FK→Order | 关联订单 |
| pick_list_id | FK→PickList | 关联拣货单 |
| warehouse_id | FK→Warehouse | 发货仓库 |
| org | string | 所属组织 |
| status | enum | PENDING/CONFIRMED/IN_TRANSIT/DELIVERED/FAILED |
| weight_kg | number | 发货重量 |
| carrier | string | 承运商 |
| created_by | string | 创建者 |

### Return (退货)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 主键 |
| order_id | FK→Order | 原订单 |
| customer_id | FK→Customer | 客户 |
| org | string | 所属组织 |
| product_id | FK→Product | 退货商品 |
| quantity | integer | 退货数量 |
| reason | string | 退货原因 |
| status | enum | REQUESTED/APPROVED/RECEIVED/INSPECTED/REFUNDED/REJECTED |

### RestockOrder (补货单)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 主键 |
| supplier_id | FK→Supplier | 供应商 |
| product_id | FK→Product | 商品 |
| quantity | integer | 补货数量 |
| warehouse_id | FK→Warehouse | 目标仓库 |
| org | string | 所属组织 |
| status | enum | PENDING/ORDERED/RECEIVED/CANCELLED |
| expected_delivery | date | 预期到货日 |

### Supplier (供应商)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 主键 |
| name | string | 供应商名称 |
| org | string | 所属组织 |
| lead_time_days | integer | 交货周期(天) |
| status | enum | ACTIVE/SUSPENDED |

### Reservation (库存预留)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 主键 |
| order_id | FK→Order | 关联订单 |
| batch_id | FK→InventoryBatch | 关联批次 |
| product_id | FK→Product | 商品 |
| quantity | integer | 预留数量 |
| org | string | 所属组织 |
| status | enum | ACTIVE/RELEASED/CONSUMED |

## 关键业务约束

1. order_ref全局唯一（幂等键）
2. 同一order_id + batch_id组合不可重复预留
3. InventoryBatch.version用于乐观锁并发控制
4. 可用库存 = quantity - reserved_quantity
5. 订单total_amount = SUM(所有OrderLine.line_total)
6. 仓库used_capacity = SUM(该仓库所有批次quantity)
