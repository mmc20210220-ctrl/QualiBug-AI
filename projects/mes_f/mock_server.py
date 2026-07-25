#!/usr/bin/env python3
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
from socketserver import ThreadingMixIn
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


class MESHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode()) if length > 0 else {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "/health":
            return self._json({"status": "healthy", "service": "mes"})
        user = authenticate(self.headers)
        if not user:
            return self._json({"error": "Unauthorized"}, 401)
        params = parse_qs(parsed.query)
        if path == "/products":
            return self._list_products(user, params)
        if path.startswith("/products/") and path.count("/") == 2:
            return self._get_product(user, path.split("/")[2])
        if path == "/boms":
            return self._list_boms(user)
        if path.startswith("/boms/") and path.endswith("/lines"):
            return self._get_bom_lines(user, path.split("/")[2])
        if path.startswith("/boms/") and path.endswith("/expand"):
            return self._expand_bom(user, path.split("/")[2])
        if path.startswith("/boms/") and path.count("/") == 2:
            return self._get_bom(user, path.split("/")[2])
        if path == "/work-centers":
            return self._list_work_centers(user)
        if path.startswith("/work-centers/") and path.count("/") == 2:
            return self._get_work_center(user, path.split("/")[2])
        if path == "/routings":
            return self._list_routings(user)
        if path.startswith("/routings/") and path.endswith("/steps"):
            return self._get_routing_steps(user, path.split("/")[2])
        if path.startswith("/routings/") and path.count("/") == 2:
            return self._get_routing(user, path.split("/")[2])
        if path == "/work-orders":
            return self._list_work_orders(user, params)
        if path.startswith("/work-orders/") and path.endswith("/operations"):
            return self._get_wo_operations(user, path.split("/")[2])
        if path.startswith("/work-orders/") and path.count("/") == 2:
            return self._get_work_order(user, path.split("/")[2])
        if path == "/material-reservations":
            return self._list_reservations(user, params)
        if path.startswith("/material-reservations/") and path.count("/") == 2:
            return self._get_reservation(user, path.split("/")[2])
        if path == "/material-issues":
            return self._list_material_issues(user, params)
        if path.startswith("/material-issues/") and path.count("/") == 2:
            return self._get_material_issue(user, path.split("/")[2])
        if path == "/work-reports":
            return self._list_work_reports(user, params)
        if path.startswith("/work-reports/") and path.count("/") == 2:
            return self._get_work_report(user, path.split("/")[2])
        if path == "/quality-inspections":
            return self._list_inspections(user, params)
        if path.startswith("/quality-inspections/") and path.count("/") == 2:
            return self._get_inspection(user, path.split("/")[2])
        if path == "/rework-orders":
            return self._list_rework_orders(user)
        if path.startswith("/rework-orders/") and path.count("/") == 2:
            return self._get_rework_order(user, path.split("/")[2])
        if path == "/finished-goods-receipts":
            return self._list_receipts(user)
        if path.startswith("/finished-goods-receipts/") and path.count("/") == 2:
            return self._get_receipt(user, path.split("/")[2])
        if path == "/sales-orders":
            return self._list_sales_orders(user)
        if path.startswith("/sales-orders/") and path.count("/") == 2:
            return self._get_sales_order(user, path.split("/")[2])
        if path == "/production-plans":
            return self._list_production_plans(user)
        if path.startswith("/production-plans/") and path.count("/") == 2:
            return self._get_production_plan(user, path.split("/")[2])
        self._json({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        user = authenticate(self.headers)
        if not user:
            return self._json({"error": "Unauthorized"}, 401)
        body = self._body()
        if path == "/products":
            return self._create_product(user, body)
        if path == "/boms":
            return self._create_bom(user, body)
        if path.startswith("/boms/") and path.endswith("/lines"):
            return self._add_bom_line(user, path.split("/")[2], body)
        if path == "/work-centers":
            return self._create_work_center(user, body)
        if path == "/routings":
            return self._create_routing(user, body)
        if path.startswith("/routings/") and path.endswith("/steps"):
            return self._add_routing_step(user, path.split("/")[2], body)
        if path == "/sales-orders":
            return self._create_sales_order(user, body)
        if path == "/production-plans":
            return self._create_production_plan(user, body)
        if path.startswith("/production-plans/") and path.endswith("/confirm"):
            return self._confirm_plan(user, path.split("/")[2])
        if path == "/work-orders":
            return self._create_work_order(user, body)
        if path.startswith("/work-orders/") and path.endswith("/release"):
            return self._release_wo(user, path.split("/")[2])
        if path.startswith("/work-orders/") and path.endswith("/start"):
            return self._start_wo(user, path.split("/")[2])
        if path.startswith("/work-orders/") and path.endswith("/complete"):
            return self._complete_wo(user, path.split("/")[2])
        if path.startswith("/work-orders/") and path.endswith("/close"):
            return self._close_wo(user, path.split("/")[2])
        if path.startswith("/work-orders/") and path.endswith("/cancel"):
            return self._cancel_wo(user, path.split("/")[2])
        if path == "/material-reservations":
            return self._create_reservation(user, body)
        if path.startswith("/material-reservations/") and path.endswith("/release"):
            return self._release_reservation(user, path.split("/")[2])
        if path == "/material-issues":
            return self._create_material_issue(user, body)
        if path.startswith("/material-issues/") and path.endswith("/pick"):
            return self._pick_issue(user, path.split("/")[2])
        if path.startswith("/material-issues/") and path.endswith("/return"):
            return self._return_issue(user, path.split("/")[2])
        if path == "/work-reports":
            return self._create_work_report(user, body)
        if path == "/quality-inspections":
            return self._create_inspection(user, body)
        if path.startswith("/quality-inspections/") and path.endswith("/start"):
            return self._start_inspection(user, path.split("/")[2])
        if path.startswith("/quality-inspections/") and path.endswith("/submit"):
            return self._submit_inspection(user, path.split("/")[2], body)
        if path == "/rework-orders":
            return self._create_rework(user, body)
        if path.startswith("/rework-orders/") and path.endswith("/start"):
            return self._start_rework(user, path.split("/")[2])
        if path.startswith("/rework-orders/") and path.endswith("/complete"):
            return self._complete_rework(user, path.split("/")[2])
        if path == "/finished-goods-receipts":
            return self._create_receipt(user, body)
        if path.startswith("/finished-goods-receipts/") and path.endswith("/confirm"):
            return self._confirm_receipt(user, path.split("/")[2])
        if path == "/work-orders/bulk-release":
            return self._bulk_release(user, body)
        if path == "/material-issues/bulk-issue":
            return self._bulk_issue(user, body)
        if path == "/reset":
            STORE.reset()
            return self._json({"status": "reset_complete"})
        self._json({"error": "Not found"}, 404)

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        user = authenticate(self.headers)
        if not user:
            return self._json({"error": "Unauthorized"}, 401)
        body = self._body()
        if path.startswith("/products/") and path.count("/") == 2:
            return self._update_product(user, path.split("/")[2], body)
        if path.startswith("/work-orders/") and path.count("/") == 2:
            return self._update_wo(user, path.split("/")[2], body)
        if path.startswith("/production-plans/") and path.count("/") == 2:
            return self._update_plan(user, path.split("/")[2], body)
        if path.startswith("/sales-orders/") and path.count("/") == 2:
            return self._update_sales_order(user, path.split("/")[2], body)
        self._json({"error": "Not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        user = authenticate(self.headers)
        if not user:
            return self._json({"error": "Unauthorized"}, 401)
        if path.startswith("/boms/") and path.count("/") == 2:
            return self._delete_bom(user, path.split("/")[2])
        if path.startswith("/work-orders/") and path.count("/") == 2:
            return self._delete_wo(user, path.split("/")[2])
        if path.startswith("/bom-lines/") and path.count("/") == 2:
            return self._delete_bom_line(user, path.split("/")[2])
        self._json({"error": "Not found"}, 404)

    # === Products ===
    def _list_products(self, user, params):
        with STORE.lock:
            products = list(STORE.products.values())
            cat = params.get("category", [None])[0]
            if cat:
                products = [p for p in products if p["category"] == cat]
            self._json({"products": products, "total": len(products)})

    def _get_product(self, user, pid):
        with STORE.lock:
            p = STORE.products.get(pid)
            if not p:
                return self._json({"error": "Product not found"}, 404)
            self._json(p)

    def _create_product(self, user, body):
        with STORE.lock:
            # BUG-MES-001: ACTOR_AUTHORIZATION - Operator can create products with cost
            if not check_role(user, ["ADMIN", "MANAGER", "PLANNER", "OPERATOR"]):
                return self._json({"error": "Forbidden"}, 403)
            pid = gen_id("mat")
            product = {"id": pid, "sku": body.get("sku", ""), "name": body.get("name", ""), "org": user["org"], "category": body.get("category", "RAW_MATERIAL"), "unit": body.get("unit", "pcs"), "unit_cost": body.get("unit_cost", 0), "status": "ACTIVE", "created_at": STORE._now()}
            STORE.products[pid] = product
            self._json(product, 201)

    def _update_product(self, user, pid, body):
        with STORE.lock:
            p = STORE.products.get(pid)
            if not p:
                return self._json({"error": "Product not found"}, 404)
            # BUG-MES-002: ACTOR_AUTHORIZATION - No role check on cost change
            if "unit_cost" in body:
                p["unit_cost"] = body["unit_cost"]
            if "name" in body:
                p["name"] = body["name"]
            if "status" in body:
                p["status"] = body["status"]
            self._json(p)

    # === BOMs ===
    def _list_boms(self, user):
        with STORE.lock:
            self._json({"boms": list(STORE.boms.values()), "total": len(STORE.boms)})

    def _get_bom(self, user, bid):
        with STORE.lock:
            b = STORE.boms.get(bid)
            if not b:
                return self._json({"error": "BOM not found"}, 404)
            self._json(b)

    def _get_bom_lines(self, user, bid):
        with STORE.lock:
            lines = [l for l in STORE.bom_lines.values() if l["bom_id"] == bid]
            self._json({"bom_id": bid, "lines": lines, "total": len(lines)})

    def _expand_bom(self, user, bid):
        with STORE.lock:
            b = STORE.boms.get(bid)
            if not b:
                return self._json({"error": "BOM not found"}, 404)
            lines = [l for l in STORE.bom_lines.values() if l["bom_id"] == bid]
            expanded = []
            for line in lines:
                mat = STORE.products.get(line["material_id"], {})
                expanded.append({**line, "material_name": mat.get("name", ""), "material_unit": mat.get("unit", "")})
            self._json({"bom": b, "expanded_lines": expanded})

    def _create_bom(self, user, body):
        with STORE.lock:
            if not check_role(user, ["PLANNER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            bid = gen_id("bom")
            bom = {"id": bid, "product_id": body.get("product_id", ""), "org": user["org"], "version": body.get("version", "1.0"), "status": "ACTIVE", "created_at": STORE._now()}
            STORE.boms[bid] = bom
            self._json(bom, 201)

    def _add_bom_line(self, user, bid, body):
        with STORE.lock:
            if not STORE.boms.get(bid):
                return self._json({"error": "BOM not found"}, 404)
            if not check_role(user, ["PLANNER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            lid = gen_id("bl")
            line = {"id": lid, "bom_id": bid, "material_id": body.get("material_id", ""), "quantity_per_unit": body.get("quantity_per_unit", 1), "unit": body.get("unit", "pcs"), "scrap_factor": body.get("scrap_factor", 0)}
            STORE.bom_lines[lid] = line
            self._json(line, 201)

    def _delete_bom(self, user, bid):
        with STORE.lock:
            if not STORE.boms.get(bid):
                return self._json({"error": "BOM not found"}, 404)
            if not check_role(user, ["ADMIN", "MANAGER"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-MES-026: COMPENSATION - Orphan BOM lines not cleaned
            del STORE.boms[bid]
            self._json({"deleted": bid})

    def _delete_bom_line(self, user, lid):
        with STORE.lock:
            if not STORE.bom_lines.get(lid):
                return self._json({"error": "BOM line not found"}, 404)
            if not check_role(user, ["ADMIN", "MANAGER", "PLANNER"]):
                return self._json({"error": "Forbidden"}, 403)
            del STORE.bom_lines[lid]
            self._json({"deleted": lid})

    # === Work Centers ===
    def _list_work_centers(self, user):
        with STORE.lock:
            wcs = list(STORE.work_centers.values())
            # BUG-MES-005: SCOPE_ISOLATION - All orgs visible
            self._json({"work_centers": wcs, "total": len(wcs)})

    def _get_work_center(self, user, wid):
        with STORE.lock:
            w = STORE.work_centers.get(wid)
            if not w:
                return self._json({"error": "Work center not found"}, 404)
            self._json(w)

    def _create_work_center(self, user, body):
        with STORE.lock:
            if not check_role(user, ["ADMIN", "MANAGER"]):
                return self._json({"error": "Forbidden"}, 403)
            wid = gen_id("wc")
            wc = {"id": wid, "name": body.get("name", ""), "org": user["org"], "factory": body.get("factory", user.get("factory", "")), "capacity_hours_per_day": body.get("capacity_hours_per_day", 8), "status": "ACTIVE", "created_at": STORE._now()}
            STORE.work_centers[wid] = wc
            self._json(wc, 201)

    # === Routings ===
    def _list_routings(self, user):
        with STORE.lock:
            self._json({"routings": list(STORE.routings.values()), "total": len(STORE.routings)})

    def _get_routing(self, user, rid):
        with STORE.lock:
            r = STORE.routings.get(rid)
            if not r:
                return self._json({"error": "Routing not found"}, 404)
            self._json(r)

    def _get_routing_steps(self, user, rid):
        with STORE.lock:
            steps = sorted([s for s in STORE.routing_steps.values() if s["routing_id"] == rid], key=lambda x: x["seq"])
            self._json({"routing_id": rid, "steps": steps, "total": len(steps)})

    def _create_routing(self, user, body):
        with STORE.lock:
            if not check_role(user, ["PLANNER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            rid = gen_id("rt")
            routing = {"id": rid, "product_id": body.get("product_id", ""), "org": user["org"], "version": body.get("version", "1.0"), "status": "ACTIVE", "created_at": STORE._now()}
            STORE.routings[rid] = routing
            self._json(routing, 201)

    def _add_routing_step(self, user, rid, body):
        with STORE.lock:
            if not STORE.routings.get(rid):
                return self._json({"error": "Routing not found"}, 404)
            if not check_role(user, ["PLANNER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            sid = gen_id("rs")
            step = {"id": sid, "routing_id": rid, "seq": body.get("seq", 10), "name": body.get("name", ""), "work_center_id": body.get("work_center_id", ""), "setup_time_min": body.get("setup_time_min", 0), "run_time_min_per_unit": body.get("run_time_min_per_unit", 0)}
            STORE.routing_steps[sid] = step
            self._json(step, 201)

    # === Sales Orders ===
    def _list_sales_orders(self, user):
        with STORE.lock:
            self._json({"sales_orders": list(STORE.sales_orders.values()), "total": len(STORE.sales_orders)})

    def _get_sales_order(self, user, sid):
        with STORE.lock:
            so = STORE.sales_orders.get(sid)
            if not so:
                return self._json({"error": "Sales order not found"}, 404)
            self._json(so)

    def _create_sales_order(self, user, body):
        with STORE.lock:
            if not check_role(user, ["PLANNER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-MES-023: IDEMPOTENCY - No duplicate order_ref check
            sid = gen_id("so")
            so = {"id": sid, "order_ref": body.get("order_ref", f"SO-{uuid.uuid4().hex[:6]}"), "customer": body.get("customer", ""), "org": user["org"], "product_id": body.get("product_id", ""), "quantity": body.get("quantity", 0), "delivery_date": body.get("delivery_date", ""), "status": "CREATED", "created_at": STORE._now(), "version": 1}
            STORE.sales_orders[sid] = so
            self._json(so, 201)

    def _update_sales_order(self, user, sid, body):
        with STORE.lock:
            so = STORE.sales_orders.get(sid)
            if not so:
                return self._json({"error": "Sales order not found"}, 404)
            # BUG-MES-028: TEMPORAL - Can modify after plan confirmed
            if "quantity" in body:
                so["quantity"] = body["quantity"]
            if "delivery_date" in body:
                so["delivery_date"] = body["delivery_date"]
            if "status" in body:
                so["status"] = body["status"]
            so["version"] = so.get("version", 1) + 1
            self._json(so)

    # === Production Plans ===
    def _list_production_plans(self, user):
        with STORE.lock:
            self._json({"production_plans": list(STORE.production_plans.values()), "total": len(STORE.production_plans)})

    def _get_production_plan(self, user, pid):
        with STORE.lock:
            pp = STORE.production_plans.get(pid)
            if not pp:
                return self._json({"error": "Production plan not found"}, 404)
            self._json(pp)

    def _create_production_plan(self, user, body):
        with STORE.lock:
            if not check_role(user, ["PLANNER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            pid = gen_id("pp")
            pp = {"id": pid, "sales_order_id": body.get("sales_order_id", ""), "org": user["org"], "factory": body.get("factory", user.get("factory", "")), "product_id": body.get("product_id", ""), "planned_quantity": body.get("planned_quantity", 0), "planned_start": body.get("planned_start", ""), "planned_end": body.get("planned_end", ""), "status": "CREATED", "created_by": user["id"], "created_at": STORE._now(), "version": 1}
            STORE.production_plans[pid] = pp
            self._json(pp, 201)

    def _confirm_plan(self, user, pid):
        with STORE.lock:
            pp = STORE.production_plans.get(pid)
            if not pp:
                return self._json({"error": "Production plan not found"}, 404)
            if not check_role(user, ["PLANNER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            if pp["status"] != "CREATED":
                return self._json({"error": f"Cannot confirm in status {pp['status']}"}, 409)
            pp["status"] = "CONFIRMED"
            pp["version"] = pp.get("version", 1) + 1
            self._json(pp)

    def _update_plan(self, user, pid, body):
        with STORE.lock:
            pp = STORE.production_plans.get(pid)
            if not pp:
                return self._json({"error": "Production plan not found"}, 404)
            if not check_role(user, ["PLANNER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-MES-009: STATE_TRANSITION - Can modify confirmed plan
            if "planned_quantity" in body:
                pp["planned_quantity"] = body["planned_quantity"]
            if "planned_start" in body:
                pp["planned_start"] = body["planned_start"]
            if "planned_end" in body:
                pp["planned_end"] = body["planned_end"]
            pp["version"] = pp.get("version", 1) + 1
            self._json(pp)

    # === Work Orders ===
    def _list_work_orders(self, user, params):
        with STORE.lock:
            wos = list(STORE.work_orders.values())
            status = params.get("status", [None])[0]
            if status:
                wos = [w for w in wos if w["status"] == status]
            self._json({"work_orders": wos, "total": len(wos)})

    def _get_work_order(self, user, wid):
        with STORE.lock:
            wo = STORE.work_orders.get(wid)
            if not wo:
                return self._json({"error": "Work order not found"}, 404)
            # BUG-MES-006: RESOURCE_OWNERSHIP - No org check
            self._json(wo)

    def _get_wo_operations(self, user, wid):
        with STORE.lock:
            ops = sorted([o for o in STORE.work_order_operations.values() if o["work_order_id"] == wid], key=lambda x: x["seq"])
            self._json({"work_order_id": wid, "operations": ops, "total": len(ops)})

    def _create_work_order(self, user, body):
        with STORE.lock:
            if not check_role(user, ["PLANNER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-MES-013: CROSS_ENTITY_PRECONDITION - No BOM/Routing check
            # BUG-MES-017: CROSS_ENTITY_CONSISTENCY - No plan quantity check
            # BUG-MES-008: TEMPORAL - No planned_start < planned_end validation
            wid = gen_id("wo")
            wo = {"id": wid, "order_ref": body.get("order_ref", f"WO-{uuid.uuid4().hex[:6]}"), "production_plan_id": body.get("production_plan_id", ""), "product_id": body.get("product_id", ""), "bom_id": body.get("bom_id", ""), "routing_id": body.get("routing_id", ""), "org": user["org"], "factory": body.get("factory", user.get("factory", "")), "planned_quantity": body.get("planned_quantity", 0), "completed_quantity": 0, "status": "CREATED", "priority": body.get("priority", 3), "planned_start": body.get("planned_start", ""), "planned_end": body.get("planned_end", ""), "created_by": user["id"], "created_at": STORE._now(), "released_at": None, "version": 1}
            STORE.work_orders[wid] = wo
            routing_id = body.get("routing_id", "")
            if routing_id:
                steps = sorted([s for s in STORE.routing_steps.values() if s["routing_id"] == routing_id], key=lambda x: x["seq"])
                for step in steps:
                    opid = gen_id("woo")
                    STORE.work_order_operations[opid] = {"id": opid, "work_order_id": wid, "routing_step_id": step["id"], "seq": step["seq"], "name": step["name"], "work_center_id": step["work_center_id"], "status": "PENDING", "reported_quantity": 0, "started_at": None, "completed_at": None}
            self._json(wo, 201)

    def _release_wo(self, user, wid):
        with STORE.lock:
            wo = STORE.work_orders.get(wid)
            if not wo:
                return self._json({"error": "Work order not found"}, 404)
            if not check_role(user, ["PLANNER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-MES-010: STATE_TRANSITION - Can release COMPLETED orders
            if wo["status"] in ["CLOSED", "CANCELLED"]:
                return self._json({"error": f"Cannot release in {wo['status']}"}, 409)
            wo["status"] = "RELEASED"
            wo["released_at"] = STORE._now()
            wo["version"] = wo.get("version", 1) + 1
            self._json(wo)

    def _start_wo(self, user, wid):
        with STORE.lock:
            wo = STORE.work_orders.get(wid)
            if not wo:
                return self._json({"error": "Work order not found"}, 404)
            if not check_role(user, ["OPERATOR", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            if wo["status"] != "RELEASED":
                return self._json({"error": f"Cannot start in {wo['status']}"}, 409)
            # BUG-MES-014: CROSS_ENTITY_PRECONDITION - No material check
            wo["status"] = "IN_PRODUCTION"
            wo["version"] = wo.get("version", 1) + 1
            self._json(wo)

    def _complete_wo(self, user, wid):
        with STORE.lock:
            wo = STORE.work_orders.get(wid)
            if not wo:
                return self._json({"error": "Work order not found"}, 404)
            if not check_role(user, ["OPERATOR", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            if wo["status"] != "IN_PRODUCTION":
                return self._json({"error": f"Cannot complete in {wo['status']}"}, 409)
            # BUG-MES-015: CROSS_ENTITY_CONSISTENCY - No operation completion check
            wo["status"] = "COMPLETED"
            wo["completed_quantity"] = wo["planned_quantity"]
            wo["version"] = wo.get("version", 1) + 1
            self._json(wo)

    def _close_wo(self, user, wid):
        with STORE.lock:
            wo = STORE.work_orders.get(wid)
            if not wo:
                return self._json({"error": "Work order not found"}, 404)
            if not check_role(user, ["MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-MES-007: STATE_TRANSITION - Can close from IN_PRODUCTION (skip COMPLETED)
            if wo["status"] not in ["COMPLETED", "IN_PRODUCTION"]:
                return self._json({"error": f"Cannot close in {wo['status']}"}, 409)
            wo["status"] = "CLOSED"
            wo["version"] = wo.get("version", 1) + 1
            self._json(wo)

    def _cancel_wo(self, user, wid):
        with STORE.lock:
            wo = STORE.work_orders.get(wid)
            if not wo:
                return self._json({"error": "Work order not found"}, 404)
            if not check_role(user, ["PLANNER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            if wo["status"] in ["COMPLETED", "CLOSED"]:
                return self._json({"error": f"Cannot cancel in {wo['status']}"}, 409)
            # BUG-MES-025: COMPENSATION - No reservation release on cancel
            wo["status"] = "CANCELLED"
            wo["version"] = wo.get("version", 1) + 1
            self._json(wo)

    def _update_wo(self, user, wid, body):
        with STORE.lock:
            wo = STORE.work_orders.get(wid)
            if not wo:
                return self._json({"error": "Work order not found"}, 404)
            if not check_role(user, ["PLANNER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-MES-029: CONCURRENCY - No version check
            if "planned_quantity" in body:
                wo["planned_quantity"] = body["planned_quantity"]
            if "priority" in body:
                wo["priority"] = body["priority"]
            wo["version"] = wo.get("version", 1) + 1
            self._json(wo)

    def _delete_wo(self, user, wid):
        with STORE.lock:
            wo = STORE.work_orders.get(wid)
            if not wo:
                return self._json({"error": "Work order not found"}, 404)
            # BUG-MES-003: ACTOR_AUTHORIZATION - Operator can delete
            if not check_role(user, ["ADMIN", "MANAGER", "PLANNER", "OPERATOR"]):
                return self._json({"error": "Forbidden"}, 403)
            if wo["status"] not in ["CREATED", "CANCELLED"]:
                return self._json({"error": "Can only delete CREATED/CANCELLED"}, 409)
            del STORE.work_orders[wid]
            self._json({"deleted": wid})

    def _bulk_release(self, user, body):
        with STORE.lock:
            if not check_role(user, ["PLANNER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            wo_ids = body.get("work_order_ids", [])
            results = []
            # BUG-MES-031: BATCH_OPERATION - Partial failure not rolled back
            for wo_id in wo_ids:
                wo = STORE.work_orders.get(wo_id)
                if wo and wo["status"] == "CREATED":
                    wo["status"] = "RELEASED"
                    wo["released_at"] = STORE._now()
                    wo["version"] = wo.get("version", 1) + 1
                    results.append({"id": wo_id, "status": "RELEASED"})
                else:
                    results.append({"id": wo_id, "status": "FAILED"})
            self._json({"results": results, "total": len(results)})

    # === Material Reservations ===
    def _list_reservations(self, user, params):
        with STORE.lock:
            res = list(STORE.material_reservations.values())
            wo_id = params.get("work_order_id", [None])[0]
            if wo_id:
                res = [r for r in res if r["work_order_id"] == wo_id]
            self._json({"reservations": res, "total": len(res)})

    def _get_reservation(self, user, rid):
        with STORE.lock:
            r = STORE.material_reservations.get(rid)
            if not r:
                return self._json({"error": "Reservation not found"}, 404)
            self._json(r)

    def _create_reservation(self, user, body):
        with STORE.lock:
            if not check_role(user, ["PLANNER", "WAREHOUSE", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            rid = gen_id("mr")
            reservation = {"id": rid, "work_order_id": body.get("work_order_id", ""), "material_id": body.get("material_id", ""), "required_quantity": body.get("required_quantity", 0), "reserved_quantity": body.get("required_quantity", 0), "issued_quantity": 0, "org": user["org"], "status": "RESERVED", "created_at": STORE._now()}
            STORE.material_reservations[rid] = reservation
            self._json(reservation, 201)

    def _release_reservation(self, user, rid):
        with STORE.lock:
            r = STORE.material_reservations.get(rid)
            if not r:
                return self._json({"error": "Reservation not found"}, 404)
            if not check_role(user, ["PLANNER", "WAREHOUSE", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            r["status"] = "RELEASED"
            r["reserved_quantity"] = 0
            self._json(r)

    # === Material Issues ===
    def _list_material_issues(self, user, params):
        with STORE.lock:
            issues = list(STORE.material_issues.values())
            wo_id = params.get("work_order_id", [None])[0]
            if wo_id:
                issues = [i for i in issues if i["work_order_id"] == wo_id]
            self._json({"material_issues": issues, "total": len(issues)})

    def _get_material_issue(self, user, mid):
        with STORE.lock:
            mi = STORE.material_issues.get(mid)
            if not mi:
                return self._json({"error": "Material issue not found"}, 404)
            self._json(mi)

    def _create_material_issue(self, user, body):
        with STORE.lock:
            if not check_role(user, ["WAREHOUSE", "OPERATOR", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-MES-019: CONSERVATION - No qty <= reserved check
            mid = gen_id("mi")
            issue = {"id": mid, "work_order_id": body.get("work_order_id", ""), "reservation_id": body.get("reservation_id", ""), "material_id": body.get("material_id", ""), "quantity": body.get("quantity", 0), "org": user["org"], "factory": user.get("factory", ""), "status": "CREATED", "issued_by": user["id"], "issued_at": None, "created_at": STORE._now(), "version": 1}
            STORE.material_issues[mid] = issue
            self._json(issue, 201)

    def _pick_issue(self, user, mid):
        with STORE.lock:
            mi = STORE.material_issues.get(mid)
            if not mi:
                return self._json({"error": "Material issue not found"}, 404)
            if not check_role(user, ["WAREHOUSE", "OPERATOR", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            if mi["status"] != "CREATED":
                return self._json({"error": f"Cannot pick in {mi['status']}"}, 409)
            mi["status"] = "PICKED"
            mi["issued_at"] = STORE._now()
            mi["version"] = mi.get("version", 1) + 1
            res = STORE.material_reservations.get(mi.get("reservation_id", ""))
            if res:
                res["issued_quantity"] = res.get("issued_quantity", 0) + mi["quantity"]
            self._json(mi)

    def _return_issue(self, user, mid):
        with STORE.lock:
            mi = STORE.material_issues.get(mid)
            if not mi:
                return self._json({"error": "Material issue not found"}, 404)
            if not check_role(user, ["WAREHOUSE", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            if mi["status"] != "PICKED":
                return self._json({"error": f"Cannot return in {mi['status']}"}, 409)
            # BUG-MES-030: CONCURRENCY - No version check
            mi["status"] = "RETURNED"
            mi["version"] = mi.get("version", 1) + 1
            res = STORE.material_reservations.get(mi.get("reservation_id", ""))
            if res:
                res["issued_quantity"] = max(0, res.get("issued_quantity", 0) - mi["quantity"])
            self._json(mi)

    def _bulk_issue(self, user, body):
        with STORE.lock:
            if not check_role(user, ["WAREHOUSE", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            items = body.get("items", [])
            results = []
            # BUG-MES-032: BATCH_OPERATION - No atomicity
            for item in items:
                mid = gen_id("mi")
                issue = {"id": mid, "work_order_id": item.get("work_order_id", ""), "reservation_id": item.get("reservation_id", ""), "material_id": item.get("material_id", ""), "quantity": item.get("quantity", 0), "org": user["org"], "factory": user.get("factory", ""), "status": "CREATED", "issued_by": user["id"], "issued_at": None, "created_at": STORE._now(), "version": 1}
                STORE.material_issues[mid] = issue
                results.append(issue)
            self._json({"issued": results, "total": len(results)}, 201)

    # === Work Reports ===
    def _list_work_reports(self, user, params):
        with STORE.lock:
            reports = list(STORE.work_reports.values())
            wo_id = params.get("work_order_id", [None])[0]
            if wo_id:
                reports = [r for r in reports if r["work_order_id"] == wo_id]
            self._json({"work_reports": reports, "total": len(reports)})

    def _get_work_report(self, user, rid):
        with STORE.lock:
            r = STORE.work_reports.get(rid)
            if not r:
                return self._json({"error": "Work report not found"}, 404)
            self._json(r)

    def _create_work_report(self, user, body):
        with STORE.lock:
            if not check_role(user, ["OPERATOR", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-MES-020: CONSERVATION - No qty limit check
            # BUG-MES-004: ACTOR_AUTHORIZATION - No factory scope check
            op_id = body.get("operation_id", "")
            op = STORE.work_order_operations.get(op_id)
            if op and op["status"] == "PENDING":
                op["status"] = "IN_PROGRESS"
                op["started_at"] = STORE._now()
            rid = gen_id("wr")
            report = {"id": rid, "work_order_id": body.get("work_order_id", ""), "operation_id": op_id, "quantity": body.get("quantity", 0), "defect_quantity": body.get("defect_quantity", 0), "org": user["org"], "factory": user.get("factory", ""), "reported_by": user["id"], "reported_at": STORE._now(), "shift": body.get("shift", "DAY"), "notes": body.get("notes", "")}
            STORE.work_reports[rid] = report
            if op:
                op["reported_quantity"] = op.get("reported_quantity", 0) + body.get("quantity", 0)
            self._json(report, 201)

    # === Quality Inspections ===
    def _list_inspections(self, user, params):
        with STORE.lock:
            # BUG-MES-012: SCOPE_ISOLATION - No org filter on inspections
            inspections = list(STORE.quality_inspections.values())
            wo_id = params.get("work_order_id", [None])[0]
            if wo_id:
                inspections = [i for i in inspections if i["work_order_id"] == wo_id]
            self._json({"inspections": inspections, "total": len(inspections)})

    def _get_inspection(self, user, iid):
        with STORE.lock:
            insp = STORE.quality_inspections.get(iid)
            if not insp:
                return self._json({"error": "Inspection not found"}, 404)
            self._json(insp)

    def _create_inspection(self, user, body):
        with STORE.lock:
            if not check_role(user, ["INSPECTOR", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            iid = gen_id("qi")
            inspection = {"id": iid, "work_order_id": body.get("work_order_id", ""), "work_report_id": body.get("work_report_id", ""), "inspection_type": body.get("inspection_type", "FINAL"), "sample_size": body.get("sample_size", 0), "org": user["org"], "factory": user.get("factory", ""), "status": "PENDING", "inspector_id": user["id"], "result": None, "pass_quantity": 0, "fail_quantity": 0, "created_at": STORE._now(), "completed_at": None, "expiry_date": body.get("expiry_date", "2026-12-31"), "version": 1}
            STORE.quality_inspections[iid] = inspection
            self._json(inspection, 201)

    def _start_inspection(self, user, iid):
        with STORE.lock:
            insp = STORE.quality_inspections.get(iid)
            if not insp:
                return self._json({"error": "Inspection not found"}, 404)
            if not check_role(user, ["INSPECTOR", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            if insp["status"] != "PENDING":
                return self._json({"error": f"Cannot start in {insp['status']}"}, 409)
            insp["status"] = "IN_PROGRESS"
            insp["version"] = insp.get("version", 1) + 1
            self._json(insp)

    def _submit_inspection(self, user, iid, body):
        with STORE.lock:
            insp = STORE.quality_inspections.get(iid)
            if not insp:
                return self._json({"error": "Inspection not found"}, 404)
            if not check_role(user, ["INSPECTOR", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            if insp["status"] != "IN_PROGRESS":
                return self._json({"error": f"Cannot submit in {insp['status']}"}, 409)
            # BUG-MES-027: TEMPORAL - No expiry check
            # BUG-MES-021: CONSERVATION - pass+fail can exceed sample_size
            insp["result"] = body.get("result", "PASS")
            insp["pass_quantity"] = body.get("pass_quantity", 0)
            insp["fail_quantity"] = body.get("fail_quantity", 0)
            insp["status"] = "COMPLETED"
            insp["completed_at"] = STORE._now()
            insp["version"] = insp.get("version", 1) + 1
            self._json(insp)

    # === Rework Orders ===
    def _list_rework_orders(self, user):
        with STORE.lock:
            self._json({"rework_orders": list(STORE.rework_orders.values()), "total": len(STORE.rework_orders)})

    def _get_rework_order(self, user, rid):
        with STORE.lock:
            r = STORE.rework_orders.get(rid)
            if not r:
                return self._json({"error": "Rework order not found"}, 404)
            self._json(r)

    def _create_rework(self, user, body):
        with STORE.lock:
            if not check_role(user, ["INSPECTOR", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-MES-016: CROSS_ENTITY - No inspection REJECT check
            rid = gen_id("rwk")
            rework = {"id": rid, "inspection_id": body.get("inspection_id", ""), "work_order_id": body.get("work_order_id", ""), "quantity": body.get("quantity", 0), "reason": body.get("reason", ""), "org": user["org"], "factory": user.get("factory", ""), "status": "CREATED", "created_by": user["id"], "created_at": STORE._now(), "version": 1}
            STORE.rework_orders[rid] = rework
            self._json(rework, 201)

    def _start_rework(self, user, rid):
        with STORE.lock:
            r = STORE.rework_orders.get(rid)
            if not r:
                return self._json({"error": "Rework order not found"}, 404)
            if not check_role(user, ["OPERATOR", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            if r["status"] != "CREATED":
                return self._json({"error": f"Cannot start in {r['status']}"}, 409)
            r["status"] = "IN_PROGRESS"
            r["version"] = r.get("version", 1) + 1
            self._json(r)

    def _complete_rework(self, user, rid):
        with STORE.lock:
            r = STORE.rework_orders.get(rid)
            if not r:
                return self._json({"error": "Rework order not found"}, 404)
            if not check_role(user, ["OPERATOR", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            if r["status"] != "IN_PROGRESS":
                return self._json({"error": f"Cannot complete in {r['status']}"}, 409)
            r["status"] = "COMPLETED"
            r["version"] = r.get("version", 1) + 1
            self._json(r)

    # === Finished Goods Receipts ===
    def _list_receipts(self, user):
        with STORE.lock:
            self._json({"receipts": list(STORE.finished_goods_receipts.values()), "total": len(STORE.finished_goods_receipts)})

    def _get_receipt(self, user, rid):
        with STORE.lock:
            r = STORE.finished_goods_receipts.get(rid)
            if not r:
                return self._json({"error": "Receipt not found"}, 404)
            self._json(r)

    def _create_receipt(self, user, body):
        with STORE.lock:
            if not check_role(user, ["WAREHOUSE", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-MES-011: STATE_TRANSITION - No WO COMPLETED check
            # BUG-MES-018: CROSS_ENTITY - No quality pass check
            # BUG-MES-024: IDEMPOTENCY - No duplicate receipt check
            rid = gen_id("fgr")
            receipt = {"id": rid, "work_order_id": body.get("work_order_id", ""), "product_id": body.get("product_id", ""), "quantity": body.get("quantity", 0), "org": user["org"], "factory": user.get("factory", ""), "status": "CREATED", "received_by": user["id"], "created_at": STORE._now(), "version": 1}
            STORE.finished_goods_receipts[rid] = receipt
            self._json(receipt, 201)

    def _confirm_receipt(self, user, rid):
        with STORE.lock:
            r = STORE.finished_goods_receipts.get(rid)
            if not r:
                return self._json({"error": "Receipt not found"}, 404)
            if not check_role(user, ["WAREHOUSE", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            if r["status"] != "CREATED":
                return self._json({"error": f"Cannot confirm in {r['status']}"}, 409)
            r["status"] = "CONFIRMED"
            r["version"] = r.get("version", 1) + 1
            # BUG-MES-022: CONSERVATION - WO completed_quantity not updated
            self._json(r)


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    PORT = 8020
    server = ThreadingHTTPServer(("0.0.0.0", PORT), MESHandler)
    print(f"MES Mock Server running on port {PORT}")
    server.serve_forever()
