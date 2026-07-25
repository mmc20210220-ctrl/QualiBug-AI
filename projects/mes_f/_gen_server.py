#!/usr/bin/env python3
"""Generator script: creates the MES mock server for Project F."""
import os

TARGET = os.path.join(os.path.dirname(__file__), "mock_server.py")

PART1 = '''#!/usr/bin/env python3
"""
Manufacturing Execution System (MES) - Mock Server
Project F Blind Generalization Benchmark Target

Domain: Discrete Manufacturing MES
Entities: Product, BOM, BOMLine, WorkCenter, Routing, RoutingStep, WorkOrder,
          WorkOrderOperation, MaterialReservation, MaterialIssue, WorkReport,
          QualityInspection, ReworkOrder, FinishedGoodsReceipt, SalesOrder, ProductionPlan
Roles: Planner, Operator, Inspector, Manager, WarehouseKeeper, Admin
Scope: Organization (acme/globex) + Factory (fac-001/fac-002/fac-003)
"""

import json
import uuid
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ACCOUNTS = {
    "planner-pat-token": {"id": "pln-001", "name": "Pat Zhang", "role": "PLANNER", "org": "acme", "factory": "fac-001"},
    "planner-pam-token": {"id": "pln-002", "name": "Pam Xu", "role": "PLANNER", "org": "globex", "factory": "fac-003"},
    "operator-oli-token": {"id": "opr-001", "name": "Oli Chen", "role": "OPERATOR", "org": "acme", "factory": "fac-001"},
    "operator-ole-token": {"id": "opr-002", "name": "Ole Wu", "role": "OPERATOR", "org": "acme", "factory": "fac-002"},
    "operator-ova-token": {"id": "opr-003", "name": "Ova Li", "role": "OPERATOR", "org": "globex", "factory": "fac-003"},
    "inspector-iris-token": {"id": "ins-001", "name": "Iris Wang", "role": "INSPECTOR", "org": "acme", "factory": "fac-001"},
    "inspector-ivan-token": {"id": "ins-002", "name": "Ivan Zhao", "role": "INSPECTOR", "org": "globex", "factory": "fac-003"},
    "manager-marcus-token": {"id": "mgr-001", "name": "Marcus Sun", "role": "MANAGER", "org": "acme", "factory": "fac-001"},
    "manager-mona-token": {"id": "mgr-002", "name": "Mona Huang", "role": "MANAGER", "org": "globex", "factory": "fac-003"},
    "warehouse-will-token": {"id": "whk-001", "name": "Will Zhou", "role": "WAREHOUSE", "org": "acme", "factory": "fac-001"},
    "warehouse-wanda-token": {"id": "whk-002", "name": "Wanda Yang", "role": "WAREHOUSE", "org": "globex", "factory": "fac-003"},
    "admin-arthur-token": {"id": "adm-001", "name": "Arthur Liu", "role": "ADMIN", "org": "acme", "factory": None},
}


class DataStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        self.products = {}
        self.boms = {}
        self.bom_lines = {}
        self.work_centers = {}
        self.routings = {}
        self.routing_steps = {}
        self.work_orders = {}
        self.work_order_operations = {}
        self.material_reservations = {}
        self.material_issues = {}
        self.work_reports = {}
        self.quality_inspections = {}
        self.rework_orders = {}
        self.finished_goods_receipts = {}
        self.sales_orders = {}
        self.production_plans = {}
        self._init_data()

    def _now(self):
        return datetime.utcnow().isoformat() + "Z"

    def _init_data(self):
        self.products["mat-001"] = {"id": "mat-001", "sku": "RAW-STEEL-01", "name": "Steel Plate 2mm", "org": "acme", "category": "RAW_MATERIAL", "unit": "kg", "unit_cost": 12.5, "status": "ACTIVE", "created_at": self._now()}
        self.products["mat-002"] = {"id": "mat-002", "sku": "RAW-ALU-01", "name": "Aluminum Rod 10mm", "org": "acme", "category": "RAW_MATERIAL", "unit": "pcs", "unit_cost": 8.0, "status": "ACTIVE", "created_at": self._now()}
        self.products["mat-003"] = {"id": "mat-003", "sku": "COMP-GEAR-01", "name": "Gear Module A", "org": "acme", "category": "COMPONENT", "unit": "pcs", "unit_cost": 45.0, "status": "ACTIVE", "created_at": self._now()}
        self.products["mat-004"] = {"id": "mat-004", "sku": "COMP-BEARING-01", "name": "Bearing 6205", "org": "acme", "category": "COMPONENT", "unit": "pcs", "unit_cost": 22.0, "status": "ACTIVE", "created_at": self._now()}
        self.products["mat-005"] = {"id": "mat-005", "sku": "FG-REDUCER-01", "name": "Planetary Reducer X1", "org": "acme", "category": "FINISHED_GOODS", "unit": "pcs", "unit_cost": 580.0, "status": "ACTIVE", "created_at": self._now()}
        self.products["mat-006"] = {"id": "mat-006", "sku": "RAW-PLASTIC-01", "name": "ABS Pellets", "org": "globex", "category": "RAW_MATERIAL", "unit": "kg", "unit_cost": 6.5, "status": "ACTIVE", "created_at": self._now()}
        self.products["mat-007"] = {"id": "mat-007", "sku": "FG-HOUSING-01", "name": "Motor Housing H2", "org": "globex", "category": "FINISHED_GOODS", "unit": "pcs", "unit_cost": 120.0, "status": "ACTIVE", "created_at": self._now()}
        self.work_centers["wc-001"] = {"id": "wc-001", "name": "CNC Machining Center 1", "org": "acme", "factory": "fac-001", "capacity_hours_per_day": 16, "status": "ACTIVE", "created_at": self._now()}
        self.work_centers["wc-002"] = {"id": "wc-002", "name": "Assembly Station 1", "org": "acme", "factory": "fac-001", "capacity_hours_per_day": 8, "status": "ACTIVE", "created_at": self._now()}
        self.work_centers["wc-003"] = {"id": "wc-003", "name": "Quality Lab", "org": "acme", "factory": "fac-001", "capacity_hours_per_day": 8, "status": "ACTIVE", "created_at": self._now()}
        self.work_centers["wc-004"] = {"id": "wc-004", "name": "CNC Machining Center 2", "org": "acme", "factory": "fac-002", "capacity_hours_per_day": 16, "status": "ACTIVE", "created_at": self._now()}
        self.work_centers["wc-005"] = {"id": "wc-005", "name": "Injection Molding 1", "org": "globex", "factory": "fac-003", "capacity_hours_per_day": 20, "status": "ACTIVE", "created_at": self._now()}
        self.boms["bom-001"] = {"id": "bom-001", "product_id": "mat-005", "org": "acme", "version": "1.0", "status": "ACTIVE", "created_at": self._now()}
        self.boms["bom-002"] = {"id": "bom-002", "product_id": "mat-007", "org": "globex", "version": "1.0", "status": "ACTIVE", "created_at": self._now()}
        self.bom_lines["bl-001"] = {"id": "bl-001", "bom_id": "bom-001", "material_id": "mat-003", "quantity_per_unit": 4, "unit": "pcs", "scrap_factor": 0.02}
        self.bom_lines["bl-002"] = {"id": "bl-002", "bom_id": "bom-001", "material_id": "mat-004", "quantity_per_unit": 2, "unit": "pcs", "scrap_factor": 0.01}
        self.bom_lines["bl-003"] = {"id": "bl-003", "bom_id": "bom-001", "material_id": "mat-001", "quantity_per_unit": 1.5, "unit": "kg", "scrap_factor": 0.05}
        self.bom_lines["bl-004"] = {"id": "bl-004", "bom_id": "bom-002", "material_id": "mat-006", "quantity_per_unit": 0.8, "unit": "kg", "scrap_factor": 0.03}
        self.routings["rt-001"] = {"id": "rt-001", "product_id": "mat-005", "org": "acme", "version": "1.0", "status": "ACTIVE", "created_at": self._now()}
        self.routings["rt-002"] = {"id": "rt-002", "product_id": "mat-007", "org": "globex", "version": "1.0", "status": "ACTIVE", "created_at": self._now()}
        self.routing_steps["rs-001"] = {"id": "rs-001", "routing_id": "rt-001", "seq": 10, "name": "CNC Machining", "work_center_id": "wc-001", "setup_time_min": 30, "run_time_min_per_unit": 12}
        self.routing_steps["rs-002"] = {"id": "rs-002", "routing_id": "rt-001", "seq": 20, "name": "Assembly", "work_center_id": "wc-002", "setup_time_min": 15, "run_time_min_per_unit": 8}
        self.routing_steps["rs-003"] = {"id": "rs-003", "routing_id": "rt-001", "seq": 30, "name": "Final Inspection", "work_center_id": "wc-003", "setup_time_min": 5, "run_time_min_per_unit": 5}
        self.routing_steps["rs-004"] = {"id": "rs-004", "routing_id": "rt-002", "seq": 10, "name": "Injection Molding", "work_center_id": "wc-005", "setup_time_min": 45, "run_time_min_per_unit": 3}
        self.sales_orders["so-001"] = {"id": "so-001", "order_ref": "SO-2026-001", "customer": "AutoParts Corp", "org": "acme", "product_id": "mat-005", "quantity": 100, "delivery_date": "2026-09-15", "status": "CONFIRMED", "created_at": self._now(), "version": 1}
        self.sales_orders["so-002"] = {"id": "so-002", "order_ref": "SO-2026-002", "customer": "MotorTech Ltd", "org": "globex", "product_id": "mat-007", "quantity": 500, "delivery_date": "2026-08-30", "status": "CREATED", "created_at": self._now(), "version": 1}
        self.production_plans["pp-001"] = {"id": "pp-001", "sales_order_id": "so-001", "org": "acme", "factory": "fac-001", "product_id": "mat-005", "planned_quantity": 100, "planned_start": "2026-08-01", "planned_end": "2026-08-20", "status": "CONFIRMED", "created_by": "pln-001", "created_at": self._now(), "version": 1}
        self.work_orders["wo-001"] = {"id": "wo-001", "order_ref": "WO-2026-001", "production_plan_id": "pp-001", "product_id": "mat-005", "bom_id": "bom-001", "routing_id": "rt-001", "org": "acme", "factory": "fac-001", "planned_quantity": 50, "completed_quantity": 0, "status": "RELEASED", "priority": 1, "planned_start": "2026-08-01", "planned_end": "2026-08-10", "created_by": "pln-001", "created_at": self._now(), "released_at": self._now(), "version": 1}
        self.work_orders["wo-002"] = {"id": "wo-002", "order_ref": "WO-2026-002", "production_plan_id": "pp-001", "product_id": "mat-005", "bom_id": "bom-001", "routing_id": "rt-001", "org": "acme", "factory": "fac-001", "planned_quantity": 50, "completed_quantity": 0, "status": "CREATED", "priority": 2, "planned_start": "2026-08-11", "planned_end": "2026-08-20", "created_by": "pln-001", "created_at": self._now(), "released_at": None, "version": 1}
        self.work_order_operations["woo-001"] = {"id": "woo-001", "work_order_id": "wo-001", "routing_step_id": "rs-001", "seq": 10, "name": "CNC Machining", "work_center_id": "wc-001", "status": "PENDING", "reported_quantity": 0, "started_at": None, "completed_at": None}
        self.work_order_operations["woo-002"] = {"id": "woo-002", "work_order_id": "wo-001", "routing_step_id": "rs-002", "seq": 20, "name": "Assembly", "work_center_id": "wc-002", "status": "PENDING", "reported_quantity": 0, "started_at": None, "completed_at": None}
        self.work_order_operations["woo-003"] = {"id": "woo-003", "work_order_id": "wo-001", "routing_step_id": "rs-003", "seq": 30, "name": "Final Inspection", "work_center_id": "wc-003", "status": "PENDING", "reported_quantity": 0, "started_at": None, "completed_at": None}
        self.material_reservations["mr-001"] = {"id": "mr-001", "work_order_id": "wo-001", "material_id": "mat-003", "required_quantity": 200, "reserved_quantity": 200, "issued_quantity": 0, "org": "acme", "status": "RESERVED", "created_at": self._now()}
        self.material_reservations["mr-002"] = {"id": "mr-002", "work_order_id": "wo-001", "material_id": "mat-004", "required_quantity": 100, "reserved_quantity": 100, "issued_quantity": 0, "org": "acme", "status": "RESERVED", "created_at": self._now()}
        self.material_reservations["mr-003"] = {"id": "mr-003", "work_order_id": "wo-001", "material_id": "mat-001", "required_quantity": 75, "reserved_quantity": 75, "issued_quantity": 0, "org": "acme", "status": "RESERVED", "created_at": self._now()}


STORE = DataStore()


def gen_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def authenticate(headers):
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return ACCOUNTS.get(auth[7:])
    return None


def check_role(user, roles):
    return user and user.get("role") in roles
'''

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(PART1)

print(f"Part 1 written: {os.path.getsize(TARGET)} bytes")
