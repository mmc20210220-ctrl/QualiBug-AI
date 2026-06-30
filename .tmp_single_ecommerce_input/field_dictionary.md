# 数据库字段说明

## users 用户表

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | INTEGER | 用户ID | 主键 |
| email | VARCHAR(64) | 登录邮箱 | 必填 |
| password | VARCHAR(64) | 登录密码 | 必填 |
| role | VARCHAR(16) | customer/admin | 默认 customer |
| level | VARCHAR(16) | normal/silver/gold/platinum | 可空 |
| balance | DECIMAL(8,2) | 账户余额 | 可空 |
| created_at | TEXT | 注册时间 | ISO 日期 |

## products 商品表

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | INTEGER | 商品ID | 主键 |
| sku | VARCHAR(32) | SKU 编码 | 应唯一 |
| name | VARCHAR(128) | 商品名称 | 可搜索 |
| category | VARCHAR(32) | 分类 | electronics/fashion/grocery/home |
| price | DECIMAL(8,2) | 售价 | 大于 0 |
| stock | INTEGER | 库存 | 大于等于 0 |
| status | VARCHAR(16) | ON_SALE/DRAFT/OFFLINE | 默认 DRAFT |
| image_url | TEXT | 图片地址 | 可空 |

## orders 订单表

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | VARCHAR(64) | 订单ID | 主键 |
| user_id | INTEGER | 下单用户 | 关联 users.id |
| status | VARCHAR(16) | CREATED/PAID/SHIPPED/FINISHED/CANCELLED/REFUNDED | 必填 |
| payable | DECIMAL(8,2) | 实付金额 | 大于等于 0 |
| address | TEXT | 收货地址 JSON | 必填 |
| created_at | TEXT | 创建时间 | ISO 日期 |
| paid_at | TEXT | 支付时间 | 可空 |
