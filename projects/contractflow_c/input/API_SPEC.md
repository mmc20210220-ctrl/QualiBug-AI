# API 接口文档

API Base URL: `http://localhost:8000`

所有需要登录的接口使用：

```http
Authorization: Bearer <token>
```

## 测试账号

Acme租户测试Token：
- admin: `acme-admin-token`
- legal: `acme-legal-token`
- finance: `acme-finance-token`
- requester: `acme-requester-token`
- project_manager: `acme-manager-token`
- auditor: `acme-auditor-token`
- vendor: `acme-vendor-token`

Globex租户测试Token：
- admin: `globex-admin-token`
- requester: `globex-requester-token`
- finance: `globex-finance-token`

## Contracts

### GET /api/v1/contracts

查询当前租户合同。

响应 200：

```json
[{"contract_no": "string", "title": "string", "department_id": "550e8400-e29b-41d4-a716-446655440000", "vendor_id": "550e8400-e29b-41d4-a716-446655440000", "budget_id": "550e8400-e29b-41d4-a716-446655440000", "total_amount": 1000.0, "currency": "string", "start_date": "2025-01-01", "end_date": "2025-01-01", "internal_notes": "string", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "owner_id": "550e8400-e29b-41d4-a716-446655440000", "paid_amount": 1000.0, "status": "DRAFT", "rejection_reason": "string", "version": 1, "created_at": "2025-01-01T10:00:00Z", "updated_at": "2025-01-01T10:00:00Z"}]
```

### POST /api/v1/contracts

创建合同草稿。

请求：

```json
{"contract_no": "string", "title": "string", "department_id": "550e8400-e29b-41d4-a716-446655440000", "vendor_id": "550e8400-e29b-41d4-a716-446655440000", "budget_id": "550e8400-e29b-41d4-a716-446655440000", "total_amount": 1000.0, "currency": "string", "start_date": "2025-01-01", "end_date": "2025-01-01", "internal_notes": "string"}
```

响应 201：

```json
{"contract_no": "string", "title": "string", "department_id": "550e8400-e29b-41d4-a716-446655440000", "vendor_id": "550e8400-e29b-41d4-a716-446655440000", "budget_id": "550e8400-e29b-41d4-a716-446655440000", "total_amount": 1000.0, "currency": "string", "start_date": "2025-01-01", "end_date": "2025-01-01", "internal_notes": "string", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "owner_id": "550e8400-e29b-41d4-a716-446655440000", "paid_amount": 1000.0, "status": "DRAFT", "rejection_reason": "string", "version": 1, "created_at": "2025-01-01T10:00:00Z", "updated_at": "2025-01-01T10:00:00Z"}
```

### GET /api/v1/contracts/:contractId

获取合同详情。

必须验证tenant_id。vendor视图不得暴露internal_notes。

响应 200：

```json
{"contract_no": "string", "title": "string", "department_id": "550e8400-e29b-41d4-a716-446655440000", "vendor_id": "550e8400-e29b-41d4-a716-446655440000", "budget_id": "550e8400-e29b-41d4-a716-446655440000", "total_amount": 1000.0, "currency": "string", "start_date": "2025-01-01", "end_date": "2025-01-01", "internal_notes": "string", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "owner_id": "550e8400-e29b-41d4-a716-446655440000", "paid_amount": 1000.0, "status": "DRAFT", "rejection_reason": "string", "version": 1, "created_at": "2025-01-01T10:00:00Z", "updated_at": "2025-01-01T10:00:00Z"}
```

### PATCH /api/v1/contracts/:contractId

修改草稿合同。

请求头：`If-Match-Version` - 乐观锁版本；不匹配返回409。

请求：

```json
{"title": "string", "total_amount": 1000.0, "start_date": "2025-01-01", "end_date": "2025-01-01", "internal_notes": "string"}
```

响应 200：

