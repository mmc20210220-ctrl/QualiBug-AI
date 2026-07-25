# MES Database Schema

## Overview
The MES system uses an in-memory data store with the following entity schemas. All entities use string IDs with entity-specific prefixes.

---

## 1. products

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: mat- | Material/product ID |
| sku | string | UNIQUE | Stock keeping unit code |
| name | string | NOT NULL | Display name |
| org | string | NOT NULL | Organization (acme/globex) |
| category | enum | NOT NULL | RAW_MATERIAL, COMPONENT, FINISHED_GOODS |
| unit | string | NOT NULL | Unit of measure (kg, pcs) |
| unit_cost | number | ≥0 | Cost per unit |
| status | enum | NOT NULL | ACTIVE, INACTIVE |
| created_at | datetime | NOT NULL | Creation timestamp |

---

## 2. boms

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: bom- | BOM ID |
| product_id | string | FK → products.id | Finished product |
| org | string | NOT NULL | Organization |
| version | string | NOT NULL | BOM version |
| status | enum | NOT NULL | ACTIVE, INACTIVE |
| created_at | datetime | NOT NULL | Creation timestamp |

---

## 3. bom_lines

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: bl- | BOM line ID |
| bom_id | string | FK → boms.id | Parent BOM |
| material_id | string | FK → products.id | Component material |
| quantity_per_unit | number | >0 | Quantity needed per finished unit |
| unit | string | NOT NULL | Unit of measure |
| scrap_factor | number | ≥0 | Expected scrap ratio (0.02 = 2%) |

---

## 4. work_centers

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: wc- | Work center ID |
| name | string | NOT NULL | Display name |
| org | string | NOT NULL | Organization |
| factory | string | NOT NULL | Factory (fac-001/002/003) |
| capacity_hours_per_day | number | >0 | Daily capacity |
| status | enum | NOT NULL | ACTIVE, INACTIVE |
| created_at | datetime | NOT NULL | Creation timestamp |

---

## 5. routings

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: rt- | Routing ID |
| product_id | string | FK → products.id | Product this routing produces |
| org | string | NOT NULL | Organization |
| version | string | NOT NULL | Routing version |
| status | enum | NOT NULL | ACTIVE, INACTIVE |
| created_at | datetime | NOT NULL | Creation timestamp |

---

## 6. routing_steps

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: rs- | Step ID |
| routing_id | string | FK → routings.id | Parent routing |
| seq | number | NOT NULL | Sequence (10, 20, 30...) |
| name | string | NOT NULL | Operation name |
| work_center_id | string | FK → work_centers.id | Assigned work center |
| setup_time_min | number | ≥0 | Setup time in minutes |
| run_time_min_per_unit | number | ≥0 | Run time per unit |

---

## 7. sales_orders

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: so- | Sales order ID |
| order_ref | string | UNIQUE | Customer order reference (idempotency key) |
| customer | string | NOT NULL | Customer name |
| org | string | NOT NULL | Organization |
| product_id | string | FK → products.id | Ordered product |
| quantity | number | >0 | Ordered quantity |
| delivery_date | date | NOT NULL | Required delivery date |
| status | enum | NOT NULL | CREATED, CONFIRMED |
| created_at | datetime | NOT NULL | Creation timestamp |
| version | number | ≥1 | Optimistic lock version |

---

## 8. production_plans

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: pp- | Plan ID |
| sales_order_id | string | FK → sales_orders.id | Source sales order |
| org | string | NOT NULL | Organization |
| factory | string | NOT NULL | Production factory |
| product_id | string | FK → products.id | Product to manufacture |
| planned_quantity | number | >0 | Planned output quantity |
| planned_start | date | NOT NULL | Start date |
| planned_end | date | NOT NULL | End date (> planned_start) |
| status | enum | NOT NULL | CREATED, CONFIRMED |
| created_by | string | FK → users.id | Creator user ID |
| created_at | datetime | NOT NULL | Creation timestamp |
| version | number | ≥1 | Optimistic lock version |

---

## 9. work_orders

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: wo- | Work order ID |
| order_ref | string | NOT NULL | WO reference number |
| production_plan_id | string | FK → production_plans.id | Parent plan (nullable) |
| product_id | string | FK → products.id | Product to produce |
| bom_id | string | FK → boms.id | BOM to use |
| routing_id | string | FK → routings.id | Routing to use |
| org | string | NOT NULL | Organization |
| factory | string | NOT NULL | Production factory |
| planned_quantity | number | >0 | Planned output |
| completed_quantity | number | ≥0 | Completed output |
| status | enum | NOT NULL | CREATED, RELEASED, IN_PRODUCTION, COMPLETED, CLOSED, CANCELLED |
| priority | number | ≥1 | Priority (1=highest) |
| planned_start | date | | Planned start |
| planned_end | date | | Planned end |
| created_by | string | | Creator user ID |
| created_at | datetime | NOT NULL | Creation timestamp |
| released_at | datetime | nullable | Release timestamp |
| version | number | ≥1 | Optimistic lock version |

