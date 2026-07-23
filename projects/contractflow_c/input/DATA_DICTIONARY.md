# 数据字典

## 通用约定

- 主键：UUID；
- 金额：`NUMERIC(18,2)`；
- 时间：`TIMESTAMPTZ`；
- 多租户字段：`tenant_id`；
- 乐观锁字段：`version`；
- 状态字段：大写枚举字符串。

## 核心表

### tenants

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 租户ID |
| code | VARCHAR(32) | 唯一租户编码 |
| name | VARCHAR(128) | 租户名称 |

### users

| 字段 | 类型 | 语义 |
|---|---|---|
| id | UUID | IDENTITY |
| tenant_id | UUID | TENANT |
| department_id | UUID | FOREIGN_KEY |
| email | VARCHAR | IDENTITY/LOGIN |
| password | VARCHAR | 测试环境明文密码 |
| role | VARCHAR | ENUM/ROLE |
| api_token | VARCHAR | AUTH TOKEN |
| active | BOOLEAN | BOOLEAN_FLAG |

### budgets

| 字段 | 类型 | 语义 |
|---|---|---|
| total_amount | NUMERIC | AMOUNT_BALANCE |
| available_amount | NUMERIC | AMOUNT_BALANCE |
| reserved_amount | NUMERIC | AMOUNT_BALANCE |
| spent_amount | NUMERIC | AMOUNT_BALANCE |
| version | INTEGER | VERSION |

### contracts

| 字段 | 类型 | 语义 |
|---|---|---|
| contract_no | VARCHAR | BUSINESS_IDENTITY |
| owner_id | UUID | OWNER |
| tenant_id | UUID | TENANT |
| vendor_id | UUID | FOREIGN_KEY |
| department_id | UUID | FOREIGN_KEY |
| budget_id | UUID | FOREIGN_KEY |
| total_amount | NUMERIC | AMOUNT_BALANCE |
| paid_amount | NUMERIC | AMOUNT_BALANCE |
| status | VARCHAR | STATE |
| start_date/end_date | DATE | TEMPORAL |
| internal_notes | TEXT | INTERNAL_SENSITIVE |
| version | INTEGER | VERSION |

### milestones

| 字段 | 类型 | 语义 |
|---|---|---|
| contract_id | UUID | FOREIGN_KEY |
| amount | NUMERIC | AMOUNT_BALANCE |
| accepted_amount | NUMERIC | AMOUNT_BALANCE |
| due_date | DATE | TEMPORAL |
| status | VARCHAR | STATE |
| submission_version | INTEGER | VERSION/SEQUENCE |

### invoices

| 字段 | 类型 | 语义 |
|---|---|---|
| invoice_no | VARCHAR | BUSINESS_IDENTITY |
| subtotal | NUMERIC | AMOUNT_BALANCE |
| tax_amount | NUMERIC | AMOUNT_BALANCE |
| total_amount | NUMERIC | AMOUNT_BALANCE |
| issue_date | DATE | TEMPORAL |
| status | VARCHAR | STATE |

### payment_requests

| 字段 | 类型 | 语义 |
|---|---|---|
| amount | NUMERIC | AMOUNT_BALANCE |
| status | VARCHAR | STATE |
| idempotency_key | VARCHAR | IDEMPOTENCY_KEY |
| requested_by | UUID | OWNER |
| paid_at | TIMESTAMPTZ | TEMPORAL |
| version | INTEGER | VERSION |

### audit_logs

审计记录只允许插入和读取，不提供更新或删除API。