```json
{"contract_no": "string", "title": "string", "department_id": "550e8400-e29b-41d4-a716-446655440000", "vendor_id": "550e8400-e29b-41d4-a716-446655440000", "budget_id": "550e8400-e29b-41d4-a716-446655440000", "total_amount": 1000.0, "currency": "string", "start_date": "2025-01-01", "end_date": "2025-01-01", "internal_notes": "string", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "owner_id": "550e8400-e29b-41d4-a716-446655440000", "paid_amount": 1000.0, "status": "DRAFT", "rejection_reason": "string", "version": 1, "created_at": "2025-01-01T10:00:00Z", "updated_at": "2025-01-01T10:00:00Z"}
```

### POST /api/v1/contracts/:contractId/submit

提交法务审核。

仅DRAFT；至少一个里程碑且里程碑金额合计等于合同总额。

响应 200：

```json
{"contract_no": "string", "title": "string", "department_id": "550e8400-e29b-41d4-a716-446655440000", "vendor_id": "550e8400-e29b-41d4-a716-446655440000", "budget_id": "550e8400-e29b-41d4-a716-446655440000", "total_amount": 1000.0, "currency": "string", "start_date": "2025-01-01", "end_date": "2025-01-01", "internal_notes": "string", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "owner_id": "550e8400-e29b-41d4-a716-446655440000", "paid_amount": 1000.0, "status": "DRAFT", "rejection_reason": "string", "version": 1, "created_at": "2025-01-01T10:00:00Z", "updated_at": "2025-01-01T10:00:00Z"}
```

前置条件：合同状态必须为DRAFT，至少有一个里程碑且里程碑金额合计等于合同总额。

### POST /api/v1/contracts/:contractId/legal-approve

法务批准。

仅legal/admin且合同状态必须为LEGAL_REVIEW。

响应 200：

```json
{"contract_no": "string", "title": "string", "department_id": "550e8400-e29b-41d4-a716-446655440000", "vendor_id": "550e8400-e29b-41d4-a716-446655440000", "budget_id": "550e8400-e29b-41d4-a716-446655440000", "total_amount": 1000.0, "currency": "string", "start_date": "2025-01-01", "end_date": "2025-01-01", "internal_notes": "string", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "owner_id": "550e8400-e29b-41d4-a716-446655440000", "paid_amount": 1000.0, "status": "DRAFT", "rejection_reason": "string", "version": 1, "created_at": "2025-01-01T10:00:00Z", "updated_at": "2025-01-01T10:00:00Z"}
```

前置条件：只有legal或admin角色可操作，合同状态必须为LEGAL_REVIEW。

### POST /api/v1/contracts/:contractId/legal-reject

法务驳回。

请求：

```json
{"reason": "string"}
```

响应 200：

```json
{"contract_no": "string", "title": "string", "department_id": "550e8400-e29b-41d4-a716-446655440000", "vendor_id": "550e8400-e29b-41d4-a716-446655440000", "budget_id": "550e8400-e29b-41d4-a716-446655440000", "total_amount": 1000.0, "currency": "string", "start_date": "2025-01-01", "end_date": "2025-01-01", "internal_notes": "string", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "owner_id": "550e8400-e29b-41d4-a716-446655440000", "paid_amount": 1000.0, "status": "DRAFT", "rejection_reason": "string", "version": 1, "created_at": "2025-01-01T10:00:00Z", "updated_at": "2025-01-01T10:00:00Z"}
```

### POST /api/v1/contracts/:contractId/return-to-draft

驳回合同返回草稿。

响应 200：

```json
{"contract_no": "string", "title": "string", "department_id": "550e8400-e29b-41d4-a716-446655440000", "vendor_id": "550e8400-e29b-41d4-a716-446655440000", "budget_id": "550e8400-e29b-41d4-a716-446655440000", "total_amount": 1000.0, "currency": "string", "start_date": "2025-01-01", "end_date": "2025-01-01", "internal_notes": "string", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "owner_id": "550e8400-e29b-41d4-a716-446655440000", "paid_amount": 1000.0, "status": "DRAFT", "rejection_reason": "string", "version": 1, "created_at": "2025-01-01T10:00:00Z", "updated_at": "2025-01-01T10:00:00Z"}
```

