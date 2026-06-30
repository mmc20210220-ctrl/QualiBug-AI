# 数据库表结构设计

## 1. 设计目标

数据库用于支撑综合电商核心链路：用户、商品、购物车、订单、支付、退款、物流、后台审计。测试引擎应重点检查字段约束、索引、外键、金额精度、状态机、历史迁移兼容性与数据一致性。

## 2. 核心实体关系

```text
users 1---N carts
users 1---N orders
orders 1---N order_items
orders 1---N payments
orders 1---N refunds
products 1---N order_items
products 1---N carts
```

## 3. 状态机

### 订单状态

```text
CREATED -> PAID -> SHIPPED -> FINISHED
CREATED -> CANCELLED
PAID -> REFUNDING -> REFUNDED
SHIPPED -> RETURNING -> RETURNED -> REFUNDED
```

### 支付状态

```text
INIT -> PROCESSING -> SUCCESS
INIT -> PROCESSING -> FAILED
SUCCESS -> REFUNDING -> REFUNDED
```

### 售后状态

```text
APPLIED -> APPROVED -> WAIT_RETURN -> RECEIVED -> REFUNDED
APPLIED -> REJECTED
```

## 4. 表说明

### users

用户登录、角色、会员等级、余额。登录凭证、锁定状态、余额一致性、角色权限是重点风险点。

### products

商品 SKU、分类、价格、库存、上下架状态、图片。搜索、库存并发、SKU 唯一性、草稿商品可见性是重点风险点。

### carts

购物车记录。数量边界、库存同步、游客合并、优惠券预计算是重点风险点。

### orders

订单主表。金额、状态流转、收货地址、支付时间、用户隔离是重点风险点。

### order_items

订单商品明细。下单价格快照、SKU 快照、库存扣减、退货数量是重点风险点。

### payments

支付流水。幂等、金额一致、回调验签、重复支付是重点风险点。

### refunds

退款单。权限、退款金额上限、订单状态、优惠分摊、跨境税费是重点风险点。

## 5. 迁移脚本

`db/migrations` 中模拟多个历史版本迁移。测试引擎应把迁移脚本和当前表结构一起看，判断历史兼容逻辑是否可能影响运行时行为。
