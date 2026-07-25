# MES API Specification

## Base URL
```
http://localhost:8020
```

## Authentication
All endpoints (except `/health` and `/reset`) require Bearer token authentication:
```
Authorization: Bearer <token>
```

## Response Format
- Success: `200 OK` with JSON body
- Created: `201 Created` with JSON body
- Client Error: `400/401/403/404/409` with `{"error": "message"}`

---

## 1. Health Check

### GET /health
Returns service health status. No auth required.

**Response:** `{"status": "healthy", "service": "mes"}`

---

## 2. Products (Materials)

### GET /products
List all products/materials. Supports `?category=` filter.

**Response:** `{"products": [...], "total": N}`

### GET /products/{id}
Get single product by ID.

### POST /products
Create a new product/material.

**Request Body:**
```json
{
  "sku": "string (required, unique)",
  "name": "string (required)",
  "category": "RAW_MATERIAL | COMPONENT | FINISHED_GOODS (required)",
  "unit": "string (required)",
  "unit_cost": "number (optional)"
}
```

**Roles:** PLANNER, MANAGER, ADMIN

### PUT /products/{id}
Update product fields (name, unit_cost, status).

**Roles:** PLANNER, MANAGER, ADMIN (cost modification: MANAGER, ADMIN only)

---

## 3. Bill of Materials (BOM)

### GET /boms
List all BOMs.

### GET /boms/{id}
Get single BOM.

### GET /boms/{id}/lines
Get BOM lines for a BOM.

### GET /boms/{id}/expand
Expand BOM with material details (calculated requirements).

**Response:** `{"bom": {...}, "expanded_lines": [{"material_id", "material_name", "quantity_per_unit", "scrap_factor", "unit"}]}`

### POST /boms
Create a new BOM.

**Request Body:**
```json
{
  "product_id": "string (required)",
  "version": "string (required)"
}
```

**Roles:** PLANNER, MANAGER, ADMIN

### POST /boms/{id}/lines
Add a line to BOM.

**Request Body:**
```json
{
  "material_id": "string (required)",
  "quantity_per_unit": "number (required)",
  "unit": "string (required)",
  "scrap_factor": "number (default 0)"
}
```

### DELETE /boms/{id}
Delete a BOM and all its lines.

**Roles:** MANAGER, ADMIN

### DELETE /bom-lines/{id}
Delete a single BOM line.

---

## 4. Work Centers

### GET /work-centers
List work centers.

### GET /work-centers/{id}
Get single work center.

### POST /work-centers
Create a work center.

**Request Body:**
```json
{
  "name": "string (required)",
  "factory": "string (required)",
  "capacity_hours_per_day": "number (required)"
}
```

**Roles:** MANAGER, ADMIN

---

## 5. Routings (Process Routes)

### GET /routings
List all routings.

### GET /routings/{id}
Get single routing.

### GET /routings/{id}/steps
Get routing steps.

### POST /routings
Create a routing.

**Request Body:**
```json
{
  "product_id": "string (required)",
  "version": "string (required)"
}
```

**Roles:** PLANNER, MANAGER, ADMIN

### POST /routings/{id}/steps
Add a step to routing.

**Request Body:**
```json
{
  "seq": "number (required)",
  "name": "string (required)",
  "work_center_id": "string (required)",
  "setup_time_min": "number",
  "run_time_min_per_unit": "number"
}
```

---

## 6. Sales Orders

### GET /sales-orders
List all sales orders.

### GET /sales-orders/{id}
Get single sales order.

### POST /sales-orders
Create a sales order.

**Request Body:**
```json
{
  "order_ref": "string (required, unique - idempotency key)",
  "customer": "string (required)",
  "product_id": "string (required)",
  "quantity": "number (required)",
  "delivery_date": "string YYYY-MM-DD (required)"
}
```

**Roles:** PLANNER, MANAGER, ADMIN
**Idempotency:** Duplicate `order_ref` returns 409 Conflict.

### PUT /sales-orders/{id}
Update sales order (delivery_date, quantity).

**Constraint:** Cannot modify delivery_date if a linked Production Plan is CONFIRMED.

---