### POST /api/v1/contracts/:contractId/activate

激活合同并预留预算。

仅APPROVED；available减少合同金额，reserved增加同额，预算守恒。

响应 200：

```json
{"contract_no": "string", "title": "string", "department_id": "550e8400-e29b-41d4-a716-446655440000", "vendor_id": "550e8400-e29b-41d4-a716-446655440000", "budget_id": "550e8400-e29b-41d4-a716-446655440000", "total_amount": 1000.0, "currency": "string", "start_date": "2025-01-01", "end_date": "2025-01-01", "internal_notes": "string", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "owner_id": "550e8400-e29b-41d4-a716-446655440000", "paid_amount": 1000.0, "status": "DRAFT", "rejection_reason": "string", "version": 1, "created_at": "2025-01-01T10:00:00Z", "updated_at": "2025-01-01T10:00:00Z"}
```

前置条件：合同状态必须为APPROVED。
副作用：预算available减少合同金额，reserved增加同额。

### POST /api/v1/contracts/:contractId/cancel

取消合同并释放未使用预算。

响应 200：

```json
{"contract_no": "string", "title": "string", "department_id": "550e8400-e29b-41d4-a716-446655440000", "vendor_id": "550e8400-e29b-41d4-a716-446655440000", "budget_id": "550e8400-e29b-41d4-a716-446655440000", "total_amount": 1000.0, "currency": "string", "start_date": "2025-01-01", "end_date": "2025-01-01", "internal_notes": "string", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "owner_id": "550e8400-e29b-41d4-a716-446655440000", "paid_amount": 1000.0, "status": "DRAFT", "rejection_reason": "string", "version": 1, "created_at": "2025-01-01T10:00:00Z", "updated_at": "2025-01-01T10:00:00Z"}
```

副作用：释放未使用预算预留，所有未完成付款申请自动REJECTED。

### POST /api/v1/contracts/:contractId/complete

完成合同。

所有里程碑已验收、已足额付款且无进行中付款时才允许。

响应 200：

```json
{"contract_no": "string", "title": "string", "department_id": "550e8400-e29b-41d4-a716-446655440000", "vendor_id": "550e8400-e29b-41d4-a716-446655440000", "budget_id": "550e8400-e29b-41d4-a716-446655440000", "total_amount": 1000.0, "currency": "string", "start_date": "2025-01-01", "end_date": "2025-01-01", "internal_notes": "string", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "owner_id": "550e8400-e29b-41d4-a716-446655440000", "paid_amount": 1000.0, "status": "DRAFT", "rejection_reason": "string", "version": 1, "created_at": "2025-01-01T10:00:00Z", "updated_at": "2025-01-01T10:00:00Z"}
```

### GET /api/v1/contracts/:contractId/summary

合同金额聚合摘要。

### GET /api/v1/contracts/:contractId/vendor-view

供应商合同视图。

不得返回internal_notes、budget_id和内部审批信息。

## Milestones

### GET /api/v1/contracts/:contractId/milestones

查询合同里程碑。

响应 200：

```json
[{"name": "string", "amount": 1000.0, "due_date": "2025-01-01", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "contract_id": "550e8400-e29b-41d4-a716-446655440000", "accepted_amount": 1000.0, "status": "PENDING", "submission_version": 1, "evidence_url": "string", "version": 1}]
```

### POST /api/v1/contracts/:contractId/milestones

创建里程碑。

请求：

```json
{"name": "string", "amount": 1000.0, "due_date": "2025-01-01"}
```

响应 201：

```json
{"name": "string", "amount": 1000.0, "due_date": "2025-01-01", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "contract_id": "550e8400-e29b-41d4-a716-446655440000", "accepted_amount": 1000.0, "status": "PENDING", "submission_version": 1, "evidence_url": "string", "version": 1}
```

