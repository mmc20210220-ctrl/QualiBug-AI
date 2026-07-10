"""P3 integration test: state machine violation detection.

The buggy SUT allows paying a refunded order (no state guard on POST /api/orders/{id}/pay),
violating the state machine invariant: refunded→paid is an illegal transition.

This test uses a multi-step runtime scenario contract (create→pay→refund→pay again
with expected_status 409 for the second pay) to verify the system detects the
state violation as a confirmed defect.
"""

from __future__ import annotations

import json, threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT = "p3_state_machine_integration"
SCOPE = "orders-scope"
ENV = "staging"

OPENAPI = """openapi: 3.0.0
info:
  title: Buggy Shop
  version: 1.0.0
paths:
  /api/orders:
    post:
      summary: Create order
      responses:
        '201': {description: created}
  /api/orders/{id}/pay:
    post:
      summary: Pay order
      parameters:
        - name: id
          in: path
          required: true
          schema: {type: string}
      responses:
        '200': {description: paid}
        '409': {description: state_conflict}
  /api/orders/{id}/refund:
    post:
      summary: Refund order
      parameters:
        - name: id
          in: path
          required: true
          schema: {type: string}
      responses:
        '200': {description: refunded}
  /api/orders/{id}:
    get:
      summary: Get order
      parameters:
        - name: id
          in: path
          required: true
          schema: {type: string}
      responses:
        '200': {description: ok}
    delete:
      summary: Delete order
      responses:
        '204': {description: deleted}
"""

_ORDERS: dict[str, dict] = {}
_LOCK = threading.Lock()
_SEQ = {"n": 0}


class _BuggyHandler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:
        return

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        cl = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(cl) or b"{}") if cl else {}

    def do_GET(self):
        oid = self.path.rsplit("/api/orders/", 1)[-1] if "/api/orders/" in self.path else ""
        with _LOCK:
            o = _ORDERS.get(oid)
        return self._json(200, o) if o else self._json(404, {"error": "nf"})

    def do_POST(self):
        path = self.path; parts = path.split("/"); body = self._read_json()
        if path == "/api/orders":
            with _LOCK:
                _SEQ["n"] += 1; oid = str(_SEQ["n"])
                o = {"id": oid, "product_id": body.get("product_id"),
                     "quantity": body.get("quantity", 1), "status": "created",
                     "amount_paid": 0, "amount_refunded": 0}
                _ORDERS[oid] = o
            return self._json(201, o)
        if len(parts) == 5 and parts[4] == "pay":
            oid = parts[3]
            with _LOCK:
                o = _ORDERS.get(oid)
                if not o: return self._json(404, {"error": "nf"})
                # BUG: no state check — refunded→paid is an illegal transition
                o["amount_paid"] += body.get("amount", 100)
                o["status"] = "paid"
            return self._json(200, o)
        if len(parts) == 5 and parts[4] == "refund":
            oid = parts[3]
            with _LOCK:
                o = _ORDERS.get(oid)
                if not o: return self._json(404, {"error": "nf"})
                o["amount_refunded"] += body.get("amount", o.get("amount_paid", 0))
                o["status"] = "refunded"
            return self._json(200, o)
        return self._json(404, {"error": "nf"})

    def do_DELETE(self):
        oid = self.path.rsplit("/api/orders/", 1)[-1]
        _ORDERS.pop(oid, None)
        return self._json(204, {})


@pytest.fixture(scope="module")
def _result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    _ORDERS.clear(); _SEQ["n"] = 0
    root = tmp_path_factory.mktemp("state")

    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign, source_snapshot_hash
    from ai_test_asset_center.execution_approvals import issue_execution_approval
    from ai_test_asset_center.__main__ import scan

    manifest = register_source_asset(
        PROJECT, "shop-openapi", OPENAPI,
        source_type="openapi", root=root,
        actor={"name": "qa_lead", "role": "qa_lead"},
    )

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _BuggyHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    try:
        snap = source_snapshot_hash("", OPENAPI, "", SCOPE, ENV)
        campaign = EnterpriseCampaign.create(
            PROJECT, SCOPE, ENV, snap,
            source_id=manifest["source_id"],
            source_hash=manifest["source_hash"], policy_version="",
        )
        approval = issue_execution_approval(
            PROJECT, root=root,
            campaign_id=campaign.campaign_id,
            scope_id=SCOPE, environment_ref=ENV,
            source_hash=manifest["source_hash"],
            target_base_url=base,
            execution_mode="approved_sandbox_write",
            expires_at_utc=(
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            actor={"name": "qa_lead", "role": "qa_lead"},
        )
        contract = {
            "execution_policy": "approved_sandbox_write",
            "actor": {"id": "qa_lead"},
            "scenarios": [{
                "id": "SCN_STATE",
                "entity": "orders",
                "category": "state_machine_violation",
                "severity": "P0",
                "steps": [
                    {"method": "POST", "path": "/api/orders", "expected_status": 201,
                     "body": {"product_id": "p1", "quantity": 1}},
                    {"method": "POST", "path": "/api/orders/{id}/pay", "expected_status": 200,
                     "body": {"amount": 100}},
                    {"method": "POST", "path": "/api/orders/{id}/refund", "expected_status": 200,
                     "body": {"amount": 100}},
                    # Second pay on refunded order: expected 409 state conflict.
                    # Buggy SUT returns 200 — state machine invariant violated.
                    {"method": "POST", "path": "/api/orders/{id}/pay", "expected_status": 409,
                     "body": {"amount": 50}},
                    {"method": "GET", "path": "/api/orders/{id}", "expected_status": 200},
                ],
                "cleanup_steps": [
                    {"method": "DELETE", "path": "/api/orders/{id}", "expected_status": 204},
                ],
                "expected_state": "state_machine_preserves_invariants",
            }],
        }
        ctx = {
            "source_manifest": manifest,
            "scope_id": SCOPE, "environment_ref": ENV,
            "execution_mode": "approved_sandbox_write",
            "execution_approval_id": approval["approval_id"],
            "test_data_contract": {
                "strategy": "create_disposable", "write_approved": True,
                "disposable_scope_ref": SCOPE,
            },
            "runtime_scenario_contract": contract,
        }
        result = scan(
            PROJECT, root=root, prd_text="", api_doc_text=OPENAPI,
            base_url=base, campaign_context=ctx,
        )
        return result
    finally:
        srv.shutdown(); srv.server_close(); thread.join(timeout=3)


def test_scan_completed_with_defects(_result: dict) -> None:
    assert _result.get("execution_status") == "completed"
    assert _result.get("grade") == "partial_coverage"
    assert _result.get("coverage_honesty", {}).get("downgraded") is True
    assert len(_result.get("findings") or []) >= 2


def test_state_machine_violation_flagged(_result: dict) -> None:
    """At least one finding must relate to state/status/machine/conflict violation."""
    findings = _result.get("findings") or []
    state = [
        f for f in findings
        if any(tok in str(f).lower()
               for tok in ("state", "machine", "conflict", "transition", "invariant"))
    ]
    assert len(state) >= 1, f"no state-machine finding among {len(findings)}"


def test_confirmed_defect_has_evidence(_result: dict) -> None:
    findings = _result.get("findings") or []
    for f in findings:
        assert f.get("gate_passed") is True, f
        assert f.get("bug_status") == "reproduced", f
        assert f.get("customer_delivery_status") == "defect", f
        assert bool(f.get("raw_evidence")), f
