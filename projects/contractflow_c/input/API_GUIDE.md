# API使用说明

完整机器可读定义见 `openapi/openapi.yaml`。

## 鉴权

```http
Authorization: Bearer <api_token>
```

登录：

```http
POST /api/v1/auth/login
Content-Type: application/json

{"email":"admin@acme.test","password":"Admin123!"}
```

## 并发控制

修改合同时携带：

```http
If-Match-Version: 3
```

版本不一致应返回409。

## 幂等付款

```http
POST /api/v1/payment-requests/{id}/pay
Idempotency-Key: pay-demo-001
```

相同租户、相同幂等键的重试必须返回同一结果。

## 标准错误

```json
{
  "detail": "human readable message",
  "code": "BUSINESS_ERROR_CODE",
  "correlation_id": "optional"
}
```

## 关键接口分组

- `/api/v1/contracts`：合同；
- `/api/v1/contracts/{id}/milestones`：合同里程碑；
- `/api/v1/milestones/{id}`：履约与验收；
- `/api/v1/invoices`：发票；
- `/api/v1/payment-requests`：付款申请；
- `/api/v1/budgets`：预算；
- `/api/v1/audit-logs`：审计。