## 7. Production Plans

### GET /production-plans
List all production plans.

### GET /production-plans/{id}
Get single production plan.

### POST /production-plans
Create a production plan.

**Request Body:**
```json
{
  "sales_order_id": "string (required)",
  "factory": "string (required)",
  "product_id": "string (required)",
  "planned_quantity": "number (required)",
  "planned_start": "string YYYY-MM-DD (required)",
  "planned_end": "string YYYY-MM-DD (required)"
}
```

**Roles:** PLANNER, MANAGER, ADMIN

### PUT /production-plans/{id}
Update production plan. **Only allowed when status = CREATED.**

### POST /production-plans/{id}/confirm
Confirm a production plan (CREATED → CONFIRMED).

**Roles:** PLANNER, MANAGER, ADMIN
**Effect:** Plan becomes immutable after confirmation.

---

## 8. Work Orders

### GET /work-orders
List work orders. Supports `?status=` and `?production_plan_id=` filters.

### GET /work-orders/{id}
Get single work order.

### GET /work-orders/{id}/operations
Get operations for a work order.

### POST /work-orders
Create a work order.

**Request Body:**
```json
{
  "order_ref": "string (required)",
  "production_plan_id": "string (optional)",
  "product_id": "string (required)",
  "bom_id": "string (required)",
  "routing_id": "string (required)",
  "factory": "string (required)",
  "planned_quantity": "number (required)",
  "priority": "number (default 1)",
  "planned_start": "string YYYY-MM-DD",
  "planned_end": "string YYYY-MM-DD"
}
```

**Roles:** PLANNER, MANAGER, ADMIN
**Preconditions:** Product must have active BOM and Routing.

### PUT /work-orders/{id}
Update work order. Requires `version` field for optimistic locking.

**Request Body:** `{"version": N, ...fields}`
**Conflict:** Returns 409 if version mismatch.

### POST /work-orders/{id}/release
Release work order (CREATED → RELEASED).

**Roles:** PLANNER, MANAGER, ADMIN

### POST /work-orders/{id}/start
Start work order (RELEASED → IN_PRODUCTION).

**Roles:** OPERATOR, MANAGER, ADMIN
**Precondition:** All material reservations must be RESERVED.

### POST /work-orders/{id}/complete
Complete work order (IN_PRODUCTION → COMPLETED).

**Roles:** OPERATOR, MANAGER, ADMIN
**Precondition:** All operations must be COMPLETED.

### POST /work-orders/{id}/close
Close work order (COMPLETED → CLOSED).

**Roles:** MANAGER, ADMIN

### POST /work-orders/{id}/cancel
Cancel work order (CREATED/RELEASED/IN_PRODUCTION → CANCELLED).

**Roles:** PLANNER, MANAGER, ADMIN
**Compensation:** Must release all material reservations.

### DELETE /work-orders/{id}
Delete a work order (only CREATED status).

**Roles:** MANAGER, ADMIN

### POST /work-orders/bulk-release
Bulk release multiple work orders.

**Request Body:** `{"work_order_ids": ["id1", "id2", ...]}`
**Semantics:** All-or-nothing. If any fails, all roll back.

---

## 9. Material Reservations

### GET /material-reservations
List reservations. Supports `?work_order_id=` filter.

### GET /material-reservations/{id}
Get single reservation.

### POST /material-reservations
Create a material reservation.

**Request Body:**
```json
{
  "work_order_id": "string (required)",
  "material_id": "string (required)",
  "required_quantity": "number (required)"
}
```

**Roles:** PLANNER, MANAGER, WAREHOUSE, ADMIN

### POST /material-reservations/{id}/release
Release a reservation (RESERVED → RELEASED).

---

## 10. Material Issues

### GET /material-issues
List material issues. Supports `?work_order_id=` filter.

### GET /material-issues/{id}
Get single material issue.

### POST /material-issues
Create a material issue.

**Request Body:**
```json
{
  "reservation_id": "string (required)",
  "work_order_id": "string (required)",
  "material_id": "string (required)",
  "quantity": "number (required)"
}
```