### POST /api/v1/milestones/:milestoneId/submit

提交履约材料。

响应 200：

```json
{"name": "string", "amount": 1000.0, "due_date": "2025-01-01", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "contract_id": "550e8400-e29b-41d4-a716-446655440000", "accepted_amount": 1000.0, "status": "PENDING", "submission_version": 1, "evidence_url": "string", "version": 1}
```

前置条件：合同状态必须为DRAFT，至少有一个里程碑且里程碑金额合计等于合同总额。

### POST /api/v1/milestones/:milestoneId/accept

验收里程碑。

仅SUBMITTED；重复调用不得重复生成验收记录。

请求：

```json
{"accepted_amount": 1000.0, "notes": "string"}
```

响应 200：

```json
{"name": "string", "amount": 1000.0, "due_date": "2025-01-01", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "contract_id": "550e8400-e29b-41d4-a716-446655440000", "accepted_amount": 1000.0, "status": "PENDING", "submission_version": 1, "evidence_url": "string", "version": 1}
```

前置条件：里程碑状态必须为SUBMITTED。
幂等性：重复调用不得重复生成验收记录。

### POST /api/v1/milestones/:milestoneId/reject

驳回履约。

请求：

```json
{"reason": "string"}
```

响应 200：

```json
{"name": "string", "amount": 1000.0, "due_date": "2025-01-01", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "contract_id": "550e8400-e29b-41d4-a716-446655440000", "accepted_amount": 1000.0, "status": "PENDING", "submission_version": 1, "evidence_url": "string", "version": 1}
```

## Finance

### POST /api/v1/invoices

创建发票。

同一供应商发票号唯一；金额非负；total=subtotal+tax。

请求：

```json
{"contract_id": "550e8400-e29b-41d4-a716-446655440000", "invoice_no": "string", "subtotal": 1000.0, "tax_amount": 1000.0, "issue_date": "2025-01-01"}
```

响应 201：

```json
{"contract_id": "550e8400-e29b-41d4-a716-446655440000", "invoice_no": "string", "subtotal": 1000.0, "tax_amount": 1000.0, "issue_date": "2025-01-01", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "vendor_id": "550e8400-e29b-41d4-a716-446655440000", "total_amount": 1000.0, "status": "VALID", "created_by": "550e8400-e29b-41d4-a716-446655440000"}
```

### GET /api/v1/invoices/:invoiceId

查询发票。

响应 200：

```json
{"contract_id": "550e8400-e29b-41d4-a716-446655440000", "invoice_no": "string", "subtotal": 1000.0, "tax_amount": 1000.0, "issue_date": "2025-01-01", "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "vendor_id": "550e8400-e29b-41d4-a716-446655440000", "total_amount": 1000.0, "status": "VALID", "created_by": "550e8400-e29b-41d4-a716-446655440000"}
```

### GET /api/v1/payment-requests

查询付款申请。

响应 200：

```json
[{"contract_id": "550e8400-e29b-41d4-a716-446655440000", "milestone_id": "550e8400-e29b-41d4-a716-446655440000", "invoice_id": "550e8400-e29b-41d4-a716-446655440000", "amount": 1000.0, "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "requested_by": "550e8400-e29b-41d4-a716-446655440000", "status": "DRAFT", "idempotency_key": "string", "manager_approved_by": "550e8400-e29b-41d4-a716-446655440000", "finance_approved_by": "550e8400-e29b-41d4-a716-446655440000", "paid_at": "2025-01-01T10:00:00Z", "rejection_reason": "string", "version": 1}]
```

前置条件：付款申请状态必须为FINANCE_APPROVED。
幂等性：相同Idempotency-Key重复调用不得重复改变资金。
副作用：reserved减少、spent增加、contract.paid增加。

### POST /api/v1/payment-requests

创建付款申请。

合同ACTIVE、里程碑ACCEPTED、发票VALID；金额不得超过剩余额度。

