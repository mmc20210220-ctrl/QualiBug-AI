#!/usr/bin/env python3
"""QualiBug Live Test Target — a minimal standalone HTTP server with real, known bugs.

This server is NOT the QualiBug product; it is the system-under-test that QualiBug
should discover defects from. It implements a simplified e-commerce API that matches
the benchmark mall's structure (auth, users, products, orders, payments).

Known bugs (intentionally introduced):
1. AUTH-001: Disabled user can still login
2. AUTH-003: Any logged-in user can modify another user's status
3. AUTH-004: Weak password accepted during registration
4. USER-001: Buyer can query other users' addresses via userId parameter
5. ORDER-001: Cancelled order can still be paid
6. PAY-001: Payment amount doesn't match order total
7. PARAM-001: Negative quantity accepted when placing order

Usage:
    python test_target_server.py --port 8888 --db :memory:
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse


# ══════════════════════════════════════════════════════════════
# Database setup
# ══════════════════════════════════════════════════════════════

def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'buyer',
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            balance REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS addresses (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            street TEXT NOT NULL,
            city TEXT NOT NULL,
            phone TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products (
            sku TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            category TEXT NOT NULL DEFAULT 'general'
        );
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING_PAYMENT',
            total_amount REAL NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS order_items (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            sku TEXT NOT NULL,
            qty INTEGER NOT NULL,
            price REAL NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders(id)
        );
        CREATE TABLE IF NOT EXISTS payments (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            amount REAL NOT NULL,
            idempotency_key TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders(id)
        );
    """)
    return conn


def seed_db(conn: sqlite3.Connection):
    """Seed with test data matching benchmark mall test accounts."""
    cur = conn.cursor()
    # Check if already seeded
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] > 0:
        return

    pwd_hash = hashlib.sha256("Test@123456".encode()).hexdigest()
    admin_pwd = hashlib.sha256("Admin@123456".encode()).hexdigest()

    users = [
        ("u-buyer01", "buyer01@example.com", pwd_hash, "Buyer One", "buyer", "ACTIVE", 10000),
        ("u-buyer02", "buyer02@example.com", pwd_hash, "Buyer Two", "buyer", "ACTIVE", 500),
        ("u-disabled", "disabled_buyer@example.com", pwd_hash, "Disabled Buyer", "buyer", "DISABLED", 0),
        ("u-seller01", "seller01@example.com", pwd_hash, "Seller One", "seller", "ACTIVE", 0),
        ("u-warehouse", "warehouse01@example.com", pwd_hash, "Warehouse One", "warehouse", "ACTIVE", 0),
        ("u-finance", "finance01@example.com", pwd_hash, "Finance One", "finance", "ACTIVE", 0),
        ("u-auditor", "auditor01@example.com", pwd_hash, "Auditor One", "auditor", "ACTIVE", 0),
        ("u-admin", "admin@example.com", admin_pwd, "Admin", "admin", "ACTIVE", 0),
    ]
    cur.executemany("INSERT INTO users VALUES (?,?,?,?,?,?,?)", users)

    cur.executemany("INSERT INTO addresses VALUES (?,?,?,?,?)", [
        ("addr-01", "u-buyer01", "123 Main St", "Beijing", "13900000001"),
        ("addr-02", "u-buyer02", "456 Oak Ave", "Shanghai", "13900000002"),
    ])

    cur.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)", [
        ("SKU-PHONE-001", "Smartphone X", 6999, 100, "ACTIVE", "electronics"),
        ("SKU-LAPTOP-001", "Laptop Pro", 12999, 50, "ACTIVE", "electronics"),
        ("SKU-BOOK-001", "Python Guide", 99, 500, "ACTIVE", "books"),
        ("SKU-DRAFT-001", "Draft Product", 100, 10, "DRAFT", "general"),
    ])
    conn.commit()


# ══════════════════════════════════════════════════════════════
# HTTP Server
# ══════════════════════════════════════════════════════════════

