# MES Test Accounts

## Authentication Method
All API calls use Bearer token authentication:
```
Authorization: Bearer <token>
```

---

## Account List

### Planners (PLANNER)

| Name | ID | Org | Factory | Token |
|------|----|-----|---------|-------|
| Pat Zhang | pln-001 | acme | fac-001 | planner-pat-token |
| Pam Xu | pln-002 | globex | fac-003 | planner-pam-token |

**Capabilities:** Create/manage sales orders, production plans, work orders, BOMs, routings, material reservations. Release and cancel work orders.

---

### Operators (OPERATOR)

| Name | ID | Org | Factory | Token |
|------|----|-----|---------|-------|
| Oli Chen | opr-001 | acme | fac-001 | operator-oli-token |
| Ole Wu | opr-002 | acme | fac-002 | operator-ole-token |
| Ova Li | opr-003 | globex | fac-003 | operator-ova-token |

**Capabilities:** Start/complete work orders, create work reports, pick materials, start/complete rework.
**Scope Constraint:** Can only operate within their assigned factory.

---

### Quality Inspectors (INSPECTOR)

| Name | ID | Org | Factory | Token |
|------|----|-----|---------|-------|
| Iris Wang | ins-001 | acme | fac-001 | inspector-iris-token |
| Ivan Zhao | ins-002 | globex | fac-003 | inspector-ivan-token |

**Capabilities:** Create/start/submit quality inspections, create rework orders.

---

### Production Managers (MANAGER)

| Name | ID | Org | Factory | Token |
|------|----|-----|---------|-------|
| Marcus Sun | mgr-001 | acme | fac-001 | manager-marcus-token |
| Mona Huang | mgr-002 | globex | fac-003 | manager-mona-token |

**Capabilities:** Full access within org - all planner permissions plus close work orders, delete work orders/BOMs, create work centers, modify product costs, manage material issues, create/confirm receipts.

---

### Warehouse Keepers (WAREHOUSE)

| Name | ID | Org | Factory | Token |
|------|----|-----|---------|-------|
| Will Zhou | whk-001 | acme | fac-001 | warehouse-will-token |
| Wanda Yang | whk-002 | globex | fac-003 | warehouse-wanda-token |

**Capabilities:** Create material reservations, create/pick/return material issues, create/confirm finished goods receipts, bulk issue.

---

### Administrators (ADMIN)

| Name | ID | Org | Factory | Token |
|------|----|-----|---------|-------|
| Arthur Liu | adm-001 | acme | (all) | admin-arthur-token |

**Capabilities:** All permissions across all organizations. Cross-org data access.

---

## Test Scenarios by Account

### Cross-Org Isolation Test
- Use `planner-pat-token` (acme) to access globex entities → should be denied
- Use `admin-arthur-token` to access both orgs → should succeed

### Factory Scope Test
- Use `operator-oli-token` (fac-001) to report on fac-002 work order → should be denied
- Use `operator-ole-token` (fac-002) to report on fac-001 work order → should be denied

### Role Escalation Test
- Use `operator-oli-token` to create products → should be denied (403)
- Use `warehouse-will-token` to release work orders → should be denied (403)
- Use `inspector-iris-token` to create work orders → should be denied (403)

---

## Quick Reference

```bash
# Health check (no auth)
curl http://localhost:8020/health

# List products as planner
curl -H "Authorization: Bearer planner-pat-token" http://localhost:8020/products

# Create work report as operator
curl -X POST -H "Authorization: Bearer operator-oli-token" \
  -H "Content-Type: application/json" \
  -d '{"work_order_id":"wo-001","operation_id":"woo-001","quantity":10}' \
  http://localhost:8020/work-reports
```
