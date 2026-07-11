"""P6 Benchmark: first metrics against a seeded-bug HTTP SUT.

Measures recall, precision, evidence completeness, and reproduction success
of the QualiBug scan pipeline against 5 known ground-truth defects injected
into a local HTTP system-under-test.

Ground truth: 5 seeded bugs
  1. POST /api/register role=admin → SUT returns 201 (should be 403) — privilege_escalation
  2. POST /api/orders quantity=-5 → SUT returns 201 (should be 400) — parameter_boundary
  3. GET /api/debug/config → SUT returns 200 (should be 403) — security_boundary
  4. POST /api/orders body={} → SUT returns 201 (should be 400) — error_handling
  5. POST /api/orders/{id}/pay duplicate → SUT returns 200 (should be 409) — money_conservation
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROJECT = "p6_benchmark"
SCOPE_ID = "orders-p6-scope"
ENVIRONMENT_REF = "customer-staging"

OPENAPI_TEXT = """
openapi: 3.0.0
info:
  title: P6 Benchmark Seeded Bug API
  version: 1.0.0
paths:
  /api/register:
    post:
      summary: Register a new user
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                username:
                  type: string
                password:
                  type: string
                role:
                  type: string
                  enum: [user, admin]
      responses:
        '201':
          description: User created successfully
        '400':
          description: Invalid input
        '403':
          description: Forbidden role assignment
  /api/users/{userId}:
    delete:
      summary: Delete a user
      responses:
        '204':
          description: User deleted
  /api/orders:
    post:
      summary: Create a new order
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                quantity:
                  type: integer
                  minimum: 1
                sku:
                  type: string
                amount_cents:
                  type: integer
                  minimum: 0
      responses:
        '201':
          description: Order created
        '400':
          description: Invalid input
  /api/orders/{orderId}:
    delete:
      summary: Delete an order
      responses:
        '204':
          description: Order deleted
  /api/orders/{orderId}/pay:
    post:
      summary: Pay for an order
      responses:
        '200':
          description: Payment successful
        '409':
          description: Duplicate payment conflict
  /api/debug/config:
    get:
      summary: Debug configuration (should NOT be exposed)
      responses:
        '403':
          description: Forbidden
""".strip()

PRD_TEXT = """
Customer order and user management system:

1. User Registration:
   - Users may self-register with role "user" only.
   - The "admin" role must NOT be assignable via self-registration.
   - Attempting to register with role="admin" must be rejected with 403 Forbidden.

2. Order Creation:
   - Orders must have a positive quantity (minimum 1).
   - Orders must include at least a valid sku or amount_cents.
   - Negative or zero quantity must be rejected with 400 Bad Request.
   - Completely empty request body must be rejected with 400 Bad Request.

3. Payment:
   - Each order can be paid exactly once.
   - Duplicate payment attempts must be rejected with 409 Conflict.
   - Payment must preserve money conservation — no double-charge.

4. Security:
   - Debug/configuration endpoints must not be exposed to unauthenticated users.
   - GET /api/debug/config must always return 403 Forbidden in production.

5. Data Management:
   - All created resources (users, orders) must be deletable for cleanup.
