"""P3 integration test: error code / exception handling detection.

The buggy SUT accepts POST /api/orders with an empty body {} and creates an order
with default values. Correct behavior would reject with HTTP 400 (bad request /
missing required fields). HttpStatusOracle detects the expected_status mismatch
(expected 400, actual 201) as a confirmed defect.
"""

from __future__ import annotations

import json, threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT = "p3_error_handling_integration"
SCOPE = "orders-scope"; ENV = "staging"

OPENAPI = """openapi: 3.0.0
info:
  title: Buggy Shop
  version: 1.0.0
paths:
  /api/orders:
    post:
      summary: Create order (product_id and quantity are required)
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [product_id, quantity]
              properties:
                product_id: {type: string}
                quantity: {type: integer, minimum: 1}
      responses:
        '201': {description: created}
        '400': {description: invalid_request}
  /api/orders/{id}:
    delete:
      summary: Delete order
      parameters:
        - name: id
          in: path
          required: true
          schema: {type: string}
      responses:
        '204': {description: deleted}
"""

_ORDERS: dict[str, dict] = {}
_LOCK = threading.Lock(); _SEQ = {"n": 0}


class _BuggyHandler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None: return

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _read_json(self):
        cl = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(cl) or b"{}") if cl else {}

    def do_POST(self):
        body = self._read_json()
        if self.path == "/api/orders":
            qty = body.get("quantity", 1)
            pid = body.get("product_id", "")
            # BUG: no validation — accepts empty body {}, missing required fields
            with _LOCK:
                _SEQ["n"] += 1; oid = str(_SEQ["n"])
                _ORDERS[oid] = {"id": oid, "product_id": pid, "quantity": qty, "status": "created"}
            return self._json(201, _ORDERS[oid])
        return self._json(404, {"error": "nf"})

    def do_DELETE(self):
        oid = self.path.rsplit("/", 1)[-1]
        _ORDERS.pop(oid, None)
        return self._json(204, {})


@pytest.fixture(scope="module")
def _result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    _ORDERS.clear(); _SEQ["n"] = 0
    root = tmp_path_factory.mktemp("errh")

    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign, source_snapshot_hash
    from ai_test_asset_center.execution_approvals import issue_execution_approval
    from ai_test_asset_center.__main__ import scan

    manifest = register_source_asset(
        PROJECT, "shop-openapi", OPENAPI, source_type="openapi", root=root,
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
                "id": "SCN_ERR",
                "entity": "orders",
                "category": "error_handling",
                "severity": "P1",
                "steps": [
                    # Empty body: missing required fields product_id + quantity
                    # should be HTTP 400. Buggy SUT returns 201 with defaults.
                    {"method": "POST", "path": "/api/orders", "expected_status": 400, "body": {}},
                ],
                "cleanup_steps": [
                    {"method": "DELETE", "path": "/api/orders/{id}", "expected_status": 204},
                ],
                "expected_state": "empty_body_rejected_with_400",
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


def test_scan_produces_error_handling_defect(_result: dict) -> None:
    assert _result.get("execution_status") == "completed"
    findings = _result.get("findings") or []
    assert len(findings) >= 1, f"expected >= 1 finding, got {len(findings)}"


def test_error_handling_finding_is_confirmed(_result: dict) -> None:
    findings = _result.get("findings") or []
    errh = [f for f in findings if any(
        t in str(f).lower() for t in ("400", "error", "missing", "required", "empty", "bad_request"))]
    assert len(errh) >= 1, f"no error handling finding among {len(findings)}"
    for f in errh:
        assert f.get("gate_passed") is True, f
        assert f.get("bug_status") == "reproduced", f
        assert f.get("customer_delivery_status") == "defect", f
        assert bool(f.get("raw_evidence")), f
