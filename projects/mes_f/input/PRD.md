# MES Product Requirements Document (PRD)

## 1. Product Overview

**Product Name:** Discrete Manufacturing MES (Manufacturing Execution System)
**Version:** 1.0
**Domain:** Discrete Manufacturing (machining, assembly, injection molding)

### 1.1 Purpose
This MES system manages the complete production execution lifecycle for discrete manufacturing enterprises, from sales order receipt through production planning, shop floor execution, quality control, and finished goods delivery.

### 1.2 Target Users
- Production Planners: Create and manage production plans and work orders
- Shop Floor Operators: Execute operations and report production output
- Quality Inspectors: Perform quality inspections and manage rework
- Production Managers: Oversee production, approve operations
- Warehouse Keepers: Manage material issuing and finished goods receipt
- Administrators: System configuration and cross-org oversight

### 1.3 Multi-Tenancy
- Organization-level isolation (acme, globex)
- Factory-level scope within organizations (fac-001, fac-002, fac-003)
- Role-based access control with 6 distinct roles

---

## 2. Core Business Flows

### 2.1 Order-to-Production Flow
```
Sales Order → Production Plan → Work Order → Shop Floor Execution
```

1. Sales Order received from customer with product, quantity, delivery date
2. Production Plan created referencing sales order, specifying factory and schedule
3. Plan confirmed → becomes immutable baseline
4. Work Orders created from confirmed plans, specifying BOM and Routing
5. Work Orders released to shop floor

### 2.2 Material Management Flow
```
BOM Expansion → Material Reservation → Material Issue → Consumption
```

1. BOM defines material requirements per unit of finished product
2. Material Reservations created for work order quantities (with scrap factor)
3. Material Issues created against reservations
4. Operators pick materials from warehouse
5. Materials consumed in production; returns handled if excess

### 2.3 Production Execution Flow
```
Operation Start → Work Report → Quality Inspection → Completion
```

1. Work Order operations follow routing sequence
2. Operators report produced quantities per operation per shift
3. Quality inspectors sample and inspect output
4. Failed inspections trigger rework orders
5. All operations completed → Work Order completed

### 2.4 Finished Goods Flow
```
Quality Pass → Finished Goods Receipt → Warehouse → Work Order Close
```

1. Quality inspection passed for work order output
2. Finished Goods Receipt created and confirmed
3. Goods stored in warehouse
4. Work Order closed by manager

---

## 3. Functional Requirements

### 3.1 Product/Material Management
- FR-001: Support three material categories: RAW_MATERIAL, COMPONENT, FINISHED_GOODS
- FR-002: Each material has SKU, name, unit, unit_cost, org ownership
- FR-003: Material cost modification restricted to Manager/Admin

### 3.2 BOM Management
- FR-010: BOM links a finished product to its component materials
- FR-011: BOM lines specify quantity_per_unit and scrap_factor
- FR-012: BOM expansion calculates total material requirements
- FR-013: BOM deletion must cascade to all BOM lines

### 3.3 Routing Management
- FR-020: Routing defines the sequence of operations for a product
- FR-021: Each routing step references a work center with time standards
- FR-022: Steps are sequenced (seq 10, 20, 30...)

### 3.4 Sales Order Management
- FR-030: Sales orders track customer demand with delivery dates
- FR-031: order_ref is unique (idempotency key)
- FR-032: delivery_date frozen after linked plan is confirmed

### 3.5 Production Planning
- FR-040: Plans reference sales orders and specify factory/schedule
- FR-041: Plan confirmation makes it immutable
- FR-042: planned_start must precede planned_end

### 3.6 Work Order Management
- FR-050: Work orders are the primary production execution unit
- FR-051: Full lifecycle: CREATED → RELEASED → IN_PRODUCTION → COMPLETED → CLOSED
- FR-052: Cancellation allowed from CREATED/RELEASED/IN_PRODUCTION
- FR-053: Optimistic locking via version field
- FR-054: Bulk release with all-or-nothing semantics

### 3.7 Material Reservation & Issue
- FR-060: Reservations allocate materials to work orders
- FR-061: Issue quantity cannot exceed reserved quantity
- FR-062: Pick/Return lifecycle for physical material handling
- FR-063: Bulk issue with atomic semantics

### 3.8 Work Reporting
- FR-070: Operators report output per operation per shift
- FR-071: Factory scope enforcement (operator factory = WO factory)
- FR-072: Cumulative reported quantity ≤ planned quantity

### 3.9 Quality Inspection
- FR-080: Inspections sample output with pass/fail quantities
- FR-081: pass_quantity + fail_quantity = sample_size (conservation)
- FR-082: Expiry date enforcement
- FR-083: REJECT result triggers rework eligibility

### 3.10 Rework Management
- FR-090: Rework orders reference failed inspections
- FR-091: Rework quantity ≤ inspection fail_quantity
- FR-092: Lifecycle: CREATED → IN_PROGRESS → COMPLETED

### 3.11 Finished Goods Receipt
- FR-100: Receipt requires WO COMPLETED + quality PASS
- FR-101: One receipt per work order (idempotency)
- FR-102: Confirmation updates WO completed_quantity

---

## 4. Non-Functional Requirements

### 4.1 Security
- Bearer token authentication on all business endpoints
- Role-based access control (6 roles)
- Organization-level data isolation
- Factory-level operation scope

### 4.2 Data Integrity
- Optimistic locking for concurrent modifications
- Conservation laws for quantities
- Cascade operations for entity relationships
- Idempotency keys for duplicate prevention

### 4.3 Availability
- RESTful JSON API
- Stateless authentication
- Health check endpoint

---

## 5. Entity Relationship Overview

```
SalesOrder 1──N ProductionPlan 1──N WorkOrder
WorkOrder N──1 Product (via product_id)
WorkOrder N──1 BOM (via bom_id)
WorkOrder N──1 Routing (via routing_id)
BOM 1──N BOMLine N──1 Product(material)
Routing 1──N RoutingStep N──1 WorkCenter
WorkOrder 1──N WorkOrderOperation
WorkOrder 1──N MaterialReservation 1──N MaterialIssue
WorkOrder 1──N WorkReport
WorkOrder 1──N QualityInspection 1──N ReworkOrder
WorkOrder 1──1 FinishedGoodsReceipt
```

---

## 6. Deployment

- **Port:** 8020
- **Protocol:** HTTP/1.1
- **Format:** JSON
- **Auth:** Bearer Token (static tokens per user)
