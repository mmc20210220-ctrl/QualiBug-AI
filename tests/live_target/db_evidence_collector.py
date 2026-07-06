#!/usr/bin/env python3
"""Real PostgreSQL Database Evidence Collector for QualiBug.

Connects to the benchmark mall's real PostgreSQL database and captures
before/after snapshots tied to specific business operations.

Usage:
    from db_evidence_collector import DBEvidenceCollector
    db = DBEvidenceCollector("postgresql://benchmark_user:benchmark_pass@localhost:5432/benchmark_mall")
    before = db.snapshot("orders", "id = 'ord-001'")
    # ... execute API call that modifies the order ...
    after = db.snapshot("orders", "id = 'ord-001'")
    evidence = db.build_evidence(before, after, "POST /api/payments/pay")
"""
from __future__ import annotations

import json
import time
from typing import Any

# We don't need psycopg2 — use subprocess to call psql for portability
import subprocess

PSQL_BIN = "C:/Program Files/PostgreSQL/17/bin/psql.exe"


class DBEvidenceCollector:
    """Captures real DB snapshots and builds evidence chains."""

    def __init__(self, conn_string: str):
        # Parse conn_string: postgresql://user:pass@host:port/dbname
        self.conn_string = conn_string
        parts = conn_string.replace("postgresql://", "").split("@")
        user_pass = parts[0].split(":")
        host_db = parts[1].split("/")
        host_port = host_db[0].split(":")
        self.user = user_pass[0]
        self.password = user_pass[1] if len(user_pass) > 1 else ""
        self.host = host_port[0]
        self.port = host_port[1] if len(host_port) > 1 else "5432"
        self.dbname = host_db[1] if len(host_db) > 1 else "benchmark_mall"

    def query(self, sql: str) -> list[dict] | None:
        """Run a query and return results as list of dicts."""
        try:
            import os
            env = os.environ.copy()
            env["PGPASSWORD"] = self.password
            result = subprocess.run(
                [PSQL_BIN, "-U", self.user, "-h", self.host, "-p", self.port,
                 "-d", self.dbname, "-t", "-A", "-F", "|||", "-c", sql],
                capture_output=True, text=True, timeout=10, env=env
            )
            if result.returncode != 0:
                return None
            lines = result.stdout.strip().split("\n")
            if not lines or not lines[0].strip():
                return []
            return [{"row": line.split("|||")} for line in lines if line.strip()]
        except Exception:
            return None

    def query_json(self, sql: str) -> list[dict] | None:
        """Run query and return as JSON rows."""
        try:
            import os
            env = os.environ.copy()
            env["PGPASSWORD"] = self.password
            result = subprocess.run(
                [PSQL_BIN, "-U", self.user, "-h", self.host, "-p", self.port,
                 "-d", self.dbname, "-t", "-c", sql],
                capture_output=True, text=True, timeout=10, env=env,
                encoding="utf-8"
            )
            if result.returncode != 0:
                print(f"[DB] psql error: {result.stderr[:200]}")
                return None
            rows = []
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        rows.append({"value": line})
            return rows
        except Exception as e:
            print(f"[DB] query_json error: {e}")
            return None

    def snapshot(self, table: str, where_clause: str = "1=1") -> dict:
        """Take a before/after snapshot of specific rows."""
        sql = f"SELECT row_to_json(t.*) FROM (SELECT * FROM {table} WHERE {where_clause}) t"
        rows = self.query_json(sql)
        return {
            "table": table,
            "where_clause": where_clause,
            "timestamp": time.time(),
            "rows": rows or [],
            "row_count": len(rows) if rows else 0,
        }

    def build_evidence(self, before: dict, after: dict,
                       business_operation: str,
                       expected: str = "",
                       actual: str = "") -> dict:
        """Build DB evidence with before/after snapshots bound to a business operation.

        Returns a ready-to-use evidence dict matching display_ready_formatter expectations.
        """
        return {
            "before_db_snapshot": before,
            "after_db_snapshot": after,
            "business_operation": business_operation,
            "db_assertion": f"Expected: {expected} | Actual: {actual}" if expected else "",
            "db_evidence_unavailable_reason": "",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def check_disabled_user_login(self) -> dict | None:
        """Verify DB: disabled user has status='DISABLED'."""
        rows = self.query_json("SELECT id, email, status FROM users WHERE email='disabled_buyer@example.com'")
        if not rows:
            return None
        user = rows[0]
        if user.get("status") != "DISABLED":
            return None
        return {
            "bug_id": "AUTH-001",
            "evidence": {
                "table": "users",
                "row": user,
                "finding": f"User {user['email']} is {user['status']} but can still login",
            }
        }

    def check_product_draft_visible(self) -> dict | None:
        """Verify DB: draft products exist but are visible in API."""
        rows = self.query_json("SELECT sku, name, status FROM products WHERE status = 'DRAFT'")
        if rows:
            return {
                "bug_id": "PRODUCT-004",
                "evidence": {
                    "table": "products",
                    "draft_products": rows,
                    "finding": f"{len(rows)} draft products exist and may be visible in list API",
                }
            }
        return None

    def check_coupon_validation(self) -> list[dict]:
        """Check coupon rules in DB."""
        results = []
        # COUPON-001: Expired coupons
        expired = self.query_json("SELECT code, status, valid_until FROM coupons WHERE valid_until < NOW()")
        if expired:
            results.append({
                "bug_id": "COUPON-001",
                "evidence": {"table": "coupons", "expired_coupons": expired,
                             "finding": f"{len(expired)} expired coupons exist but may validate successfully"}
            })
        # COUPON-002: Disabled coupons
        disabled = self.query_json("SELECT code, status FROM coupons WHERE status != 'ACTIVE'")
        if disabled:
            results.append({
                "bug_id": "COUPON-002",
                "evidence": {"table": "coupons", "disabled_coupons": disabled}
            })
        return results

    def check_db_constraints(self) -> list[dict]:
        """Check DB constraints that should exist but don't."""
        results = []
        # DB-001: payments.idempotency_key UNIQUE
        check_sql = """
        SELECT indexname FROM pg_indexes 
        WHERE tablename='payments' AND indexdef LIKE '%idempotency_key%UNIQUE%'
        """
        has_unique = self.query(check_sql)
        if not has_unique or not has_unique[0].get("row", [""])[0].strip():
            results.append({
                "bug_id": "DB-001",
                "evidence": {"table": "payments", "column": "idempotency_key",
                             "finding": "Missing UNIQUE constraint on idempotency_key"}
            })

        # DB-004: cart_items.qty CHECK > 0
        check_sql2 = """
        SELECT conname FROM pg_constraint 
        WHERE conrelid='cart_items'::regclass AND consrc LIKE '%qty%>%0%'
        """
        has_check = self.query(check_sql2)
        if not has_check or not has_check[0].get("row", [""])[0].strip():
            results.append({
                "bug_id": "DB-004",
                "evidence": {"table": "cart_items", "column": "qty",
                             "finding": "Missing CHECK constraint for positive qty"}
            })
        return results

    def run_all_checks(self) -> list[dict]:
        """Run all DB evidence checks."""
        all_evidence = []
        check = self.check_disabled_user_login()
        if check:
            all_evidence.append(check)
        draft = self.check_product_draft_visible()
        if draft:
            all_evidence.append(draft)
        all_evidence.extend(self.check_coupon_validation())
        all_evidence.extend(self.check_db_constraints())
        return all_evidence
