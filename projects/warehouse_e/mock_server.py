#!/usr/bin/env python3
"""
Warehouse Management System (WMS) - Mock Server
Project E Blind Generalization Benchmark Target

Domain: 仓储、库存、订单履约、出库、退货和补货
Entities: Product, Warehouse, InventoryBatch, InventoryReservation, Order, OrderLine,
          PickList, Shipment, Return, RestockOrder, Supplier
State Machines: Order lifecycle, InventoryBatch status, Return lifecycle, PickList lifecycle
Roles: WarehouseOperator, WarehouseManager, OrderManager, Customer, Admin, Auditor
Scope: Organization (acme/globex) + Warehouse (wh-001/wh-002/wh-003)
"""

import json
import uuid
import time
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ============================================================
# Authentication & Accounts
# ============================================================

ACCOUNTS = {
    "operator-omar-token": {"id": "op-001", "name": "Omar Zhang", "role": "OPERATOR", "org": "acme", "warehouse": "wh-001"},
    "operator-olga-token": {"id": "op-002", "name": "Olga Li", "role": "OPERATOR", "org": "acme", "warehouse": "wh-002"},
    "operator-oscar-token": {"id": "op-003", "name": "Oscar Wu", "role": "OPERATOR", "org": "globex", "warehouse": "wh-003"},
    "manager-mia-token": {"id": "mgr-001", "name": "Mia Chen", "role": "MANAGER", "org": "acme", "warehouse": "wh-001"},
    "manager-max-token": {"id": "mgr-002", "name": "Max Sun", "role": "MANAGER", "org": "globex", "warehouse": "wh-003"},
    "ordermgr-nina-token": {"id": "om-001", "name": "Nina Zhao", "role": "ORDER_MANAGER", "org": "acme", "warehouse": "wh-001"},
    "ordermgr-noah-token": {"id": "om-002", "name": "Noah Xu", "role": "ORDER_MANAGER", "org": "globex", "warehouse": "wh-003"},
    "customer-cara-token": {"id": "cust-001", "name": "Cara Wang", "role": "CUSTOMER", "org": "acme", "warehouse": None},
    "customer-carl-token": {"id": "cust-002", "name": "Carl Liu", "role": "CUSTOMER", "org": "globex", "warehouse": None},
    "admin-alex-token": {"id": "adm-001", "name": "Alex Zhou", "role": "ADMIN", "org": "acme", "warehouse": None},
    "admin-anna-token": {"id": "adm-002", "name": "Anna Huang", "role": "ADMIN", "org": "globex", "warehouse": None},
    "auditor-ava-token": {"id": "aud-001", "name": "Ava Yang", "role": "AUDITOR", "org": "acme", "warehouse": None},
}

# ============================================================
# Data Store
# ============================================================

class DataStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        self.products = {}
        self.warehouses = {}
        self.batches = {}
        self.reservations = {}
        self.orders = {}
        self.order_lines = {}
        self.pick_lists = {}
        self.shipments = {}
        self.returns = {}
        self.restock_orders = {}
        self.suppliers = {}
        self._init_data()

    def _now(self):
        return datetime.utcnow().isoformat() + "Z"

    def _init_data(self):
        # Suppliers
        self.suppliers["sup-001"] = {"id": "sup-001", "name": "Shenzhen Parts Co", "org": "acme", "lead_time_days": 7, "status": "ACTIVE", "created_at": self._now()}
        self.suppliers["sup-002"] = {"id": "sup-002", "name": "Guangzhou Materials Ltd", "org": "acme", "lead_time_days": 5, "status": "ACTIVE", "created_at": self._now()}
        self.suppliers["sup-003"] = {"id": "sup-003", "name": "Beijing Components Inc", "org": "globex", "lead_time_days": 10, "status": "ACTIVE", "created_at": self._now()}

        # Warehouses
        self.warehouses["wh-001"] = {"id": "wh-001", "name": "ACME East Warehouse", "org": "acme", "capacity": 10000, "used_capacity": 3500, "status": "ACTIVE", "created_at": self._now()}
        self.warehouses["wh-002"] = {"id": "wh-002", "name": "ACME West Warehouse", "org": "acme", "capacity": 8000, "used_capacity": 2000, "status": "ACTIVE", "created_at": self._now()}
        self.warehouses["wh-003"] = {"id": "wh-003", "name": "Globex Central Warehouse", "org": "globex", "capacity": 12000, "used_capacity": 4000, "status": "ACTIVE", "created_at": self._now()}

        # Products
        self.products["prod-001"] = {"id": "prod-001", "sku": "SKU-ELEC-001", "name": "Circuit Board A", "org": "acme", "unit_price": 45.50, "weight_kg": 0.3, "category": "ELECTRONICS", "status": "ACTIVE", "created_at": self._now()}
        self.products["prod-002"] = {"id": "prod-002", "sku": "SKU-ELEC-002", "name": "Sensor Module B", "org": "acme", "unit_price": 120.00, "weight_kg": 0.5, "category": "ELECTRONICS", "status": "ACTIVE", "created_at": self._now()}
        self.products["prod-003"] = {"id": "prod-003", "sku": "SKU-MECH-001", "name": "Gear Assembly C", "org": "acme", "unit_price": 78.00, "weight_kg": 1.2, "category": "MECHANICAL", "status": "ACTIVE", "created_at": self._now()}
        self.products["prod-004"] = {"id": "prod-004", "sku": "SKU-PKG-001", "name": "Packaging Box D", "org": "globex", "unit_price": 5.00, "weight_kg": 0.1, "category": "PACKAGING", "status": "ACTIVE", "created_at": self._now()}
        self.products["prod-005"] = {"id": "prod-005", "sku": "SKU-PKG-002", "name": "Protective Wrap E", "org": "globex", "unit_price": 3.50, "weight_kg": 0.05, "category": "PACKAGING", "status": "ACTIVE", "created_at": self._now()}

        # Inventory Batches
        self.batches["batch-001"] = {"id": "batch-001", "product_id": "prod-001", "warehouse_id": "wh-001", "org": "acme", "quantity": 500, "reserved_quantity": 50, "status": "AVAILABLE", "received_at": self._now(), "expiry_date": "2027-01-01", "version": 1}
        self.batches["batch-002"] = {"id": "batch-002", "product_id": "prod-002", "warehouse_id": "wh-001", "org": "acme", "quantity": 200, "reserved_quantity": 20, "status": "AVAILABLE", "received_at": self._now(), "expiry_date": "2026-12-01", "version": 1}
        self.batches["batch-003"] = {"id": "batch-003", "product_id": "prod-003", "warehouse_id": "wh-002", "org": "acme", "quantity": 150, "reserved_quantity": 0, "status": "AVAILABLE", "received_at": self._now(), "expiry_date": "2027-06-01", "version": 1}
        self.batches["batch-004"] = {"id": "batch-004", "product_id": "prod-004", "warehouse_id": "wh-003", "org": "globex", "quantity": 1000, "reserved_quantity": 100, "status": "AVAILABLE", "received_at": self._now(), "expiry_date": "2027-03-01", "version": 1}
        self.batches["batch-005"] = {"id": "batch-005", "product_id": "prod-005", "warehouse_id": "wh-003", "org": "globex", "quantity": 2000, "reserved_quantity": 0, "status": "RECEIVED", "received_at": self._now(), "expiry_date": "2027-09-01", "version": 1}

        # Orders
        self.orders["ord-001"] = {"id": "ord-001", "order_ref": "ORD-2026-001", "customer_id": "cust-001", "org": "acme", "warehouse_id": "wh-001", "status": "CONFIRMED", "total_amount": 575.00, "created_by": "om-001", "created_at": self._now(), "updated_at": self._now(), "confirmed_at": self._now(), "return_deadline": "2026-08-01", "version": 1}
        self.orders["ord-002"] = {"id": "ord-002", "order_ref": "ORD-2026-002", "customer_id": "cust-002", "org": "globex", "warehouse_id": "wh-003", "status": "CREATED", "total_amount": 50.00, "created_by": "om-002", "created_at": self._now(), "updated_at": self._now(), "confirmed_at": None, "return_deadline": "2026-08-15", "version": 1}
        self.orders["ord-003"] = {"id": "ord-003", "order_ref": "ORD-2026-003", "customer_id": "cust-001", "org": "acme", "warehouse_id": "wh-001", "status": "ALLOCATED", "total_amount": 240.00, "created_by": "om-001", "created_at": self._now(), "updated_at": self._now(), "confirmed_at": self._now(), "return_deadline": "2026-07-30", "version": 1}

        # Order Lines
        self.order_lines["ol-001"] = {"id": "ol-001", "order_id": "ord-001", "product_id": "prod-001", "quantity": 10, "unit_price": 45.50, "line_total": 455.00}
        self.order_lines["ol-002"] = {"id": "ol-001b", "order_id": "ord-001", "product_id": "prod-002", "quantity": 1, "unit_price": 120.00, "line_total": 120.00}
        self.order_lines["ol-003"] = {"id": "ol-003", "order_id": "ord-002", "product_id": "prod-004", "quantity": 10, "unit_price": 5.00, "line_total": 50.00}
        self.order_lines["ol-004"] = {"id": "ol-004", "order_id": "ord-003", "product_id": "prod-002", "quantity": 2, "unit_price": 120.00, "line_total": 240.00}

        # Reservations
        self.reservations["res-001"] = {"id": "res-001", "order_id": "ord-003", "batch_id": "batch-002", "product_id": "prod-002", "quantity": 2, "org": "acme", "status": "ACTIVE", "created_at": self._now()}

        # Pick Lists
        self.pick_lists["pick-001"] = {"id": "pick-001", "order_id": "ord-003", "warehouse_id": "wh-001", "org": "acme", "status": "CREATED", "items": [{"product_id": "prod-002", "quantity": 2, "batch_id": "batch-002"}], "created_by": "op-001", "created_at": self._now()}

        # Shipments (empty initially)
        # Returns (empty initially)
        # Restock Orders (empty initially)

STORE = DataStore()

# ============================================================
# Helpers
# ============================================================

def gen_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"

def authenticate(headers):
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return ACCOUNTS.get(auth[7:])
    return None

def check_role(user, roles):
    return user and user.get("role") in roles

def check_org(user, org):
    return user and user.get("org") == org

# ============================================================
# Request Handler
# ============================================================