---

## 10. work_order_operations

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: woo- | Operation ID |
| work_order_id | string | FK → work_orders.id | Parent work order |
| routing_step_id | string | FK → routing_steps.id | Source routing step |
| seq | number | NOT NULL | Sequence |
| name | string | NOT NULL | Operation name |
| work_center_id | string | FK → work_centers.id | Work center |
| status | enum | NOT NULL | PENDING, IN_PROGRESS, COMPLETED |
| reported_quantity | number | ≥0 | Total reported output |
| started_at | datetime | nullable | Start time |
| completed_at | datetime | nullable | Completion time |

---

## 11. material_reservations

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: mr- | Reservation ID |
| work_order_id | string | FK → work_orders.id | Work order |
| material_id | string | FK → products.id | Reserved material |
| required_quantity | number | >0 | Calculated requirement |
| reserved_quantity | number | >0 | Actually reserved |
| issued_quantity | number | ≥0 | Total issued so far |
| org | string | NOT NULL | Organization |
| status | enum | NOT NULL | RESERVED, RELEASED |
| created_at | datetime | NOT NULL | Creation timestamp |

---

## 12. material_issues

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: mi- | Issue ID |
| reservation_id | string | FK → material_reservations.id | Source reservation |
| work_order_id | string | FK → work_orders.id | Work order |
| material_id | string | FK → products.id | Material |
| quantity | number | >0 | Issue quantity |
| org | string | NOT NULL | Organization |
| status | enum | NOT NULL | CREATED, PICKED, RETURNED |
| issued_by | string | | Issuer user ID |
| created_at | datetime | NOT NULL | Creation timestamp |
| version | number | ≥1 | Version for return control |

---

## 13. work_reports

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: wr- | Report ID |
| work_order_id | string | FK → work_orders.id | Work order |
| operation_id | string | FK → work_order_operations.id | Operation |
| quantity | number | >0 | Good output quantity |
| defect_quantity | number | ≥0 | Defective quantity |
| shift | enum | | DAY, NIGHT |
| reported_by | string | | Reporter user ID |
| org | string | NOT NULL | Organization |
| created_at | datetime | NOT NULL | Report timestamp |

---

## 14. quality_inspections

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: qi- | Inspection ID |
| work_order_id | string | FK → work_orders.id | Work order |
| operation_id | string | FK → work_order_operations.id | Inspected operation |
| inspection_type | enum | NOT NULL | IN_PROCESS, FINAL |
| sample_size | number | >0 | Sampled quantity |
| pass_quantity | number | ≥0 | Passed count |
| fail_quantity | number | ≥0 | Failed count |
| result | enum | nullable | PASS, REJECT |
| expiry_date | date | NOT NULL | Must submit before this date |
| status | enum | NOT NULL | PENDING, IN_PROGRESS, COMPLETED |
| inspected_by | string | | Inspector user ID |
| org | string | NOT NULL | Organization |
| created_at | datetime | NOT NULL | Creation timestamp |

**Invariant:** pass_quantity + fail_quantity = sample_size (when COMPLETED)

---

## 15. rework_orders

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: rw- | Rework order ID |
| inspection_id | string | FK → quality_inspections.id | Failed inspection |
| work_order_id | string | FK → work_orders.id | Work order |
| quantity | number | >0 | Rework quantity |
| reason | string | | Rework reason |
| status | enum | NOT NULL | CREATED, IN_PROGRESS, COMPLETED |
| org | string | NOT NULL | Organization |
| created_at | datetime | NOT NULL | Creation timestamp |

---

## 16. finished_goods_receipts

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK, prefix: fgr- | Receipt ID |
| work_order_id | string | FK → work_orders.id, UNIQUE | Work order (one receipt per WO) |
| quantity | number | >0 | Received quantity |
| warehouse_location | string | | Storage location |
| status | enum | NOT NULL | CREATED, CONFIRMED |
| org | string | NOT NULL | Organization |
| received_by | string | | Receiver user ID |
| created_at | datetime | NOT NULL | Creation timestamp |

---

## Indexes (Logical)

- products: UNIQUE(sku), INDEX(org, category)
- boms: INDEX(product_id), INDEX(org)
- bom_lines: INDEX(bom_id)
- work_orders: INDEX(org, factory, status), INDEX(production_plan_id)
- work_order_operations: INDEX(work_order_id)
- material_reservations: INDEX(work_order_id), INDEX(material_id)
- material_issues: INDEX(reservation_id), INDEX(work_order_id)
- work_reports: INDEX(work_order_id), INDEX(operation_id)
- quality_inspections: INDEX(work_order_id)
- finished_goods_receipts: UNIQUE(work_order_id)
- sales_orders: UNIQUE(order_ref)
