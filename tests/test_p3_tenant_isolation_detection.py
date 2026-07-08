"""P3 integration test: tenant isolation failure (P3-2).

Two tenants: tenant-A creates an order, tenant-B attempts GET /api/orders/{id}
with tenant-B's identity and should be denied (403 Forbidden). The buggy SUT
returns 200 instead — no cross-tenant access control. HttpStatusOracle detects
the expected_status mismatch.
"""

from __future__ import annotations

import json, os, threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT = "p3_tenant_isolation"
SCOPE = "orders"; ENV = "staging"

OPENAPI = """openapi: 3.0.0
info:
  title: Multi-tenant Shop
  version: 1.0.0
paths:
  /api/orders:
    post:
      summary: Create order (tenant-scoped)
      responses:
        '201': {description: created}
  /api/orders/{id}:
    get:
      summary: Get order (must be same tenant)
      parameters:
        - name: id
          in: path
          required: true
          schema: {type: string}
      responses:
        '200': {description: ok}
        '403': {description: cross_tenant_forbidden}
    delete:
      summary: Delete order
      responses:
        '204': {description: deleted}
"""

_orders: dict[str, dict] = {}
_lock = threading.Lock()
_seq = {"n": 0}


class _BuggyHandler(BaseHTTPRequestHandler):
    """Buggy: does not check that the requesting tenant owns the order."""

    def log_message(self, *_: object) -> None: return

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _read(self):
        cl = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(cl) or b"{}") if cl else {}

    def do_POST(self):
        if self.path == "/api/orders":
            body = self._read()
            qty = body.get("quantity", 1); tenant = body.get("tenant", "default")
            with _lock:
                _seq["n"] += 1; oid = str(_seq["n"])
                _orders[oid] = {"id": oid, "quantity": qty, "tenant": tenant, "status": "created"}
            return self._json(201, _orders[oid])
        return self._json(404, {"error": "nf"})

    def do_GET(self):
        oid = self.path.rsplit("/api/orders/", 1)[-1] if self.path.startswith("/api/orders/") else ""
        with _lock:
            o = _orders.get(oid)
        # BUG: tenant-B can read tenant-A's order — no access control check
        if o:
            return self._json(200, o)
        return self._json(404, {"error": "nf"})

    def do_DELETE(self):
        oid = self.path.rsplit("/api/orders/", 1)[-1]
        _orders.pop(oid, None)
        return self._json(204, {})


@pytest.fixture(scope="module")
def _result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    _orders.clear(); _seq["n"] = 0
    root = tmp_path_factory.mktemp("tenant")

    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign, source_snapshot_hash
    from ai_test_asset_center.execution_approvals import issue_execution_approval
    from ai_test_asset_center.__main__ import scan

    manifest = register_source_asset(
        PROJECT, "tenant-openapi", OPENAPI, source_type="openapi", root=root,
        actor={"name": "qa_lead", "role": "qa_lead"},
    )
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _BuggyHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    try:
        snap = source_snapshot_hash("", OPENAPI, "", SCOPE, ENV)
        campaign = EnterpriseCampaign.create(
            PROJECT, SCOPE, ENV, snap,
            source_id=manifest["source_id"], source_hash=manifest["source_hash"], policy_version="",
        )
        approval = issue_execution_approval(
            PROJECT, root=root, campaign_id=campaign.campaign_id,
            scope_id=SCOPE, environment_ref=ENV, source_hash=manifest["source_hash"],
            target_base_url=base, execution_mode="approved_sandbox_write",
            expires_at_utc=(datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            actor={"name": "qa_lead", "role": "qa_lead"},
        )
        contract = {
            "execution_policy": "approved_sandbox_write",
            "actor": {"id": "qa_lead"},
            "scenarios": [{
                "id": "SCN_TENANT",
                "entity": "orders",
                "category": "tenant_isolation",
                "severity": "P0",
                "steps": [
                    # Step 1: tenant-A creates order
                    {"method": "POST", "path": "/api/orders", "expected_status": 201,
                     "body": {"tenant": "A", "quantity": 1}},
                    # Step 2: tenant-B tries GET the order → should be 403;
                    # BUG returns 200 (no tenant check)
                    {"method": "GET", "path": "/api/orders/{id}", "expected_status": 403,
                     "body": {"tenant": "B"}},
                ],
                "cleanup_steps": [
                    {"method": "DELETE", "path": "/api/orders/{id}", "expected_status": 204},
                ],
                "expected_state": "cross_tenant_access_blocked",
            }],
        }
        ctx = {
            "source_manifest": manifest, "scope_id": SCOPE, "environment_ref": ENV,
            "execution_mode": "approved_sandbox_write", "execution_approval_id": approval["approval_id"],
            "test_data_contract": {"strategy": "create_disposable", "write_approved": True, "disposable_scope_ref": SCOPE},
            "runtime_scenario_contract": contract,
        }
        result = scan(PROJECT, root=root, prd_text="", api_doc_text=OPENAPI, base_url=base, campaign_context=ctx)
        return result
    finally:
        srv.shutdown(); srv.server_close(); thread.join(timeout=3)


def test_scan_runs(_result: dict) -> None:
    assert _result.get("execution_status") == "completed"
    assert len(_result.get("findings") or []) >= 1


def test_tenant_isolation_violation_detected(_result: dict) -> None:
    findings = _result.get("findings") or []
    tenant = [
        f for f in findings
        if any(t in str(f).lower() for t in ("tenant", "isolation", "cross", "403", "forbidden"))
    ]
    assert len(tenant) >= 1, f"no tenant isolation finding among {len(findings)}"


def test_confirmed_defect_with_evidence(_result: dict) -> None:
    for f in (_result.get("findings") or []):
        assert f.get("gate_passed") is True, f
        assert f.get("bug_status") == "reproduced", f
        assert f.get("customer_delivery_status") == "defect", f
        assert bool(f.get("raw_evidence")), f