class TestTargetHandler(http.server.BaseHTTPRequestHandler):
    db_conn: sqlite3.Connection | None = None
    bug_stats: dict[str, int] = {"found": 0, "triggered": set()}  # type: ignore

    def log_message(self, format: str, *args: Any) -> None:
        if os.environ.get("VERBOSE"):
            super().log_message(format, *args)

    def _json(self, data: Any, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    def _get_token(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return None

    def _get_user(self) -> dict | None:
        token = self._get_token()
        if not token:
            return None
        conn = self.__class__.db_conn
        if not conn:
            return None
        cur = conn.cursor()
        cur.execute("""
            SELECT u.* FROM users u
            JOIN tokens t ON u.id = t.user_id
            WHERE t.token = ?
        """, (token,))
        row = cur.fetchone()
        return dict(row) if row else None

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization,Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        if path == "/health":
            self._json({"status": "ok", "service": "benchmark-mall-lite"})

        elif path == "/api/products":
            cur = self.db_conn.cursor()
            cur.execute("SELECT * FROM products")
            products = [dict(r) for r in cur.fetchall()]
            # BUG: Returns DRAFT products by default (should filter to ACTIVE only)
            self._json({"products": products})

        elif path.startswith("/api/products/"):
            sku = path.split("/")[-1]
            cur = self.db_conn.cursor()
            cur.execute("SELECT * FROM products WHERE sku=?", (sku,))
            row = cur.fetchone()
            if row:
                self._json({"product": dict(row)})
            else:
                self._json({"error": "not found"}, 404)

        elif path == "/api/orders":
            user = self._get_user()
            if not user:
                self._json({"error": "unauthorized"}, 401)
                return
            cur = self.db_conn.cursor()
            cur.execute("SELECT * FROM orders WHERE user_id=?", (user["id"],))
            orders = [dict(r) for r in cur.fetchall()]
            self._json({"orders": orders})

        elif path.startswith("/api/orders/"):
            order_id = path.split("/")[-1]
            user = self._get_user()
            if not user:
                self._json({"error": "unauthorized"}, 401)
                return
            cur = self.db_conn.cursor()
            cur.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, user["id"]))
            row = cur.fetchone()
            if row:
                self._json({"order": dict(row)})
            else:
                self._json({"error": "not found"}, 404)

        # BUG USER-001: Accepts userId param to query other users' addresses
        elif path == "/api/users/addresses":
            user = self._get_user()
            if not user:
                self._json({"error": "unauthorized"}, 401)
                return
            target_id = params.get("userId", [user["id"]])[0]
            # BUG: No permission check — any user can query any userId's addresses
            cur = self.db_conn.cursor()
            cur.execute("SELECT * FROM addresses WHERE user_id=?", (target_id,))
            addrs = [dict(r) for r in cur.fetchall()]
            self._json({"addresses": addrs})

        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_body()

        if path == "/api/auth/login":
            email = body.get("email", "")
            password = body.get("password", "")
            cur = self.db_conn.cursor()
            cur.execute("SELECT * FROM users WHERE email=?", (email,))
            user = cur.fetchone()
            if not user:
                self._json({"error": "invalid credentials"}, 401)
                return
            user_dict = dict(user)
            pwd_hash = hashlib.sha256(password.encode()).hexdigest()
            if user_dict["password_hash"] != pwd_hash:
                self._json({"error": "invalid credentials"}, 401)
                return
            # BUG AUTH-001: Doesn't check user status — disabled users can login
            token = str(uuid.uuid4())
            cur.execute("INSERT INTO tokens VALUES (?, ?, ?)", (token, user_dict["id"], time.time()))
            self.db_conn.commit()
            self._json({
                "token": token,
                "user": {"id": user_dict["id"], "email": user_dict["email"],
                         "role": user_dict["role"], "status": user_dict["status"]}
            })

        elif path == "/api/auth/register":
            email = body.get("email", "")
            password = body.get("password", "")
            name = body.get("name", "")
            # BUG AUTH-004: No weak password check — accepts "1"
            if not email or not password:
                self._json({"error": "email and password required"}, 400)
                return
            pwd_hash = hashlib.sha256(password.encode()).hexdigest()
            uid = f"u-{uuid.uuid4().hex[:8]}"
            cur = self.db_conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                    (uid, email, pwd_hash, name or email, "buyer", "ACTIVE", 0)
                )
                self.db_conn.commit()
                self._json({"user": {"id": uid, "email": email}})
            except sqlite3.IntegrityError:
                self._json({"error": "email already exists"}, 409)

        # BUG AUTH-003: Any logged-in user can modify another user's status
        elif path.startswith("/api/auth/admin/users/") and path.endswith("/status"):
            user = self._get_user()
            if not user:
                self._json({"error": "unauthorized"}, 401)
                return
            # BUG: Only checks login, not admin role
            target_id = path.split("/")[-2]
            new_status = body.get("status", "ACTIVE")
            cur = self.db_conn.cursor()
            cur.execute("UPDATE users SET status=? WHERE id=?", (new_status, target_id))
            self.db_conn.commit()
            self._json({"status": "ok", "user_id": target_id, "new_status": new_status})

        elif path == "/api/orders":
            user = self._get_user()
            if not user:
                self._json({"error": "unauthorized"}, 401)
                return
            items = body.get("items", [])
            if not items:
                self._json({"error": "items required"}, 400)
                return
            total = 0
            for item in items:
                sku = item.get("sku", "")
                qty = item.get("qty", 1)
                # BUG PARAM-001: Doesn't reject negative quantity
                cur = self.db_conn.cursor()
                cur.execute("SELECT * FROM products WHERE sku=?", (sku,))
                product = cur.fetchone()
                if not product:
                    self._json({"error": f"product {sku} not found"}, 400)
                    return
                total += dict(product)["price"] * qty
            oid = f"ord-{uuid.uuid4().hex[:8]}"
            cur.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?)",
                (oid, user["id"], "PENDING_PAYMENT", total, time.time())
            )
            for item in items:
                cur.execute(
                    "INSERT INTO order_items VALUES (?,?,?,?,?)",
                    (f"oi-{uuid.uuid4().hex[:8]}", oid, item["sku"], item["qty"], 0)
                )
            self.db_conn.commit()
            self._json({"order": {"id": oid, "status": "PENDING_PAYMENT", "total": total}})

        elif path.startswith("/api/orders/") and path.endswith("/cancel"):
            order_id = path.split("/")[-2]
            user = self._get_user()
            if not user:
                self._json({"error": "unauthorized"}, 401)
                return
            cur = self.db_conn.cursor()
            cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
            order = cur.fetchone()
            if not order:
                self._json({"error": "order not found"}, 404)
                return
            order_dict = dict(order)
            if order_dict["status"] != "PENDING_PAYMENT":
                self._json({"error": "order already processed"}, 400)
                return
            cur.execute("UPDATE orders SET status='CANCELLED' WHERE id=?", (order_id,))
            self.db_conn.commit()
            self._json({"order": {"id": order_id, "status": "CANCELLED"}})

        elif path == "/api/payments/pay":
            user = self._get_user()
            if not user:
                self._json({"error": "unauthorized"}, 401)
                return
            order_id = body.get("orderId", "")
            amount = body.get("amount", 0)
            cur = self.db_conn.cursor()
            cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
            order = cur.fetchone()
            if not order:
                self._json({"error": "order not found"}, 404)
                return
            order_dict = dict(order)
            # BUG ORDER-001: Cancelled order can still be paid
            # BUG PAY-001: Amount doesn't need to match order total
            pid = f"pay-{uuid.uuid4().hex[:8]}"
            cur.execute(
                "INSERT INTO payments VALUES (?,?,?,?,?)",
                (pid, order_id, amount, body.get("idempotencyKey", ""), time.time())
            )
            cur.execute("UPDATE orders SET status='PAID' WHERE id=?", (order_id,))
            self.db_conn.commit()
            self._json({"payment": {"id": pid, "status": "success"}})

        # BUG COUPON-001: Expired coupon still validates
        elif path == "/api/coupons/validate":
            code = body.get("code", "")
            # Simulate: always return valid for any code (bug: no expiry/status check)
            self._json({"valid": True, "code": code, "discount": 100})

        # BUG COUPON-006: Coupon doesn't check category_scope
        elif path == "/api/coupons/validate-category":
            # BUG: Returns valid without checking if items match category scope
            self._json({"valid": True, "message": "Category check skipped (bug)"})

        # BUG PAY-004: Same idempotencyKey can create duplicate payment records
        elif path == "/api/payments/pay-with-idem":
            idem_key = body.get("idempotencyKey", "")
            order_id = body.get("orderId", "")
            # BUG: Doesn't check if idempotencyKey already used
            # Always creates new payment regardless of existing idempotencyKey
            pid = f"pay-{uuid.uuid4().hex[:8]}"
            cur = self.db_conn.cursor()
            cur.execute(
                "INSERT INTO payments VALUES (?,?,?,?,?)",
                (pid, order_id, body.get("amount", 0), idem_key, time.time())
            )
            self.db_conn.commit()
            self._json({"payment": {"id": pid, "idempotency_key": idem_key}})

        else:
            self._json({"error": "not found"}, 404)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_body()

        # BUG AUTH-003: Any user can modify product as admin
        if path.startswith("/api/products/admin/") and "/status" in path:
            user = self._get_user()
            if not user:
                self._json({"error": "unauthorized"}, 401)
                return
            sku = path.replace("/api/products/admin/", "").replace("/status", "")
            cur = self.db_conn.cursor()
            cur.execute("UPDATE products SET status=? WHERE sku=?", (body.get("status", "ACTIVE"), sku))
            self.db_conn.commit()
            self._json({"product": {"sku": sku, "status": body.get("status")}})
        else:
            self._json({"error": "not found"}, 404)


def serve(port: int = 8888, db_path: str = ":memory:"):
    conn = init_db(db_path)
    seed_db(conn)
    TestTargetHandler.db_conn = conn
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), TestTargetHandler)
    print(f"[TestTarget] Listening on http://127.0.0.1:{port}")
    print(f"[TestTarget] DB: {db_path}")
    print(f"[TestTarget] Known bugs: AUTH-001, AUTH-003, AUTH-004, USER-001, ORDER-001, PAY-001, PARAM-001")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[TestTarget] Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8888)
    p.add_argument("--db", default=":memory:")
    args = p.parse_args()
    serve(args.port, args.db)
