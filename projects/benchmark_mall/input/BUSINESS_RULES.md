# 业务规则

## 订单状态机

```txt
CREATED -> PENDING_PAYMENT -> PAID -> SHIPPED -> COMPLETED
PENDING_PAYMENT -> CANCELLED
PAID -> REFUND_REQUESTED -> REFUNDED
COMPLETED -> REFUND_REQUESTED -> REFUNDED
```

禁止状态流转：

- CANCELLED -> PAID
- CANCELLED -> SHIPPED
- CANCELLED -> COMPLETED
- REFUNDED -> SHIPPED
- REFUNDED -> PAID
- CLOSED -> 任意状态

## 金额规则

- `total_amount = sum(item.price * item.qty)`
- `discount_amount` 不能小于 0；
- `discount_amount` 不能大于 `total_amount`；
- `payable_amount = total_amount - discount_amount`；
- 支付金额必须等于订单应付金额；
- 退款金额不能大于实际支付金额；
- 金额计算保留 2 位小数。

## 库存规则

- 用户下单时，应扣减 available_qty 并增加 locked_qty；
- 订单取消时，应减少 locked_qty 并恢复 available_qty；
- 订单支付成功后，应将 locked_qty 消耗；
- available_qty、locked_qty 均不能为负数；
- 同一个 SKU 在高并发下不得超卖。

## 数据隔离规则

- 买家只能查询自己的订单、购物车、地址、退款；
- 商家只能维护自己的商品；
- 后台接口必须校验角色；
- 报表接口不能把其他租户/角色不应看到的数据返回给普通用户。

## 前端规则

- 用户端不展示下架商品、草稿商品、内部商品；
- 管理端不同角色应展示不同菜单；
- 禁用状态的操作按钮必须不可点击；
- 前端禁用按钮不能替代后端校验。
