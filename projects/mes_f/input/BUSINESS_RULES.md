# MES Business Rules Specification

## 1. Domain Overview

This Manufacturing Execution System (MES) manages discrete manufacturing operations including production planning, work order execution, material management, quality inspection, and finished goods receipt.

**Business Main Chain:**
Sales Order → Production Plan → Work Order → BOM Expansion → Material Reservation → Material Issue → Operation Start → Work Report → Quality Inspection → Rework (if failed) → Finished Goods Receipt → Work Order Close

## 2. Authorization Rules

### 2.1 Role-Based Access Control

| Operation | PLANNER | OPERATOR | INSPECTOR | MANAGER | WAREHOUSE | ADMIN |
|-----------|---------|----------|-----------|---------|-----------|-------|
| Create Product/Material | YES | NO | NO | YES | NO | YES |
| Modify Product Cost | NO | NO | NO | YES | NO | YES |
| Create/Modify BOM | YES | NO | NO | YES | NO | YES |
| Delete BOM | NO | NO | NO | YES | NO | YES |
| Create Work Center | NO | NO | NO | YES | NO | YES |
| Create/Modify Routing | YES | NO | NO | YES | NO | YES |
| Create Sales Order | YES | NO | NO | YES | NO | YES |
| Create Production Plan | YES | NO | NO | YES | NO | YES |
| Confirm Production Plan | YES | NO | NO | YES | NO | YES |
| Create Work Order | YES | NO | NO | YES | NO | YES |
| Release Work Order | YES | NO | NO | YES | NO | YES |
| Start Work Order | NO | YES | NO | YES | NO | YES |
| Complete Work Order | NO | YES | NO | YES | NO | YES |
| Close Work Order | NO | NO | NO | YES | NO | YES |
| Cancel Work Order | YES | NO | NO | YES | NO | YES |
| Delete Work Order | NO | NO | NO | YES | NO | YES |
| Create Material Reservation | YES | NO | NO | YES | YES | YES |
| Create Material Issue | NO | NO | NO | YES | YES | YES |
| Pick Material | NO | YES | NO | YES | YES | YES |
| Return Material | NO | NO | NO | YES | YES | YES |
| Create Work Report | NO | YES | NO | YES | NO | YES |
| Create Quality Inspection | NO | NO | YES | YES | NO | YES |
| Submit Inspection Result | NO | NO | YES | YES | NO | YES |
| Create Rework Order | NO | NO | YES | YES | NO | YES |
| Start/Complete Rework | NO | YES | NO | YES | NO | YES |
| Create Finished Goods Receipt | NO | NO | NO | YES | YES | YES |
| Confirm Receipt | NO | NO | NO | YES | YES | YES |

### 2.2 Organization Scope Rules

- All entities belong to an organization (org: acme or globex)
- Users can only view and operate on entities within their own organization
- ADMIN role has cross-organization access
- Work Centers, Work Orders, and Material data must be filtered by org

### 2.3 Factory Scope Rules

- Each user is assigned to a specific factory (fac-001, fac-002, fac-003)
- Operators can only report work for operations in their assigned factory
- Work Orders are bound to a specific factory
- Material Issues must match the factory of the work order

## 3. State Machine Rules

### 3.1 Work Order Lifecycle

```
CREATED → RELEASED → IN_PRODUCTION → COMPLETED → CLOSED
    ↓         ↓           ↓
CANCELLED  CANCELLED   CANCELLED
```

- **CREATED → RELEASED**: Only from CREATED status. Requires Planner/Manager/Admin role.
- **RELEASED → IN_PRODUCTION**: Only from RELEASED status. Requires all material reservations to be in RESERVED status.
- **IN_PRODUCTION → COMPLETED**: Only from IN_PRODUCTION status. Requires all operations to be COMPLETED.
- **COMPLETED → CLOSED**: Only from COMPLETED status. Requires Manager/Admin role.
- **Cancel**: Allowed from CREATED, RELEASED, IN_PRODUCTION. Must release all material reservations.
- **COMPLETED and CLOSED orders cannot be released, modified, or cancelled.**

### 3.2 Production Plan Lifecycle

```
CREATED → CONFIRMED
```

- **CREATED → CONFIRMED**: Only from CREATED status.
- **Confirmed plans cannot be modified.** Quantity, dates, and scope are frozen after confirmation.

### 3.3 Quality Inspection Lifecycle

```
PENDING → IN_PROGRESS → COMPLETED
```

- **PENDING → IN_PROGRESS**: Only from PENDING.
- **IN_PROGRESS → COMPLETED**: Only from IN_PROGRESS. Requires valid result submission.
- Inspection must be completed before expiry_date.
- pass_quantity + fail_quantity must equal sample_size.

### 3.4 Material Issue Lifecycle

```
CREATED → PICKED → (RETURNED)
```

- **CREATED → PICKED**: Material physically issued from warehouse.
- **PICKED → RETURNED**: Material returned to warehouse.
- Issue quantity must not exceed reserved quantity for the reservation.

### 3.5 Rework Order Lifecycle

