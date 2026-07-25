# MES User Roles and Permissions

## 1. Role Definitions

| Role | Code | Description | Scope |
|------|------|-------------|-------|
| Production Planner | PLANNER | Creates production plans, work orders, manages scheduling | Org-bound |
| Shop Floor Operator | OPERATOR | Executes operations, reports output, picks materials | Org + Factory bound |
| Quality Inspector | INSPECTOR | Performs inspections, creates rework orders | Org-bound |
| Production Manager | MANAGER | Full oversight, approvals, closings | Org-bound |
| Warehouse Keeper | WAREHOUSE | Material issuing, finished goods receipt | Org-bound |
| Administrator | ADMIN | Cross-org access, system configuration | Global |

---

## 2. Permission Matrix

### 2.1 Master Data Management

| Operation | PLANNER | OPERATOR | INSPECTOR | MANAGER | WAREHOUSE | ADMIN |
|-----------|:-------:|:--------:|:---------:|:-------:|:---------:|:-----:|
| View Products | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Create Product | ✓ | ✗ | ✗ | ✓ | ✗ | ✓ |
| Modify Product Name | ✓ | ✗ | ✗ | ✓ | ✗ | ✓ |
| Modify Product Cost | ✗ | ✗ | ✗ | ✓ | ✗ | ✓ |
| View BOM | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Create/Modify BOM | ✓ | ✗ | ✗ | ✓ | ✗ | ✓ |
| Delete BOM | ✗ | ✗ | ✗ | ✓ | ✗ | ✓ |
| View Work Centers | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Create Work Centers | ✗ | ✗ | ✗ | ✓ | ✗ | ✓ |
| View Routings | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Create/Modify Routing | ✓ | ✗ | ✗ | ✓ | ✗ | ✓ |

### 2.2 Order & Planning

| Operation | PLANNER | OPERATOR | INSPECTOR | MANAGER | WAREHOUSE | ADMIN |
|-----------|:-------:|:--------:|:---------:|:-------:|:---------:|:-----:|
| Create Sales Order | ✓ | ✗ | ✗ | ✓ | ✗ | ✓ |
| Modify Sales Order | ✓ | ✗ | ✗ | ✓ | ✗ | ✓ |
| Create Production Plan | ✓ | ✗ | ✗ | ✓ | ✗ | ✓ |
| Confirm Production Plan | ✓ | ✗ | ✗ | ✓ | ✗ | ✓ |
| Modify Production Plan | ✓ | ✗ | ✗ | ✓ | ✗ | ✓ |

### 2.3 Work Order Lifecycle

| Operation | PLANNER | OPERATOR | INSPECTOR | MANAGER | WAREHOUSE | ADMIN |
|-----------|:-------:|:--------:|:---------:|:-------:|:---------:|:-----:|
| Create Work Order | ✓ | ✗ | ✗ | ✓ | ✗ | ✓ |
| Release Work Order | ✓ | ✗ | ✗ | ✓ | ✗ | ✓ |
| Start Work Order | ✗ | ✓ | ✗ | ✓ | ✗ | ✓ |
| Complete Work Order | ✗ | ✓ | ✗ | ✓ | ✗ | ✓ |
| Close Work Order | ✗ | ✗ | ✗ | ✓ | ✗ | ✓ |
| Cancel Work Order | ✓ | ✗ | ✗ | ✓ | ✗ | ✓ |
| Delete Work Order | ✗ | ✗ | ✗ | ✓ | ✗ | ✓ |
| Bulk Release | ✓ | ✗ | ✗ | ✓ | ✗ | ✓ |

### 2.4 Material Management

| Operation | PLANNER | OPERATOR | INSPECTOR | MANAGER | WAREHOUSE | ADMIN |
|-----------|:-------:|:--------:|:---------:|:-------:|:---------:|:-----:|
| Create Reservation | ✓ | ✗ | ✗ | ✓ | ✓ | ✓ |
| Release Reservation | ✓ | ✗ | ✗ | ✓ | ✓ | ✓ |
| Create Material Issue | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| Pick Material | ✗ | ✓ | ✗ | ✓ | ✓ | ✓ |
| Return Material | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| Bulk Issue | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |

### 2.5 Production Execution

| Operation | PLANNER | OPERATOR | INSPECTOR | MANAGER | WAREHOUSE | ADMIN |
|-----------|:-------:|:--------:|:---------:|:-------:|:---------:|:-----:|
| Create Work Report | ✗ | ✓ | ✗ | ✓ | ✗ | ✓ |
| Create Inspection | ✗ | ✗ | ✓ | ✓ | ✗ | ✓ |
| Start Inspection | ✗ | ✗ | ✓ | ✓ | ✗ | ✓ |
| Submit Inspection | ✗ | ✗ | ✓ | ✓ | ✗ | ✓ |
| Create Rework Order | ✗ | ✗ | ✓ | ✓ | ✗ | ✓ |
| Start/Complete Rework | ✗ | ✓ | ✗ | ✓ | ✗ | ✓ |

### 2.6 Finished Goods

| Operation | PLANNER | OPERATOR | INSPECTOR | MANAGER | WAREHOUSE | ADMIN |
|-----------|:-------:|:--------:|:---------:|:-------:|:---------:|:-----:|
| Create Receipt | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| Confirm Receipt | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |

---

## 3. Scope Rules

### 3.1 Organization Isolation
- Every entity belongs to an organization (org field)
- Users can only access entities in their own org
- Exception: ADMIN role has cross-org visibility

### 3.2 Factory Scope
- Operators are bound to a specific factory
- Work reports require operator.factory == work_order.factory
- Work centers belong to a specific factory
- Work orders are executed in a specific factory

### 3.3 Cross-Org Prevention
- PLANNER in acme cannot see globex work orders
- OPERATOR in fac-001 cannot report for fac-002 work orders
- WAREHOUSE in acme cannot issue materials for globex work orders

---

## 4. Role Hierarchy Notes

- MANAGER inherits all permissions within their org
- ADMIN inherits all permissions across all orgs
- No implicit hierarchy between PLANNER, OPERATOR, INSPECTOR, WAREHOUSE
- Permissions are additive only; no negative permissions
