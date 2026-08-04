# API 接口文档

API Base URL: `http://localhost:8080`

所有需要登录的接口使用：

```http
Authorization: Bearer <token>
```

## Auth

### POST /api/auth/login

请求：

```json
{"email":"buyer01@example.com","password":"Test@123456"}
```

响应：

```json
{"token":"...","user":{"id":"...","email":"...","role":"buyer","status":"ACTIVE"}}
```

### POST /api/auth/register

请求：

```json
{"email":"new@example.com","password":"Test@123456","name":"新用户","phone":"13900000000"}
```

## Product

### GET /api/products

查询商品列表。常用参数：

- `category`
- `keyword`
- `minPrice`
- `maxPrice`
- `status` 后台使用

### GET /api/products/:sku

查询商品详情。

### POST /api/products/admin

后台创建商品。seller/admin 可用。

### PATCH /api/products/admin/:sku

后台修改商品。seller/admin 可用。

## Cart

### POST /api/cart/items

请求：

```json
{"sku":"SKU-PHONE-001","qty":1}
```

### GET /api/cart/items

查询当前用户购物车。

### PATCH /api/cart/items/:id

修改数量或选中状态。

### DELETE /api/cart/items/:id

删除购物车条目。

## Coupon

### POST /api/coupons/validate

请求：

```json
{"code":"NEW100","items":[{"sku":"SKU-PHONE-001","qty":1,"price":6999}],"totalAmount":6999}
```

## Order

### POST /api/orders

请求：

```json
{
  "items":[{"sku":"SKU-PHONE-001","qty":1}],
  "couponCode":"NEW100",
  "addressId":"<address_id>"
}
```

### GET /api/orders/:id

查询订单。

### GET /api/orders

查询订单列表。

### POST /api/orders/:id/cancel

取消订单。

### POST /api/orders/:id/ship

发货，warehouse/admin 可用。

### POST /api/orders/:id/confirm

确认收货。

## Payment

### POST /api/payments/pay

请求：

```json
{"orderId":"<order_id>","amount":6899,"channel":"BALANCE","idempotencyKey":"abc-001"}
```

## Refund

### POST /api/refunds

请求：

```json
{"orderId":"<order_id>","amount":100,"reason":"不想要了"}
```

### POST /api/refunds/:id/approve

财务或管理员审批退款。

### POST /api/refunds/:id/reject

财务或管理员驳回退款。

## Report

### GET /api/reports/sales

销售报表。

### GET /api/reports/inventory-risk

库存风险报表。

## User

### GET /api/users/addresses

查询用户地址（应校验归属）。

可选查询参数：

- `userId` — 目标用户 ID；调用方只能查询自己的地址，跨用户查询应返回 403/404。

### POST /api/users/addresses

创建用户地址。

请求：

```json
{"receiver":"张三","phone":"13800000000","province":"上海","city":"上海","detail":"浦东新区"}
```

### DELETE /api/users/addresses/:id

删除用户地址（身份绑定清理；创建后必须可逆）。

### GET /api/users/admin/search

管理员搜索用户（应仅限管理员）。

### PATCH /api/users/admin/users/:id/balance

管理员调整用户余额（应仅限管理员）。

请求：

```json
{"delta":100,"reason":"补偿"}
```