请求：

```json
{"contract_id": "550e8400-e29b-41d4-a716-446655440000", "milestone_id": "550e8400-e29b-41d4-a716-446655440000", "invoice_id": "550e8400-e29b-41d4-a716-446655440000", "amount": 1000.0}
```

响应 201：

```json
{"contract_id": "550e8400-e29b-41d4-a716-446655440000", "milestone_id": "550e8400-e29b-41d4-a716-446655440000", "invoice_id": "550e8400-e29b-41d4-a716-446655440000", "amount": 1000.0, "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "requested_by": "550e8400-e29b-41d4-a716-446655440000", "status": "DRAFT", "idempotency_key": "string", "manager_approved_by": "550e8400-e29b-41d4-a716-446655440000", "finance_approved_by": "550e8400-e29b-41d4-a716-446655440000", "paid_at": "2025-01-01T10:00:00Z", "rejection_reason": "string", "version": 1}
```

前置条件：付款申请状态必须为FINANCE_APPROVED。
幂等性：相同Idempotency-Key重复调用不得重复改变资金。
副作用：reserved减少、spent增加、contract.paid增加。

### GET /api/v1/payment-requests/:paymentId

查询付款申请。

响应 200：

```json
{"contract_id": "550e8400-e29b-41d4-a716-446655440000", "milestone_id": "550e8400-e29b-41d4-a716-446655440000", "invoice_id": "550e8400-e29b-41d4-a716-446655440000", "amount": 1000.0, "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "requested_by": "550e8400-e29b-41d4-a716-446655440000", "status": "DRAFT", "idempotency_key": "string", "manager_approved_by": "550e8400-e29b-41d4-a716-446655440000", "finance_approved_by": "550e8400-e29b-41d4-a716-446655440000", "paid_at": "2025-01-01T10:00:00Z", "rejection_reason": "string", "version": 1}
```

前置条件：付款申请状态必须为FINANCE_APPROVED。
幂等性：相同Idempotency-Key重复调用不得重复改变资金。
副作用：reserved减少、spent增加、contract.paid增加。

### POST /api/v1/payment-requests/:paymentId/manager-approve

业务审批。

响应 200：

```json
{"contract_id": "550e8400-e29b-41d4-a716-446655440000", "milestone_id": "550e8400-e29b-41d4-a716-446655440000", "invoice_id": "550e8400-e29b-41d4-a716-446655440000", "amount": 1000.0, "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "requested_by": "550e8400-e29b-41d4-a716-446655440000", "status": "DRAFT", "idempotency_key": "string", "manager_approved_by": "550e8400-e29b-41d4-a716-446655440000", "finance_approved_by": "550e8400-e29b-41d4-a716-446655440000", "paid_at": "2025-01-01T10:00:00Z", "rejection_reason": "string", "version": 1}
```

前置条件：付款申请状态必须为FINANCE_APPROVED。
幂等性：相同Idempotency-Key重复调用不得重复改变资金。
副作用：reserved减少、spent增加、contract.paid增加。

### POST /api/v1/payment-requests/:paymentId/finance-approve

财务审批。

只能从MANAGER_APPROVED进入FINANCE_APPROVED。

响应 200：

```json
{"contract_id": "550e8400-e29b-41d4-a716-446655440000", "milestone_id": "550e8400-e29b-41d4-a716-446655440000", "invoice_id": "550e8400-e29b-41d4-a716-446655440000", "amount": 1000.0, "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "requested_by": "550e8400-e29b-41d4-a716-446655440000", "status": "DRAFT", "idempotency_key": "string", "manager_approved_by": "550e8400-e29b-41d4-a716-446655440000", "finance_approved_by": "550e8400-e29b-41d4-a716-446655440000", "paid_at": "2025-01-01T10:00:00Z", "rejection_reason": "string", "version": 1}
```

