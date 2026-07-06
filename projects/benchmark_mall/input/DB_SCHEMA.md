# 数据库结构说明

数据库：PostgreSQL 16

主要表：

| 表 | 说明 |
|---|---|
| users | 用户与角色 |
| addresses | 收货地址 |
| products | 商品 |
| inventory | 库存主表 |
| inventory_locks | 库存锁定记录 |
| cart_items | 购物车 |
| coupons | 优惠券 |
| coupon_usage | 优惠券使用记录 |
| orders | 订单主表 |
| order_items | 订单明细 |
| payments | 支付流水 |
| refunds | 退款售后 |
| audit_logs | 操作日志 |

关键字段：

- `orders.status`：订单状态；
- `orders.total_amount`：商品总金额；
- `orders.discount_amount`：优惠金额；
- `orders.payable_amount`：应付金额；
- `inventory.available_qty`：可售库存；
- `inventory.locked_qty`：锁定库存；
- `payments.idempotency_key`：支付幂等键；
- `coupons.expires_at`：优惠券过期时间；
- `users.role`：角色。

连接方式：

```txt
postgresql://benchmark_user:benchmark_pass@localhost:55432/benchmark_mall
```