**Roles:** MANAGER, WAREHOUSE, ADMIN
**Constraint:** quantity must not exceed reserved_quantity - issued_quantity.

### POST /material-issues/{id}/pick
Pick material (CREATED → PICKED).

**Roles:** OPERATOR, MANAGER, WAREHOUSE, ADMIN

### POST /material-issues/{id}/return
Return material (PICKED → RETURNED).

**Roles:** MANAGER, WAREHOUSE, ADMIN
**Version Control:** Requires version check.

### POST /material-issues/bulk-issue
Bulk create material issues.

**Request Body:** `{"items": [{...}, {...}]}`
**Semantics:** Atomic. If any fails, none created.

---

## 11. Work Reports

### GET /work-reports
List work reports. Supports `?work_order_id=` filter.

### GET /work-reports/{id}
Get single work report.

### POST /work-reports
Create a work report (operator reporting production output).

**Request Body:**
```json
{
  "work_order_id": "string (required)",
  "operation_id": "string (required)",
  "quantity": "number (required)",
  "defect_quantity": "number (default 0)",
  "shift": "DAY | NIGHT (optional)"
}
```

**Roles:** OPERATOR, MANAGER, ADMIN
**Constraints:**
- Operator factory must match work order factory
- Reported quantity must not exceed remaining planned quantity

---

## 12. Quality Inspections

### GET /quality-inspections
List inspections. Supports `?work_order_id=` filter.

### GET /quality-inspections/{id}
Get single inspection.

### POST /quality-inspections
Create a quality inspection.

**Request Body:**
```json
{
  "work_order_id": "string (required)",
  "operation_id": "string (required)",
  "inspection_type": "IN_PROCESS | FINAL (required)",
  "sample_size": "number (required)",
  "expiry_date": "string YYYY-MM-DD (required)"
}
```

**Roles:** INSPECTOR, MANAGER, ADMIN

### POST /quality-inspections/{id}/start
Start inspection (PENDING → IN_PROGRESS).

### POST /quality-inspections/{id}/submit
Submit inspection result (IN_PROGRESS → COMPLETED).

**Request Body:**
```json
{
  "pass_quantity": "number (required)",
  "fail_quantity": "number (required)",
  "result": "PASS | REJECT (required)"
}
```

**Constraints:**
- pass_quantity + fail_quantity must equal sample_size
- Must be submitted before expiry_date

---

## 13. Rework Orders

### GET /rework-orders
List rework orders.

### GET /rework-orders/{id}
Get single rework order.

### POST /rework-orders
Create a rework order.

**Request Body:**
```json
{
  "inspection_id": "string (required)",
  "work_order_id": "string (required)",
  "quantity": "number (required)",
  "reason": "string"
}
```

**Roles:** INSPECTOR, MANAGER, ADMIN
**Precondition:** Referenced inspection must have result = REJECT.

### POST /rework-orders/{id}/start
Start rework (CREATED → IN_PROGRESS).

### POST /rework-orders/{id}/complete
Complete rework (IN_PROGRESS → COMPLETED).

---

## 14. Finished Goods Receipts

### GET /finished-goods-receipts
List receipts.

### GET /finished-goods-receipts/{id}
Get single receipt.

### POST /finished-goods-receipts
Create a finished goods receipt.

**Request Body:**
```json
{
  "work_order_id": "string (required)",
  "quantity": "number (required)",
  "warehouse_location": "string"
}
```

**Roles:** MANAGER, WAREHOUSE, ADMIN
**Preconditions:**
- Work Order must be COMPLETED
- At least one Quality Inspection with PASS result
- No duplicate receipt for same work_order_id (409 Conflict)

### POST /finished-goods-receipts/{id}/confirm
Confirm receipt (CREATED → CONFIRMED).

**Effect:** Updates work order completed_quantity.

---

## 15. System

### POST /reset
Reset all data to initial state. No auth required.

---

## Error Codes

| Code | Meaning |
|------|---------|
| 400 | Bad Request - validation error |
| 401 | Unauthorized - invalid/missing token |
| 403 | Forbidden - insufficient role |
| 404 | Not Found - entity doesn't exist |
| 409 | Conflict - idempotency/version violation |
