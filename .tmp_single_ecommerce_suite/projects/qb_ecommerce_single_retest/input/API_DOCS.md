# 接口文档

Base URL: `http://localhost:4000`

## 认证

客户端登录后将 `token` 放入 Header：

```http
Authorization: Bearer user-1
```

## POST /api/auth/login

请求：

```json
{ "email": "alice@example.com", "password": "123456" }
```

响应：

```json
{ "token": "user-1", "user": { "id": 1, "email": "alice@example.com", "role": "customer" } }
```

## GET /api/products

参数：`q`, `category`, `sort`, `minPrice`, `maxPrice`。

- `sort=price_asc`：价格升序。
- `sort=price_desc`：价格降序。
- 默认只返回上架商品。

响应：

```json
{ "items": [{ "id": 1, "name": "Aster Phone 15", "price": 6999.99 }], "total": 1 }
```

## GET /api/products/{id}

商品不存在时返回 404。

## POST /api/cart/items

添加购物车。数量范围：1 到 99。

```json
{ "productId": 1, "qty": 1, "couponCode": "WELCOME50" }
```

## GET /api/cart

获取当前登录用户购物车。

## POST /api/orders

创建订单。订单初始状态为 `CREATED`。

```json
{
  "items": [{ "productId": 1, "qty": 1 }],
  "couponCode": "WELCOME50",
  "address": { "city": "上海", "detail": "人民路1号", "phone": "13800000000" }
}
```

## POST /api/payments

支付订单。金额必须等于订单应付金额。

## POST /api/refunds

申请退款。只有订单所有者或管理员可以退款。

## GET /api/orders

普通用户只能看到自己的订单，管理员可查看全部。

## GET /api/admin/audit-logs

仅管理员可访问。返回审计日志，不包含环境变量和密钥信息。

## 扩展接口兼容说明

### POST /api/legacy/v1/resource/1

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v2/resource/2

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v3/resource/3

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v4/resource/4

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v5/resource/5

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v6/resource/6

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v7/resource/7

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v8/resource/8

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v9/resource/9

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v10/resource/10

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v11/resource/11

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v12/resource/12

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v13/resource/13

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v14/resource/14

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v15/resource/15

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v16/resource/16

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v17/resource/17

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v18/resource/18

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v19/resource/19

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v20/resource/20

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v21/resource/21

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v22/resource/22

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v23/resource/23

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v24/resource/24

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v25/resource/25

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v26/resource/26

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v27/resource/27

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v28/resource/28

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v29/resource/29

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v30/resource/30

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v31/resource/31

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v32/resource/32

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v33/resource/33

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v34/resource/34

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v35/resource/35

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v36/resource/36

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v37/resource/37

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v38/resource/38

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v39/resource/39

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v40/resource/40

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v41/resource/41

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v42/resource/42

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v43/resource/43

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v44/resource/44

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v45/resource/45

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v46/resource/46

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v47/resource/47

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v48/resource/48

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v49/resource/49

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v50/resource/50

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v51/resource/51

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v52/resource/52

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v53/resource/53

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v54/resource/54

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v55/resource/55

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v56/resource/56

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v57/resource/57

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v58/resource/58

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v59/resource/59

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v60/resource/60

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v61/resource/61

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v62/resource/62

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v63/resource/63

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v64/resource/64

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v65/resource/65

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v66/resource/66

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v67/resource/67

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v68/resource/68

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v69/resource/69

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v70/resource/70

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v71/resource/71

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v72/resource/72

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v73/resource/73

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v74/resource/74

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v75/resource/75

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v76/resource/76

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v77/resource/77

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v78/resource/78

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v79/resource/79

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v80/resource/80

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v81/resource/81

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v82/resource/82

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v83/resource/83

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v84/resource/84

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v85/resource/85

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v86/resource/86

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v87/resource/87

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v88/resource/88

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v89/resource/89

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v90/resource/90

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v91/resource/91

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v92/resource/92

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v93/resource/93

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v94/resource/94

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v95/resource/95

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v96/resource/96

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v97/resource/97

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v98/resource/98

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v99/resource/99

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v100/resource/100

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v101/resource/101

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v102/resource/102

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v103/resource/103

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v104/resource/104

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v105/resource/105

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v106/resource/106

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v107/resource/107

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v108/resource/108

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v109/resource/109

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v110/resource/110

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v111/resource/111

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v112/resource/112

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v113/resource/113

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v114/resource/114

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v115/resource/115

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v116/resource/116

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 5。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### POST /api/legacy/v117/resource/117

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 6。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### PUT /api/legacy/v118/resource/118

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 7。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### DELETE /api/legacy/v119/resource/119

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 8。

- 响应字段：`code` 固定为 0，分页从第 0 页开始，时间格式为 timestamp。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。

### GET /api/legacy/v120/resource/120

- 请求字段：`userId` 为字符串，`amount` 单位为分，`status` 长度不超过 4。

- 响应字段：`code` 固定为 0，分页从第 1 页开始，时间格式为 yyyy-MM-dd HH:mm:ss。

- 兼容说明：旧客户端可继续传 `token` 查询参数，服务端需要与 Header 认证保持一致。