""".strip()

DB_SCHEMA = """
CREATE TABLE users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user'
);
CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  sku TEXT,
  quantity INTEGER NOT NULL DEFAULT 1,
  amount_cents INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'created'
);
CREATE TABLE payments (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL UNIQUE,
  paid_at TEXT NOT NULL
);
""".strip()


class _SeededBugHandler(BaseHTTPRequestHandler):
    """SUT with 5 deliberately seeded bugs. Tracks state via class-level dicts."""

    users: dict[str, dict[str, Any]] = {}
    orders: dict[str, dict[str, Any]] = {}
    payments: set[str] = set()
    _seq: int = 0

    def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover
        return

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            try:
                return json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, Exception):
                return {}
        return {}

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/register":
            body = self._read_body()
            role = str(body.get("role", "user"))
            # ── BUG 1: privilege_escalation ──
            # SUT allows admin role registration; correct behavior is 403.
            type(self)._seq += 1
            user_id = f"user_{type(self)._seq}"
            type(self).users[user_id] = {"id": user_id, "username": body.get("username", ""), "role": role}
            self._json(201, {"id": user_id, "role": role})
            return

        if self.path == "/api/orders":
            body = self._read_body()
            quantity = body.get("quantity")
            # ── BUG 2: parameter_boundary ──
            # SUT accepts negative quantity; correct behavior is 400.
            # ── BUG 4: error_handling ──
            # SUT accepts empty body {}; correct behavior is 400.
            type(self)._seq += 1
            order_id = f"ord_{type(self)._seq}"
            type(self).orders[order_id] = {
                "id": order_id,
                "quantity": quantity,
                "sku": body.get("sku", ""),
                "amount_cents": body.get("amount_cents", 0),
                "status": "created",
            }
            self._json(201, {"id": order_id, "status": "created"})
            return

        if self.path.startswith("/api/orders/") and self.path.endswith("/pay"):
            parts = self.path.strip("/").split("/")
            # path: /api/orders/{orderId}/pay
            order_id = parts[2] if len(parts) >= 4 else ""
            # ── BUG 5: money_conservation ──
            # SUT allows duplicate payment; correct behavior is 409.
            type(self).payments.add(order_id)
            self._json(200, {"order_id": order_id, "status": "paid"})
            return

        self._json(404, {"error": "not_found"})

    def do_GET(self) -> None:  # noqa: N802
        # ── BUG 3: security_boundary ──
        # SUT exposes debug config; correct behavior is 403.
        if self.path == "/api/debug/config":
            self._json(200, {"db_password": "super_secret_123", "api_key": "sk-live-abc", "admin_token": "tok_debug"})
            return

        self._json(404, {"error": "not_found"})

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path.startswith("/api/users/"):
            user_id = self.path.rsplit("/", 1)[-1]
            type(self).users.pop(user_id, None)
            self._json(204, {})
            return

        if self.path.startswith("/api/orders/"):
            order_id = self.path.rsplit("/", 1)[-1]
            type(self).orders.pop(order_id, None)
            type(self).payments.discard(order_id)
            self._json(204, {})
            return

        self._json(404, {"error": "not_found"})


class _LocalHttpServer:
    def __init__(self, handler: type[BaseHTTPRequestHandler]) -> None:
        self._handler = handler

    def __enter__(self) -> str:
        _SeededBugHandler.users.clear()
        _SeededBugHandler.orders.clear()
        _SeededBugHandler.payments.clear()
        _SeededBugHandler._seq = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def _prepare_project(tmp_path: Path) -> dict[str, Any]:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset

    input_dir = tmp_path / "platform_workspace" / PROJECT / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "schema.sql").write_text(DB_SCHEMA, encoding="utf-8")
    return register_source_asset(
        PROJECT,
        "orders-openapi",
        OPENAPI_TEXT,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "customer_qa_lead", "role": "qa_lead"},
    )


def _build_runtime_scenario_contract() -> dict[str, Any]:
    """Build a runtime_scenario_contract covering all 5 seeded bug endpoints.

    Every write scenario carries mandatory cleanup_steps so the runtime gate
    does NOT block with CLEANUP_CONTRACT_MISSING.
    """
    return {
        "execution_policy": "approved_sandbox_write",
        "actor": {"id": "customer_qa_lead"},
        "write_approved": True,
        "scenarios": [
            # ── Bug 1: privilege_escalation ──
            {
                "id": "SCN_P6_REGISTER_ADMIN",
                "entity": "users",
                "category": "runtime_contract",
                "steps": [
                    {
                        "method": "POST",
                        "path": "/api/register",
                        "expected_status": 403,
                        "body": {"username": "admin_test_user", "password": "test123", "role": "admin"},
                    }
                ],
                "expected_state": "admin_registration_rejected",
                "cleanup_steps": [
                    {"method": "DELETE", "path": "/api/users/{id}", "expected_status": 204}
                ],
            },
            # ── Bug 2: parameter_boundary ──
            {
                "id": "SCN_P6_NEGATIVE_QUANTITY",
                "entity": "orders",
                "category": "runtime_contract",
                "steps": [
                    {
                        "method": "POST",
                        "path": "/api/orders",
                        "expected_status": 400,
                        "body": {"sku": "test-sku", "quantity": -5, "amount_cents": 100},
                    }
                ],
                "expected_state": "negative_quantity_rejected",
                "cleanup_steps": [
                    {"method": "DELETE", "path": "/api/orders/{id}", "expected_status": 204}
                ],
            },
            # ── Bug 3: security_boundary ──
            {
                "id": "SCN_P6_DEBUG_CONFIG",
                "entity": "config",
                "category": "runtime_contract",
                "steps": [
                    {
                        "method": "GET",
                        "path": "/api/debug/config",
                        "expected_status": 403,
                    }
                ],
                "expected_state": "debug_config_forbidden",
            },
            # ── Bug 4: error_handling (empty body) ──
            {
                "id": "SCN_P6_EMPTY_BODY",
                "entity": "orders",
                "category": "runtime_contract",
                "steps": [
                    {
                        "method": "POST",
                        "path": "/api/orders",
                        "expected_status": 400,
                        "body": {},
                    }
                ],
                "expected_state": "empty_order_rejected",
                "cleanup_steps": [
                    {"method": "DELETE", "path": "/api/orders/{id}", "expected_status": 204}
                ],
            },
            # ── Bug 5: money_conservation ──
            {
                "id": "SCN_P6_DUPLICATE_PAYMENT",
                "entity": "payments",
                "category": "runtime_contract",
                "steps": [
                    {
                        "method": "POST",
                        "path": "/api/orders",
                        "expected_status": 201,
                        "body": {"sku": "pay-sku", "quantity": 1, "amount_cents": 500},
                    },
                    {
                        "method": "POST",
                        "path": "/api/orders/{id}/pay",
                        "expected_status": 200,
                        "body": {},
                    },
                    {
                        "method": "POST",
                        "path": "/api/orders/{id}/pay",
                        "expected_status": 409,
                        "body": {},
                    },
                ],
                "expected_state": "duplicate_payment_rejected",
                "cleanup_steps": [
                    {"method": "DELETE", "path": "/api/orders/{id}", "expected_status": 204}
                ],
            },
        ],
    }


def _build_campaign_context(
    manifest: dict[str, Any],
    base_url: str,
    approval_id: str,
    contract: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source_manifest": manifest,
        "scope_id": SCOPE_ID,
        "environment_ref": ENVIRONMENT_REF,
        "environment_type": "test",
        "execution_mode": "approved_sandbox_write",
        "execution_approval_id": approval_id,
        "test_data_contract": {
            "strategy": "create_disposable",
            "write_approved": True,
        },
        "runtime_scenario_contract": contract,
    }


def _compute_p6_metrics(result: dict[str, Any]) -> dict[str, Any]:
    """Compute P6 benchmark metrics from a scan result."""
    findings = result.get("findings") or []
    candidates = result.get("candidate_findings") or []
    confirmed = len(findings)
    detected = confirmed + len(candidates)

    persisted = 0
    non_synthetic = 0
    for f in findings:
        if not isinstance(f, dict):
            continue
        # Persisted evidence: evidence_persistence_status is not "failed"
        evidence_status = str(f.get("evidence_persistence_status") or "")
        if evidence_status != "failed":
            persisted += 1
        # Non-synthetic reproduction: reproduction is not synthetic
        repro = f.get("reproduction") if isinstance(f.get("reproduction"), dict) else {}
        if not repro.get("is_synthetic") and not f.get("is_synthetic"):
            non_synthetic += 1

    recall = confirmed / 5 if confirmed else 0.0
    precision = confirmed / detected if detected else 0.0
    evidence_completeness = persisted / confirmed if confirmed else 0.0
    repro_success = non_synthetic / confirmed if confirmed else 0.0

    return {
        "ground_truth_bugs": 5,
        "confirmed_findings": confirmed,
        "detected_total": detected,
        "candidate_count": len(candidates),
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "evidence_completeness": round(evidence_completeness, 4),
        "repro_success": round(repro_success, 4),
        "persisted_evidence_bundles": persisted,
        "non_synthetic_reproductions": non_synthetic,
    }


def test_p6_benchmark_seeded_bugs(tmp_path: Path) -> None:
    """Run scan against SUT with 5 seeded bugs and compute P6 metrics."""
    from ai_test_asset_center.__main__ import scan
    from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign, source_snapshot_hash
    from ai_test_asset_center.execution_approvals import issue_execution_approval

    # 1. Register source asset and build campaign identity
    manifest = _prepare_project(tmp_path)
    snapshot = source_snapshot_hash(PRD_TEXT, OPENAPI_TEXT, DB_SCHEMA, SCOPE_ID, ENVIRONMENT_REF)

    with _LocalHttpServer(_SeededBugHandler) as base_url:
        # 2. Create campaign
        campaign = EnterpriseCampaign.create(
            PROJECT,
            SCOPE_ID,
            ENVIRONMENT_REF,
            snapshot,
            source_id=manifest["source_id"],
            source_hash=manifest["source_hash"],
            policy_version="",
        )

        # 3. Issue execution approval for write mode
        approval = issue_execution_approval(
            PROJECT,
            root=tmp_path,
            campaign_id=campaign.campaign_id,
            scope_id=SCOPE_ID,
            environment_ref=ENVIRONMENT_REF,
            source_hash=manifest["source_hash"],
            target_base_url=base_url,
            execution_mode="approved_sandbox_write",
            expires_at_utc=(datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            actor={"name": "customer_qa_lead", "role": "qa_lead"},
        )

        # 4. Build runtime scenario contract
        contract = _build_runtime_scenario_contract()

        # 5. Build campaign context
        ctx = _build_campaign_context(manifest, base_url, approval["approval_id"], contract)

        # 6. Execute scan
        result = scan(
            PROJECT,
            root=tmp_path,
            prd_text=PRD_TEXT,
            api_doc_text=OPENAPI_TEXT,
            base_url=base_url,
            campaign_context=ctx,
        )

    # 7. Verify scan executed (not fully blocked)
    assert result.get("success") is True, f"scan failed: {result.get('error', 'unknown')}"

    # 8. Compute P6 metrics
    metrics = _compute_p6_metrics(result)
    print("\n=== P6 Benchmark Metrics ===")
    for key, value in metrics.items():
        print(f"  {key}: {value}")

    # 9. Assert minimum thresholds (first-run metrics — loose bounds)
    assert metrics["recall"] > 0, f"recall must be > 0, got {metrics['recall']}"
    assert metrics["precision"] > 0, f"precision must be > 0, got {metrics['precision']}"
    assert metrics["evidence_completeness"] >= 0.5, f"evidence_completeness must be >= 0.5, got {metrics['evidence_completeness']}"

    # 10. Verify evidence chain is present
    evidence_bundle = result.get("evidence_bundle") or {}
    assert evidence_bundle.get("status") in ("persisted", "captured"), f"evidence bundle not persisted: {evidence_bundle}"