前置条件：付款申请状态必须为FINANCE_APPROVED。
幂等性：相同Idempotency-Key重复调用不得重复改变资金。
副作用：reserved减少、spent增加、contract.paid增加。

### POST /api/v1/payment-requests/:paymentId/pay

执行付款。

仅FINANCE_APPROVED。相同Idempotency-Key重复调用不得重复改变资金。

请求头：`Idempotency-Key` - 

响应 200：

```json
{"contract_id": "550e8400-e29b-41d4-a716-446655440000", "milestone_id": "550e8400-e29b-41d4-a716-446655440000", "invoice_id": "550e8400-e29b-41d4-a716-446655440000", "amount": 1000.0, "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "requested_by": "550e8400-e29b-41d4-a716-446655440000", "status": "DRAFT", "idempotency_key": "string", "manager_approved_by": "550e8400-e29b-41d4-a716-446655440000", "finance_approved_by": "550e8400-e29b-41d4-a716-446655440000", "paid_at": "2025-01-01T10:00:00Z", "rejection_reason": "string", "version": 1}
```

前置条件：付款申请状态必须为FINANCE_APPROVED。
幂等性：相同Idempotency-Key重复调用不得重复改变资金。
副作用：reserved减少、spent增加、contract.paid增加。

### POST /api/v1/payment-requests/:paymentId/reject

驳回付款申请。

请求：

```json
{"reason": "string"}
```

响应 200：

```json
{"contract_id": "550e8400-e29b-41d4-a716-446655440000", "milestone_id": "550e8400-e29b-41d4-a716-446655440000", "invoice_id": "550e8400-e29b-41d4-a716-446655440000", "amount": 1000.0, "id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "requested_by": "550e8400-e29b-41d4-a716-446655440000", "status": "DRAFT", "idempotency_key": "string", "manager_approved_by": "550e8400-e29b-41d4-a716-446655440000", "finance_approved_by": "550e8400-e29b-41d4-a716-446655440000", "paid_at": "2025-01-01T10:00:00Z", "rejection_reason": "string", "version": 1}
```

前置条件：付款申请状态必须为FINANCE_APPROVED。
幂等性：相同Idempotency-Key重复调用不得重复改变资金。
副作用：reserved减少、spent增加、contract.paid增加。

### GET /api/v1/budgets

查询当前租户预算。

响应 200：

```json
[{"id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "department_id": "550e8400-e29b-41d4-a716-446655440000", "fiscal_year": 1, "total_amount": 1000.0, "available_amount": 1000.0, "reserved_amount": 1000.0, "spent_amount": 1000.0, "version": 1}]
```

### GET /api/v1/budgets/:budgetId

查询预算。

响应 200：

```json
{"id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "department_id": "550e8400-e29b-41d4-a716-446655440000", "fiscal_year": 1, "total_amount": 1000.0, "available_amount": 1000.0, "reserved_amount": 1000.0, "spent_amount": 1000.0, "version": 1}
```

## Audit

### GET /api/v1/audit-logs

查询审计日志。

响应 200：

```json
[{"id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "actor_id": "550e8400-e29b-41d4-a716-446655440000", "entity_type": "string", "entity_id": "550e8400-e29b-41d4-a716-446655440000", "action": "string", "before_data": null, "after_data": null, "correlation_id": "string", "created_at": "2025-01-01T10:00:00Z"}]
```

## Reference

### GET /api/v1/reference/departments

当前租户部门。

### GET /api/v1/reference/vendors

当前租户供应商。

## Auth

### POST /api/v1/auth/login

登录并获取固定测试Token。

请求：

```json
{"email": "user@example.com", "password": "string"}
```

响应 200：

```json
{"token": "string", "user_id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "role": "string", "full_name": "string"}
```

### GET /api/v1/auth/me

当前用户。

响应 200：

```json
{"id": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "550e8400-e29b-41d4-a716-446655440000", "department_id": "550e8400-e29b-41d4-a716-446655440000", "email": "string", "full_name": "string", "role": "string"}
```
