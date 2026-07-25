# MES Data Dictionary

## 1. Enumerations

### 1.1 Material Category
| Value | Description |
|-------|-------------|
| RAW_MATERIAL | Raw materials (steel, aluminum, plastic) |
| COMPONENT | Sub-assemblies or purchased parts (gears, bearings) |
| FINISHED_GOODS | Final products ready for customer delivery |

### 1.2 Entity Status Values

**Product:** ACTIVE, INACTIVE

**BOM / Routing:** ACTIVE, INACTIVE

**Sales Order:** CREATED, CONFIRMED

**Production Plan:** CREATED, CONFIRMED

**Work Order:** CREATED, RELEASED, IN_PRODUCTION, COMPLETED, CLOSED, CANCELLED

**Work Order Operation:** PENDING, IN_PROGRESS, COMPLETED

**Material Reservation:** RESERVED, RELEASED

**Material Issue:** CREATED, PICKED, RETURNED

**Quality Inspection:** PENDING, IN_PROGRESS, COMPLETED

**Inspection Result:** PASS, REJECT

**Rework Order:** CREATED, IN_PROGRESS, COMPLETED

**Finished Goods Receipt:** CREATED, CONFIRMED

### 1.3 Shift
| Value | Description |
|-------|-------------|
| DAY | Day shift (typically 06:00-18:00) |
| NIGHT | Night shift (typically 18:00-06:00) |

### 1.4 Inspection Type
| Value | Description |
|-------|-------------|
| IN_PROCESS | In-process inspection during production |
| FINAL | Final inspection before receipt |

---

## 2. Organization Codes

| Code | Name | Factories |
|------|------|-----------|
| acme | Acme Manufacturing Co. | fac-001, fac-002 |
| globex | Globex Industries | fac-003 |

---

## 3. Factory Codes

| Code | Organization | Location | Work Centers |
|------|-------------|----------|--------------|
| fac-001 | acme | Main Plant | wc-001 (CNC), wc-002 (Assembly), wc-003 (Quality Lab) |
| fac-002 | acme | Second Plant | wc-004 (CNC) |
| fac-003 | globex | Globex Plant | wc-005 (Injection Molding) |

---

## 4. Unit of Measure

| Code | Description |
|------|-------------|
| kg | Kilogram |
| pcs | Pieces |

---

## 5. ID Prefixes

| Prefix | Entity |
|--------|--------|
| mat- | Product/Material |
| bom- | Bill of Materials |
| bl- | BOM Line |
| wc- | Work Center |
| rt- | Routing |
| rs- | Routing Step |
| so- | Sales Order |
| pp- | Production Plan |
| wo- | Work Order |
| woo- | Work Order Operation |
| mr- | Material Reservation |
| mi- | Material Issue |
| wr- | Work Report |
| qi- | Quality Inspection |
| rw- | Rework Order |
| fgr- | Finished Goods Receipt |

---

## 6. Key Business Formulas

### Material Requirement Calculation
```
required_quantity = work_order.planned_quantity × bom_line.quantity_per_unit × (1 + bom_line.scrap_factor)
```

### Conservation Invariants
```
material_reservation.issued_quantity ≤ material_reservation.reserved_quantity
sum(work_report.quantity) ≤ work_order.planned_quantity  (per operation)
quality_inspection.pass_quantity + quality_inspection.fail_quantity = quality_inspection.sample_size
finished_goods_receipt.quantity ≤ work_order.planned_quantity
rework_order.quantity ≤ quality_inspection.fail_quantity
```

### Work Order Completion
```
work_order.completed_quantity += finished_goods_receipt.quantity  (on receipt confirm)
```

---

## 7. Date Formats

| Field Type | Format | Example |
|------------|--------|---------|
| Date | YYYY-MM-DD | 2026-08-15 |
| DateTime | ISO 8601 UTC | 2026-08-15T10:30:00Z |

---

## 8. Seed Data Summary

| Entity | Count | IDs |
|--------|-------|-----|
| Products | 7 | mat-001 ~ mat-007 |
| BOMs | 2 | bom-001, bom-002 |
| BOM Lines | 4 | bl-001 ~ bl-004 |
| Work Centers | 5 | wc-001 ~ wc-005 |
| Routings | 2 | rt-001, rt-002 |
| Routing Steps | 4 | rs-001 ~ rs-004 |
| Sales Orders | 2 | so-001, so-002 |
| Production Plans | 1 | pp-001 |
| Work Orders | 2 | wo-001, wo-002 |
| WO Operations | 3 | woo-001 ~ woo-003 |
| Material Reservations | 3 | mr-001 ~ mr-003 |
