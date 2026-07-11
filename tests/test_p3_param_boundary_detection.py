"""P3 integration test: parameter boundary error detection.

The buggy SUT accepts quantity=-5 on POST /api/orders (should reject with 400).
HttpStatusOracle detects the expected_status mismatch (expected 400, actual 201)
and produces a confirmed defect.
"""

from __future__ import annotations

import json, threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT = "p3_param_boundary_integration"
SCOPE = "orders-scope"; ENV = "staging"

OPENAPI = """openapi: 3.0.0
info:
  title: Buggy Shop
  version: 1.0.0
paths:
  /api/orders:
    post:
      summary: Create order (quantity must be > 0)
      responses:
        '201': {description: created}
        '400': {description: invalid_quantity}
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
            # BUG: no validation that quantity > 0
            with _LOCK:
                _SEQ["n"] += 1; oid = str(_SEQ["n"])
                _ORDERS[oid] = {"id": oid, "product_id": body.get("product_id"), "quantity": qty, "status": "created"}
            return self._json(201, _ORDERS[oid])
        return self._json(404, {"error": "nf"})

    def do_DELETE(self):
        oid = self.path.rsplit("/", 1)[-1]
        _ORDERS.pop(oid, None)
        return self._json(204, {})


@pytest.fixture(scope="module")
def _result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    _ORDERS.clear(); _SEQ["n"] = 0
    root = tmp_path_factory.mktemp("param")

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
                "id": "SCN_PARAM",
                "entity": "orders",
                "category": "parameter_boundary",
                "severity": "P1",
                "steps": [
                    {"method": "POST", "path": "/api/orders", "expected_status": 400,
                     "body": {"product_id": "p1", "quantity": -5}},
                ],
                "cleanup_steps": [
                    {"method": "DELETE", "path": "/api/orders/{id}", "expected_status": 204},
                ],
                "expected_state": "negative_quantity_rejected",
            }],
        }
        ctx = {
            "source_manifest": manifest, "scope_id": SCOPE, "environment_ref": ENV,
            "environment_type": "test",
            "execution_mode": "approved_sandbox_write", "execution_approval_id": approval["approval_id"],
            "test_data_contract": {"strategy": "create_disposable", "write_approved": True, "disposable_scope_ref": SCOPE},
            "runtime_scenario_contract": contract,
        }
        result = scan(PROJECT, root=root, prd_text="", api_doc_text=OPENAPI, base_url=base, campaign_context=ctx)
        return result
    finally:
        srv.shutdown(); srv.server_close(); thread.join(timeout=3)


def test_scan_produces_param_boundary_defect(_result: dict) -> None:
    assert _result.get("execution_status") == "completed"
    assert _result.get("grade") == "partial_coverage"
    assert _result.get("coverage_honesty", {}).get("downgraded") is True
    findings = _result.get("findings") or []
    assert len(findings) >= 1, f"expected >= 1 finding, got {len(findings)}"


def test_param_boundary_finding_is_confirmed(_result: dict) -> None:
    findings = _result.get("findings") or []
    param = [f for f in findings if any(
        t in str(f).lower() for t in ("boundary", "parameter", "quantity", "400", "negative"))]
    assert len(param) >= 1, f"no parameter boundary finding among {len(findings)}"
    for f in param:
        assert f.get("gate_passed") is True, f
        assert f.get("bug_status") == "reproduced", f
        assert f.get("customer_delivery_status") == "defect", f
        assert bool(f.get("raw_evidence")), f