class WMSHandler(BaseHTTPRequestHandler):
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

    # ─── GET ────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "/health":
            return self._json({"status": "healthy", "service": "wms"})
        user = authenticate(self.headers)
        if not user:
            return self._json({"error": "Unauthorized"}, 401)
        params = parse_qs(parsed.query)

        # Products
        if path == "/products":
            return self._list_products(user, params)
        if path.startswith("/products/") and path.count("/") == 2:
            return self._get_product(user, path.split("/")[2])
        # Warehouses
        if path == "/warehouses":
            return self._list_warehouses(user)
        if path.startswith("/warehouses/") and path.count("/") == 2:
            return self._get_warehouse(user, path.split("/")[2])
        # Batches
        if path == "/batches":
            return self._list_batches(user, params)
        if path.startswith("/batches/") and path.count("/") == 2:
            return self._get_batch(user, path.split("/")[2])
        # Orders
        if path == "/orders":
            return self._list_orders(user, params)
        if path.startswith("/orders/") and path.count("/") == 2:
            return self._get_order(user, path.split("/")[2])
        if path.startswith("/orders/") and path.endswith("/lines"):
            return self._get_order_lines(user, path.split("/")[2])
        # Pick Lists
        if path == "/pick-lists":
            return self._list_pick_lists(user)
        if path.startswith("/pick-lists/") and path.count("/") == 2:
            return self._get_pick_list(user, path.split("/")[2])
        # Shipments
        if path == "/shipments":
            return self._list_shipments(user)
        if path.startswith("/shipments/") and path.count("/") == 2:
            return self._get_shipment(user, path.split("/")[2])
        # Returns
        if path == "/returns":
            return self._list_returns(user)
        if path.startswith("/returns/") and path.count("/") == 2:
            return self._get_return(user, path.split("/")[2])
        # Restock Orders
        if path == "/restock-orders":
            return self._list_restock_orders(user)
        # Suppliers
        if path == "/suppliers":
            return self._list_suppliers(user)
        # Reservations
        if path == "/reservations":
            return self._list_reservations(user)

        self._json({"error": "Not found"}, 404)

    # ─── POST ───────────────────────────────────────────────────────────────
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        user = authenticate(self.headers)
        if not user:
            return self._json({"error": "Unauthorized"}, 401)
        body = self._body()

        # Products
        if path == "/products":
            return self._create_product(user, body)
        # Orders
        if path == "/orders":
            return self._create_order(user, body)
        if path.startswith("/orders/") and path.endswith("/confirm"):
            return self._confirm_order(user, path.split("/")[2], body)
        if path.startswith("/orders/") and path.endswith("/allocate"):
            return self._allocate_order(user, path.split("/")[2], body)
        if path.startswith("/orders/") and path.endswith("/cancel"):
            return self._cancel_order(user, path.split("/")[2], body)
        if path.startswith("/orders/") and path.endswith("/lines"):
            return self._add_order_line(user, path.split("/")[2], body)
        # Pick Lists
        if path == "/pick-lists":
            return self._create_pick_list(user, body)
        if path.startswith("/pick-lists/") and path.endswith("/start"):
            return self._start_pick_list(user, path.split("/")[2])
        if path.startswith("/pick-lists/") and path.endswith("/complete"):
            return self._complete_pick_list(user, path.split("/")[2])
        if path.startswith("/pick-lists/") and path.endswith("/cancel"):
            return self._cancel_pick_list(user, path.split("/")[2])
        # Shipments
        if path == "/shipments":
            return self._create_shipment(user, body)
        if path.startswith("/shipments/") and path.endswith("/confirm"):
            return self._confirm_shipment(user, path.split("/")[2])
        # Returns
        if path == "/returns":
            return self._create_return(user, body)
        if path.startswith("/returns/") and path.endswith("/approve"):
            return self._approve_return(user, path.split("/")[2])
        if path.startswith("/returns/") and path.endswith("/reject"):
            return self._reject_return(user, path.split("/")[2])
        if path.startswith("/returns/") and path.endswith("/receive"):
            return self._receive_return(user, path.split("/")[2])
        if path.startswith("/returns/") and path.endswith("/refund"):
            return self._refund_return(user, path.split("/")[2])
        # Restock
        if path == "/restock-orders":
            return self._create_restock_order(user, body)
        # Reservations
        if path == "/reservations":
            return self._create_reservation(user, body)
        # Batch operations
        if path == "/batches/bulk-receive":
            return self._bulk_receive_batches(user, body)
        if path == "/orders/bulk-allocate":
            return self._bulk_allocate_orders(user, body)
        # Warehouses
        if path == "/warehouses":
            return self._create_warehouse(user, body)

        self._json({"error": "Not found"}, 404)

    # ─── PUT ────────────────────────────────────────────────────────────────
    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        user = authenticate(self.headers)
        if not user:
            return self._json({"error": "Unauthorized"}, 401)
        body = self._body()

        if path.startswith("/products/") and path.count("/") == 2:
            return self._update_product(user, path.split("/")[2], body)
        if path.startswith("/batches/") and path.count("/") == 2:
            return self._update_batch(user, path.split("/")[2], body)
        if path.startswith("/orders/") and path.count("/") == 2:
            return self._update_order(user, path.split("/")[2], body)

        self._json({"error": "Not found"}, 404)

    # ─── DELETE ─────────────────────────────────────────────────────────────
    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        user = authenticate(self.headers)
        if not user:
            return self._json({"error": "Unauthorized"}, 401)

        if path.startswith("/warehouses/") and path.count("/") == 2:
            return self._delete_warehouse(user, path.split("/")[2])
        if path.startswith("/orders/") and path.count("/") == 2:
            return self._delete_order(user, path.split("/")[2])

        self._json({"error": "Not found"}, 404)

    # ============================================================
    # Product Handlers
    # ============================================================

    def _list_products(self, user, params):
        with STORE.lock:
            products = list(STORE.products.values())
            if user["role"] == "CUSTOMER":
                products = [p for p in products if p["org"] == user["org"]]
            self._json({"products": products, "total": len(products)})

    def _get_product(self, user, pid):
        with STORE.lock:
            p = STORE.products.get(pid)
            if not p:
                return self._json({"error": "Product not found"}, 404)
            self._json(p)

    def _create_product(self, user, body):
        with STORE.lock:
            # BUG-WMS-007: ACTOR_AUTHORIZATION - Operator can create/modify products with prices
            # Should require: ADMIN or MANAGER role only
            if not check_role(user, ["ADMIN", "MANAGER", "OPERATOR", "ORDER_MANAGER"]):
                return self._json({"error": "Forbidden"}, 403)
            pid = gen_id("prod")
            product = {"id": pid, "sku": body.get("sku", ""), "name": body.get("name", ""),
                       "org": user["org"], "unit_price": body.get("unit_price", 0),
                       "weight_kg": body.get("weight_kg", 0), "category": body.get("category", "GENERAL"),
                       "status": "ACTIVE", "created_at": STORE._now()}
            STORE.products[pid] = product
            self._json(product, 201)

    def _update_product(self, user, pid, body):
        with STORE.lock:
            p = STORE.products.get(pid)
            if not p:
                return self._json({"error": "Product not found"}, 404)
            # BUG-WMS-007b: ACTOR_AUTHORIZATION - No role check on price modification
            # Should require ADMIN for price changes
            if "unit_price" in body:
                p["unit_price"] = body["unit_price"]
            if "name" in body:
                p["name"] = body["name"]
            if "status" in body:
                p["status"] = body["status"]
            self._json(p)

    # ============================================================
    # Warehouse Handlers
    # ============================================================

    def _list_warehouses(self, user):
        with STORE.lock:
            whs = list(STORE.warehouses.values())
            # BUG-WMS-010: TENANT_OR_SCOPE_ISOLATION - Can view all orgs' warehouses
            # Should filter: whs = [w for w in whs if w["org"] == user["org"]]
            self._json({"warehouses": whs, "total": len(whs)})

    def _get_warehouse(self, user, wid):
        with STORE.lock:
            w = STORE.warehouses.get(wid)
            if not w:
                return self._json({"error": "Warehouse not found"}, 404)
            # BUG-WMS-037: AGGREGATE - used_capacity never recalculated from actual batches
            # Should compute: sum of batch quantities in this warehouse
            self._json(w)

    def _create_warehouse(self, user, body):
        with STORE.lock:
            if not check_role(user, ["ADMIN"]):
                return self._json({"error": "Only admin can create warehouses"}, 403)
            wid = gen_id("wh")
            wh = {"id": wid, "name": body.get("name", ""), "org": user["org"],
                  "capacity": body.get("capacity", 5000), "used_capacity": 0,
                  "status": "ACTIVE", "created_at": STORE._now()}
            STORE.warehouses[wid] = wh
            self._json(wh, 201)

    def _delete_warehouse(self, user, wid):
        with STORE.lock:
            w = STORE.warehouses.get(wid)
            if not w:
                return self._json({"error": "Warehouse not found"}, 404)
            # BUG-WMS-009: ACTOR_AUTHORIZATION - Operator/Manager can delete warehouses
            # Should require: ADMIN only
            if not check_role(user, ["ADMIN", "MANAGER", "OPERATOR"]):
                return self._json({"error": "Forbidden"}, 403)
            del STORE.warehouses[wid]
            self._json({"deleted": wid})

    # ============================================================
    # Batch Handlers
    # ============================================================

    def _list_batches(self, user, params):
        with STORE.lock:
            batches = list(STORE.batches.values())
            # BUG-WMS-011: TENANT_OR_SCOPE_ISOLATION - Can view other org's inventory batches
            # Should filter: batches = [b for b in batches if b["org"] == user["org"]]
            wh_id = params.get("warehouse_id", [None])[0]
            if wh_id:
                batches = [b for b in batches if b["warehouse_id"] == wh_id]
            self._json({"batches": batches, "total": len(batches)})

    def _get_batch(self, user, bid):
        with STORE.lock:
            b = STORE.batches.get(bid)
            if not b:
                return self._json({"error": "Batch not found"}, 404)
            self._json(b)

    def _update_batch(self, user, bid, body):
        with STORE.lock:
            b = STORE.batches.get(bid)
            if not b:
                return self._json({"error": "Batch not found"}, 404)
            # BUG-WMS-027: CONCURRENCY - No version check (optimistic locking missing)
            # Should check: if body.get("version") != b["version"]: 409 Conflict
            if "quantity" in body:
                b["quantity"] = body["quantity"]
            if "status" in body:
                b["status"] = body["status"]
            b["version"] = b.get("version", 1) + 1
            self._json(b)

    def _bulk_receive_batches(self, user, body):
        with STORE.lock:
            if not check_role(user, ["OPERATOR", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            items = body.get("items", [])
            results = []
            for item in items:
                bid = gen_id("batch")
                batch = {"id": bid, "product_id": item.get("product_id", ""),
                         "warehouse_id": item.get("warehouse_id", ""), "org": user["org"],
                         "quantity": item.get("quantity", 0), "reserved_quantity": 0,
                         "status": "RECEIVED", "received_at": STORE._now(),
                         "expiry_date": item.get("expiry_date", "2027-01-01"), "version": 1}
                STORE.batches[bid] = batch
                results.append(batch)
            self._json({"received": results, "total": len(results)}, 201)

    # ============================================================
    # Order Handlers
    # ============================================================

    def _list_orders(self, user, params):
        with STORE.lock:
            orders = list(STORE.orders.values())
            if user["role"] == "CUSTOMER":
                orders = [o for o in orders if o["customer_id"] == user["id"]]
            status = params.get("status", [None])[0]
            if status:
                orders = [o for o in orders if o["status"] == status]
            self._json({"orders": orders, "total": len(orders)})

    def _get_order(self, user, oid):
        with STORE.lock:
            o = STORE.orders.get(oid)
            if not o:
                return self._json({"error": "Order not found"}, 404)
            # BUG-WMS-014: RESOURCE_OWNERSHIP - Any user can view/edit any order
            # Should check: if o["org"] != user["org"]: 403
            self._json(o)

    def _create_order(self, user, body):
        with STORE.lock:
            # BUG-WMS-008: ACTOR_AUTHORIZATION - Auditor can create orders (should be read-only)
            # Should exclude: AUDITOR role from order creation
            if not check_role(user, ["ORDER_MANAGER", "CUSTOMER", "ADMIN", "AUDITOR"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-WMS-024: IDEMPOTENCY - No duplicate order_ref check
            # Should check: if any existing order has same order_ref -> 409
            oid = gen_id("ord")
            order = {"id": oid, "order_ref": body.get("order_ref", f"ORD-{uuid.uuid4().hex[:6]}"),
                     "customer_id": body.get("customer_id", user["id"]),
                     "org": user["org"], "warehouse_id": body.get("warehouse_id", ""),
                     "status": "CREATED", "total_amount": 0,
                     "created_by": user["id"], "created_at": STORE._now(),
                     "updated_at": STORE._now(), "confirmed_at": None,
                     "return_deadline": body.get("return_deadline", "2026-08-01"), "version": 1}
            STORE.orders[oid] = order
            self._json(order, 201)

    def _confirm_order(self, user, oid, body):
        with STORE.lock:
            o = STORE.orders.get(oid)
            if not o:
                return self._json({"error": "Order not found"}, 404)
            # BUG-WMS-001: STATE_TRANSITION - Can confirm CANCELLED/SHIPPED orders
            # Should check: if o["status"] != "CREATED": 409
            if not check_role(user, ["ORDER_MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            o["status"] = "CONFIRMED"
            o["confirmed_at"] = STORE._now()
            o["updated_at"] = STORE._now()
            o["version"] += 1
            self._json(o)

    def _allocate_order(self, user, oid, body):
        with STORE.lock:
            o = STORE.orders.get(oid)
            if not o:
                return self._json({"error": "Order not found"}, 404)
            # BUG-WMS-002: STATE_TRANSITION - Can allocate non-CONFIRMED orders
            # Should check: if o["status"] != "CONFIRMED": 409
            if not check_role(user, ["ORDER_MANAGER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-WMS-023: CROSS_ENTITY_PRECONDITION - Allocates from non-AVAILABLE batches
            # Should verify batch status == AVAILABLE before reserving
            lines = [l for l in STORE.order_lines.values() if l["order_id"] == oid]
            for line in lines:
                batch = next((b for b in STORE.batches.values()
                              if b["product_id"] == line["product_id"] and b["warehouse_id"] == o["warehouse_id"]), None)
                if batch:
                    # BUG-WMS-017: CROSS_ENTITY_CONSISTENCY - No available quantity check
                    # Should check: batch["quantity"] - batch["reserved_quantity"] >= line["quantity"]
                    batch["reserved_quantity"] += line["quantity"]
                    rid = gen_id("res")
                    STORE.reservations[rid] = {"id": rid, "order_id": oid, "batch_id": batch["id"],
                                               "product_id": line["product_id"], "quantity": line["quantity"],
                                               "org": user["org"], "status": "ACTIVE", "created_at": STORE._now()}
            o["status"] = "ALLOCATED"
            o["updated_at"] = STORE._now()
            o["version"] += 1
            self._json(o)

    def _cancel_order(self, user, oid, body):
        with STORE.lock:
            o = STORE.orders.get(oid)
            if not o:
                return self._json({"error": "Order not found"}, 404)
            # BUG-WMS-005: STATE_TRANSITION - Can cancel SHIPPED/DELIVERED orders
            # Should check: if o["status"] in ("SHIPPED", "DELIVERED"): 409
            if not check_role(user, ["ORDER_MANAGER", "ADMIN", "CUSTOMER"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-WMS-014b: RESOURCE_OWNERSHIP - Customer can cancel other customer's order
            # Should check: if user["role"]=="CUSTOMER" and o["customer_id"] != user["id"]: 403
            # BUG-WMS-030: CONSERVATION - Reserved quantity not released on cancel
            # Should release: for each reservation of this order, batch.reserved_quantity -= qty
            o["status"] = "CANCELLED"
            o["updated_at"] = STORE._now()
            o["version"] += 1
            self._json(o)

    def _add_order_line(self, user, oid, body):
        with STORE.lock:
            o = STORE.orders.get(oid)
            if not o:
                return self._json({"error": "Order not found"}, 404)
            if o["status"] not in ("CREATED", "CONFIRMED"):
                return self._json({"error": "Cannot modify order in current status"}, 409)
            product = STORE.products.get(body.get("product_id", ""))
            if not product:
                return self._json({"error": "Product not found"}, 404)
            qty = body.get("quantity", 1)
            lid = gen_id("ol")
            line = {"id": lid, "order_id": oid, "product_id": product["id"],
                    "quantity": qty, "unit_price": product["unit_price"],
                    "line_total": qty * product["unit_price"]}
            STORE.order_lines[lid] = line
            # BUG-WMS-038: AGGREGATE - Order total not recalculated
            # Should recalculate: o["total_amount"] = sum of all line_totals
            self._json(line, 201)

    def _update_order(self, user, oid, body):
        with STORE.lock:
            o = STORE.orders.get(oid)
            if not o:
                return self._json({"error": "Order not found"}, 404)
            # BUG-WMS-014c: RESOURCE_OWNERSHIP - No ownership check on update
            if "warehouse_id" in body:
                o["warehouse_id"] = body["warehouse_id"]
            if "return_deadline" in body:
                o["return_deadline"] = body["return_deadline"]
            o["updated_at"] = STORE._now()
            self._json(o)

    def _delete_order(self, user, oid):
        with STORE.lock:
            o = STORE.orders.get(oid)
            if not o:
                return self._json({"error": "Order not found"}, 404)
            if not check_role(user, ["ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            if o["status"] not in ("CREATED", "CANCELLED"):
                return self._json({"error": "Can only delete CREATED/CANCELLED orders"}, 409)
            del STORE.orders[oid]
            self._json({"deleted": oid})

    def _get_order_lines(self, user, oid):
        with STORE.lock:
            lines = [l for l in STORE.order_lines.values() if l["order_id"] == oid]
            self._json({"lines": lines, "total": len(lines)})

    def _bulk_allocate_orders(self, user, body):
        with STORE.lock:
            if not check_role(user, ["ORDER_MANAGER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            order_ids = body.get("order_ids", [])
            results = []
            # BUG-WMS-039: BATCH_OPERATION - Partial failure not rolled back
            # If 3rd order fails, first 2 remain allocated (no rollback)
            # BUG-WMS-040: BATCH_OPERATION - Skips per-order validation (no line check)
            # Should validate each order has lines and available inventory before allocating
            for oid in order_ids:
                o = STORE.orders.get(oid)
                if not o:
                    results.append({"order_id": oid, "status": "NOT_FOUND"})
                    continue
                if o["status"] != "CONFIRMED":
                    results.append({"order_id": oid, "status": "INVALID_STATE", "current": o["status"]})
                    continue
                o["status"] = "ALLOCATED"
                o["updated_at"] = STORE._now()
                results.append({"order_id": oid, "status": "ALLOCATED"})
            self._json({"results": results, "total": len(results)})

    # ============================================================
    # Pick List Handlers
    # ============================================================

    def _list_pick_lists(self, user):
        with STORE.lock:
            picks = list(STORE.pick_lists.values())
            self._json({"pick_lists": picks, "total": len(picks)})

    def _get_pick_list(self, user, pid):
        with STORE.lock:
            p = STORE.pick_lists.get(pid)
            if not p:
                return self._json({"error": "Pick list not found"}, 404)
            self._json(p)

    def _create_pick_list(self, user, body):
        with STORE.lock:
            if not check_role(user, ["OPERATOR", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            order = STORE.orders.get(body.get("order_id", ""))
            if not order:
                return self._json({"error": "Order not found"}, 404)
            # BUG-WMS-013: TENANT_OR_SCOPE_ISOLATION - No warehouse scope check
            # Should check: if user["warehouse"] != order["warehouse_id"]: 403
            pid = gen_id("pick")
            items = body.get("items", [])
            pick = {"id": pid, "order_id": order["id"], "warehouse_id": order["warehouse_id"],
                    "org": user["org"], "status": "CREATED", "items": items,
                    "created_by": user["id"], "created_at": STORE._now()}
            STORE.pick_lists[pid] = pick
            self._json(pick, 201)

    def _start_pick_list(self, user, pid):
        with STORE.lock:
            p = STORE.pick_lists.get(pid)
            if not p:
                return self._json({"error": "Pick list not found"}, 404)
            if p["status"] != "CREATED":
                return self._json({"error": "Pick list not in CREATED status"}, 409)
            # BUG-WMS-003: STATE_TRANSITION - Can start picking for non-ALLOCATED order
            # Should check: order = STORE.orders.get(p["order_id"]); if order["status"] != "ALLOCATED": 409
            # BUG-WMS-020: CROSS_ENTITY_CONSISTENCY - Pick list items not validated against order lines
            # Should verify each item.product_id exists in order_lines for this order
            p["status"] = "IN_PROGRESS"
            self._json(p)

    def _complete_pick_list(self, user, pid):
        with STORE.lock:
            p = STORE.pick_lists.get(pid)
            if not p:
                return self._json({"error": "Pick list not found"}, 404)
            if p["status"] != "IN_PROGRESS":
                return self._json({"error": "Pick list not in IN_PROGRESS status"}, 409)
            p["status"] = "COMPLETED"
            # Update batch status
            for item in p.get("items", []):
                batch = STORE.batches.get(item.get("batch_id", ""))
                if batch:
                    batch["status"] = "PICKED"
            self._json(p)

    def _cancel_pick_list(self, user, pid):
        with STORE.lock:
            p = STORE.pick_lists.get(pid)
            if not p:
                return self._json({"error": "Pick list not found"}, 404)
            if p["status"] in ("COMPLETED",):
                return self._json({"error": "Cannot cancel completed pick list"}, 409)
            # BUG-WMS-033: COMPENSATION - Cancelled pick list doesn't release reserved items
            # Should: for each item, restore batch.reserved_quantity and batch.status
            p["status"] = "CANCELLED"
            self._json(p)

    # ============================================================
    # Shipment Handlers
    # ============================================================

    def _list_shipments(self, user):
        with STORE.lock:
            shipments = list(STORE.shipments.values())
            # BUG-WMS-012: TENANT_OR_SCOPE_ISOLATION - Can view other org's shipments
            # Should filter: shipments = [s for s in shipments if s["org"] == user["org"]]
            self._json({"shipments": shipments, "total": len(shipments)})

    def _get_shipment(self, user, sid):
        with STORE.lock:
            s = STORE.shipments.get(sid)
            if not s:
                return self._json({"error": "Shipment not found"}, 404)
            self._json(s)

    def _create_shipment(self, user, body):
        with STORE.lock:
            if not check_role(user, ["OPERATOR", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            pick = STORE.pick_lists.get(body.get("pick_list_id", ""))
            if not pick:
                return self._json({"error": "Pick list not found"}, 404)
            # BUG-WMS-021: CROSS_ENTITY_PRECONDITION - Can create shipment without COMPLETED pick
            # Should check: if pick["status"] != "COMPLETED": 409
            # BUG-WMS-018: CROSS_ENTITY_CONSISTENCY - Shipment weight not validated against order
            # Should calculate expected weight from order lines and reject if mismatch
            order = STORE.orders.get(pick["order_id"])
            sid = gen_id("ship")
            shipment = {"id": sid, "order_id": pick["order_id"], "pick_list_id": pick["id"],
                        "warehouse_id": pick["warehouse_id"], "org": user["org"],
                        "status": "PENDING", "weight_kg": body.get("weight_kg", 0),
                        "carrier": body.get("carrier", "DEFAULT"),
                        "created_by": user["id"], "created_at": STORE._now()}
            STORE.shipments[sid] = shipment
            if order:
                order["status"] = "SHIPPED"
                order["updated_at"] = STORE._now()
            self._json(shipment, 201)

    def _confirm_shipment(self, user, sid):
        with STORE.lock:
            s = STORE.shipments.get(sid)
            if not s:
                return self._json({"error": "Shipment not found"}, 404)
            # BUG-WMS-016: RESOURCE_OWNERSHIP - Any operator can confirm any shipment
            # Should check: if s["created_by"] != user["id"] and user["role"] not in ("MANAGER","ADMIN"): 403
            # BUG-WMS-026: IDEMPOTENCY - Can confirm same shipment multiple times
            # Should check: if s["status"] == "CONFIRMED": 409
            # BUG-WMS-029: CONSERVATION - Inventory not decremented on shipment confirm
            # Should: for each item, batch.quantity -= item.quantity
            # BUG-WMS-032: COMPENSATION - If shipment fails after pick, picked inventory not restored
            # Should restore batch status and quantity on failure path
            s["status"] = "CONFIRMED"
            s["confirmed_at"] = STORE._now()
            self._json(s)

    # ============================================================
    # Return Handlers
    # ============================================================

    def _list_returns(self, user):
        with STORE.lock:
            returns = list(STORE.returns.values())
            if user["role"] == "CUSTOMER":
                returns = [r for r in returns if r["customer_id"] == user["id"]]
            self._json({"returns": returns, "total": len(returns)})

    def _get_return(self, user, rid):
        with STORE.lock:
            r = STORE.returns.get(rid)
            if not r:
                return self._json({"error": "Return not found"}, 404)
            # BUG-WMS-015: RESOURCE_OWNERSHIP - Customer can view/modify other customer's return
            # Should check: if user["role"]=="CUSTOMER" and r["customer_id"] != user["id"]: 403
            self._json(r)

    def _create_return(self, user, body):
        with STORE.lock:
            if not check_role(user, ["CUSTOMER", "ORDER_MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            order = STORE.orders.get(body.get("order_id", ""))
            if not order:
                return self._json({"error": "Order not found"}, 404)
            # BUG-WMS-035: TEMPORAL - No return deadline check
            # Should check: if current_date > order["return_deadline"]: 409 "Return window expired"
            # BUG-WMS-019: CROSS_ENTITY_CONSISTENCY - Return quantity not validated against order
            # Should check: return quantity <= original order line quantity
            rid = gen_id("ret")
            ret = {"id": rid, "order_id": order["id"], "customer_id": order["customer_id"],
                   "org": user["org"], "reason": body.get("reason", ""),
                   "quantity": body.get("quantity", 1), "product_id": body.get("product_id", ""),
                   "status": "REQUESTED", "created_at": STORE._now()}
            STORE.returns[rid] = ret
            self._json(ret, 201)

    def _approve_return(self, user, rid):
        with STORE.lock:
            r = STORE.returns.get(rid)
            if not r:
                return self._json({"error": "Return not found"}, 404)
            # BUG-WMS-006: ACTOR_AUTHORIZATION - Customer can approve returns
            # Should require: MANAGER or ADMIN only
            if not check_role(user, ["MANAGER", "ADMIN", "ORDER_MANAGER", "CUSTOMER"]):
                return self._json({"error": "Forbidden"}, 403)
            # BUG-WMS-004: STATE_TRANSITION - Can approve REJECTED returns
            # Should check: if r["status"] != "REQUESTED": 409
            r["status"] = "APPROVED"
            r["approved_at"] = STORE._now()
            self._json(r)

    def _reject_return(self, user, rid):
        with STORE.lock:
            r = STORE.returns.get(rid)
            if not r:
                return self._json({"error": "Return not found"}, 404)
            if not check_role(user, ["MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            if r["status"] != "REQUESTED":
                return self._json({"error": "Can only reject REQUESTED returns"}, 409)
            r["status"] = "REJECTED"
            # BUG-WMS-034: COMPENSATION - Rejected return doesn't restore order status
            # If order was in RETURN_PENDING, should restore to previous status
            self._json(r)

    def _receive_return(self, user, rid):
        with STORE.lock:
            r = STORE.returns.get(rid)
            if not r:
                return self._json({"error": "Return not found"}, 404)
            if r["status"] != "APPROVED":
                return self._json({"error": "Return not approved"}, 409)
            r["status"] = "RECEIVED"
            r["received_at"] = STORE._now()
            self._json(r)

    def _refund_return(self, user, rid):
        with STORE.lock:
            r = STORE.returns.get(rid)
            if not r:
                return self._json({"error": "Return not found"}, 404)
            if r["status"] != "RECEIVED":
                return self._json({"error": "Return not received"}, 409)
            # BUG-WMS-031: CONSERVATION - Return doesn't restore inventory
            # Should: batch.quantity += return.quantity for the product
            r["status"] = "REFUNDED"
            r["refunded_at"] = STORE._now()
            self._json(r)

    # ============================================================
    # Restock Order Handlers
    # ============================================================

    def _list_restock_orders(self, user):
        with STORE.lock:
            restocks = list(STORE.restock_orders.values())
            self._json({"restock_orders": restocks, "total": len(restocks)})

    def _create_restock_order(self, user, body):
        with STORE.lock:
            if not check_role(user, ["MANAGER", "ADMIN", "ORDER_MANAGER"]):
                return self._json({"error": "Forbidden"}, 403)
            supplier = STORE.suppliers.get(body.get("supplier_id", ""))
            # BUG-WMS-022: CROSS_ENTITY_PRECONDITION - No supplier validation
            # Should check: if not supplier: 404; if supplier["status"] != "ACTIVE": 409
            # BUG-WMS-036: TEMPORAL - delivery_date before order date accepted
            # Should check: if delivery_date < today: 400
            rid = gen_id("restock")
            restock = {"id": rid, "supplier_id": body.get("supplier_id", ""),
                       "product_id": body.get("product_id", ""),
                       "quantity": body.get("quantity", 0),
                       "warehouse_id": body.get("warehouse_id", ""),
                       "org": user["org"], "status": "PENDING",
                       "expected_delivery": body.get("expected_delivery", ""),
                       "created_at": STORE._now()}
            STORE.restock_orders[rid] = restock
            self._json(restock, 201)

    # ============================================================
    # Reservation Handlers
    # ============================================================

    def _list_reservations(self, user):
        with STORE.lock:
            reservations = list(STORE.reservations.values())
            self._json({"reservations": reservations, "total": len(reservations)})

    def _create_reservation(self, user, body):
        with STORE.lock:
            if not check_role(user, ["ORDER_MANAGER", "MANAGER", "ADMIN"]):
                return self._json({"error": "Forbidden"}, 403)
            batch = STORE.batches.get(body.get("batch_id", ""))
            if not batch:
                return self._json({"error": "Batch not found"}, 404)
            # BUG-WMS-025: IDEMPOTENCY - Duplicate reservation for same order+batch
            # Should check: if reservation exists for same order_id+batch_id -> 409
            # BUG-WMS-028: CONCURRENCY - No atomic check-and-decrement
            # Should atomically verify available = quantity - reserved >= requested
            rid = gen_id("res")
            qty = body.get("quantity", 0)
            res = {"id": rid, "order_id": body.get("order_id", ""),
                   "batch_id": batch["id"], "product_id": batch["product_id"],
                   "quantity": qty, "org": user["org"], "status": "ACTIVE",
                   "created_at": STORE._now()}
            STORE.reservations[rid] = res
            batch["reserved_quantity"] += qty
            self._json(res, 201)

    # ============================================================
    # Supplier Handlers
    # ============================================================

    def _list_suppliers(self, user):
        with STORE.lock:
            suppliers = [s for s in STORE.suppliers.values() if s["org"] == user["org"]]
            self._json({"suppliers": suppliers, "total": len(suppliers)})


# ============================================================
# Server Entry
# ============================================================

def run(port=8003):
    server = HTTPServer(("0.0.0.0", port), WMSHandler)
    print(f"WMS Mock Server running on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8003
    run(port)
