# WMS 仓储管理系统 - 业务规则说明

## 1. 系统概述

仓储库存订单履约管理系统，支持多组织（acme/globex）、多仓库（wh-001/wh-002/wh-003）的库存管理、订单履约、拣货出库、退货和补货全流程。

## 2. 角色与权限

| 角色 | 权限范围 |
|------|---------|
| ADMIN | 全部操作，含仓库创建/删除、商品价格修改 |
| MANAGER | 仓库管理、退货审批、库存管理、补货 |
| ORDER_MANAGER | 订单创建/确认/分配/取消、预留管理 |
| OPERATOR | 拣货、发货、入库操作（限本仓库） |
| CUSTOMER | 创建订单、查看自己的订单、申请退货 |
| AUDITOR | 只读访问，不可创建或修改任何业务数据 |

### 权限约束规则

- BR-AUTH-001: 商品价格修改（unit_price）仅ADMIN可执行
- BR-AUTH-002: 仓库删除仅ADMIN可执行
- BR-AUTH-003: 退货审批（approve/reject）仅MANAGER或ADMIN可执行
- BR-AUTH-004: AUDITOR角色为只读，不可创建订单或修改数据
- BR-AUTH-005: OPERATOR仅可操作自己所属仓库的资源
- BR-AUTH-006: 发货确认仅发货单创建者或MANAGER/ADMIN可执行

## 3. 组织与仓库Scope隔离

- BR-SCOPE-001: 用户仅可查看本组织的仓库列表
- BR-SCOPE-002: 用户仅可查看本组织的库存批次
- BR-SCOPE-003: 用户仅可查看本组织的发货单
- BR-SCOPE-004: OPERATOR创建拣货单时，订单仓库必须与自己的所属仓库一致
- BR-SCOPE-005: 不允许跨组织调拨或访问库存

## 4. 资源归属

- BR-OWN-001: CUSTOMER仅可查看和修改自己的订单
- BR-OWN-002: CUSTOMER仅可取消自己的订单
- BR-OWN-003: CUSTOMER仅可查看自己的退货记录
- BR-OWN-004: 发货单确认仅限创建者或管理角色

## 5. 订单状态机

```
CREATED → CONFIRMED → ALLOCATED → PICKING → PACKED → SHIPPED → DELIVERED
                                                              ↘ CANCELLED
```

### 状态转换规则

- BR-STATE-001: 确认（confirm）仅允许从CREATED状态执行
- BR-STATE-002: 分配（allocate）仅允许从CONFIRMED状态执行
- BR-STATE-003: 取消（cancel）不允许从SHIPPED或DELIVERED状态执行
- BR-STATE-004: 开始拣货（start pick）要求关联订单为ALLOCATED状态
- BR-STATE-005: 创建发货单要求拣货单为COMPLETED状态

## 6. 库存批次状态机

```
RECEIVED → AVAILABLE → RESERVED → PICKED → SHIPPED
                                        ↘ EXPIRED
```

- BR-BATCH-001: 库存分配仅从AVAILABLE状态批次中预留
- BR-BATCH-002: 批次更新必须携带version字段进行乐观锁校验
- BR-BATCH-003: 并发更新同一批次时，version不匹配应返回409 Conflict

## 7. 退货状态机

```
REQUESTED → APPROVED → RECEIVED → INSPECTED → REFUNDED
                                             ↘ REJECTED
```

- BR-RET-001: 审批（approve）仅允许从REQUESTED状态执行
- BR-RET-002: 已REJECTED的退货不可再审批
- BR-RET-003: 退款（refund）后应恢复对应库存数量

## 8. 拣货单状态机

```
CREATED → IN_PROGRESS → COMPLETED
                      ↘ CANCELLED
```

- BR-PICK-001: 取消拣货单时应释放已预留的库存项
- BR-PICK-002: 拣货单中的商品必须与关联订单行一致

## 9. 跨实体一致性规则

- BR-CONS-001: 订单行数量不得超过对应批次的可用库存（quantity - reserved_quantity）
- BR-CONS-002: 发货重量应与订单行的商品重量总和一致
- BR-CONS-003: 退货数量不得超过原订单行中该商品的数量
- BR-CONS-004: 拣货单中的商品项必须存在于关联订单的订单行中

## 10. 跨实体前置条件

- BR-PRE-001: 创建发货单前，关联拣货单必须为COMPLETED状态
- BR-PRE-002: 创建补货单时，供应商必须存在且为ACTIVE状态
- BR-PRE-003: 库存分配时，批次必须为AVAILABLE状态

## 11. 幂等性规则

- BR-IDEM-001: 相同order_ref的订单不可重复创建，应返回409
- BR-IDEM-002: 同一order_id + batch_id的预留不可重复创建
- BR-IDEM-003: 发货单不可重复确认（已CONFIRMED状态应返回409）

## 12. 数量守恒规则

- BR-CONV-001: 发货确认后，对应批次库存数量应扣减发货数量
- BR-CONV-002: 订单取消后，已预留的库存数量应释放（reserved_quantity减少）
- BR-CONV-003: 退货退款后，库存数量应恢复退货数量

## 13. 补偿规则

- BR-COMP-001: 发货失败后，已拣货的库存应恢复到拣货前状态
- BR-COMP-002: 取消拣货单后，关联的预留应释放，批次状态应恢复
- BR-COMP-003: 拒绝退货后，关联订单状态应恢复到退货前

## 14. 时间规则

- BR-TIME-001: 退货申请必须在订单的return_deadline之前
- BR-TIME-002: 补货单的expected_delivery不得早于创建当天

## 15. 聚合计算规则

- BR-AGG-001: 仓库的used_capacity应实时反映该仓库内所有批次库存总和
- BR-AGG-002: 添加订单行后，订单total_amount应自动重算为所有行line_total之和

## 16. 批量操作规则

- BR-BULK-001: 批量分配为原子操作，任一订单分配失败应全部回滚
- BR-BULK-002: 批量操作中每个订单需独立校验行项完整性和库存可用性