```
CREATED → IN_PROGRESS → COMPLETED
```

- Rework can only be created when a Quality Inspection result is REJECT.
- Rework quantity must not exceed the fail_quantity of the inspection.

### 3.6 Finished Goods Receipt Lifecycle

```
CREATED → CONFIRMED
```

- Receipt can only be created when Work Order status is COMPLETED.
- Receipt requires at least one Quality Inspection with result PASS for the work order.
- Receipt quantity must not exceed work order completed_quantity.
- Duplicate receipts for the same work order are not allowed (idempotency).
- On confirmation, work order completed_quantity must be updated.

## 4. Cross-Entity Business Rules

### 4.1 Work Order Creation Preconditions

- Product must have an active BOM (Bill of Materials)
- Product must have an active Routing (process route)
- If linked to a Production Plan, the plan must be CONFIRMED
- Work Order planned_quantity must not exceed remaining plan quantity

### 4.2 Work Order Start Preconditions

- All Material Reservations for the work order must be in RESERVED status
- At least one operation must exist

### 4.3 Work Order Completion Preconditions

- All Work Order Operations must be in COMPLETED status
- At least one Work Report must exist

### 4.4 Material Issue Constraints

- Issue quantity + previously issued quantity must not exceed reserved_quantity
- Material Issue must reference a valid reservation for the same work order and material

### 4.5 Work Report Constraints

- Reported quantity must not exceed work order planned_quantity minus previously reported
- Operation must belong to the referenced work order
- Operator factory must match work order factory

### 4.6 Quality Inspection Constraints

- pass_quantity + fail_quantity must equal sample_size
- Inspection must be submitted before expiry_date
- Work Report must exist for the referenced operation

### 4.7 Rework Order Constraints

- Referenced Quality Inspection must have result = REJECT
- Rework quantity must not exceed inspection fail_quantity

### 4.8 Finished Goods Receipt Constraints

- Work Order must be COMPLETED
- At least one Quality Inspection with PASS result must exist
- No duplicate receipt for the same work order (idempotency key: work_order_id)
- Receipt quantity <= work order planned_quantity

## 5. Conservation and Aggregate Rules

### 5.1 Material Conservation

For each Material Reservation:
```
issued_quantity <= reserved_quantity
```

Total material issued for a work order must not exceed total reserved.

### 5.2 Work Report Quantity Conservation

For each Work Order Operation:
```
sum(reported_quantity) <= work_order.planned_quantity
```

### 5.3 Quality Inspection Balance

For each Quality Inspection:
```
pass_quantity + fail_quantity = sample_size
```

### 5.4 Finished Goods Receipt Conservation

```
receipt.quantity <= work_order.planned_quantity
work_order.completed_quantity += receipt.quantity (on confirm)
```

### 5.5 BOM Material Requirement

For a Work Order with quantity Q and BOM line with quantity_per_unit P and scrap_factor S:
```
required_quantity = Q * P * (1 + S)
```

## 6. Idempotency Rules

### 6.1 Sales Order Reference

- Creating a Sales Order with a duplicate order_ref must return 409 Conflict
- order_ref is the idempotency key for sales orders

### 6.2 Finished Goods Receipt

- Only one receipt per work order is allowed
- work_order_id is the idempotency key for receipts
- Duplicate receipt creation must return 409 Conflict

### 6.3 Work Report

- Same operation_id + same shift + same reported_by within same day should be detected as potential duplicate

## 7. Temporal Rules

### 7.1 Delivery Date Constraint

- Sales Order delivery_date cannot be modified after a Production Plan referencing it is CONFIRMED

### 7.2 Inspection Expiry

- Quality Inspection must be submitted (status → COMPLETED) before its expiry_date
- Expired inspections cannot be submitted

### 7.3 Work Report Shift Window

- Work Reports must reference a valid shift (DAY/NIGHT)
- Reports should be created within the shift time window

### 7.4 Production Plan Date Constraint

- planned_start must be before planned_end
- Work Order dates must fall within the parent Production Plan dates

## 8. Concurrency and Version Rules

### 8.1 Work Order Optimistic Locking

- Work Order updates must include the current version number
- If the provided version does not match the stored version, return 409 Conflict
- Prevents lost updates from concurrent modifications

### 8.2 Material Issue Version Control

- Material Issue return operations must verify version
- Prevents double-return from concurrent requests

## 9. Compensation Rules

### 9.1 Work Order Cancellation

- When a Work Order is cancelled, all associated Material Reservations must be released (status → RELEASED)
- Issued materials must be flagged for return

### 9.2 BOM Deletion

- When a BOM is deleted, all associated BOM Lines must be deleted
- Orphan BOM Lines are not allowed

## 10. Batch Operation Rules

### 10.1 Bulk Work Order Release

- If any Work Order in the batch fails validation, ALL previously released orders in the batch must be rolled back
- All-or-nothing semantics required

### 10.2 Bulk Material Issue

- Batch material issue must be atomic
- If any item fails validation, no items should be created
- All-or-nothing semantics required
