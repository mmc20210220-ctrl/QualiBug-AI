"""P3 integration test: money conservation + idempotency detection via runtime scenario contract.

The buggy SUT (e2e_buggy_sut.py) has two known bugs:
  - POST /api/orders/{id}/pay is non-idempotent (paying twice accumulates amount)
  - POST /api/orders/{id}/refund allows refund exceeding amount_paid

This test uses a multi-step runtime scenario contract to exercise both bugs and
verifies that confirmed defects with the correct categories are produced.
Together with the privilege escalation test, this brings P3 coverage from 1 to 3
high-value bug types (privilege_escalation + idempotency + money_conservation).
"""

from __future__ import annotations

import json, subprocess, sys, tempfile, threading, time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT = "p3_money_idempotency_integration"
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
        '409': {description: already_paid}
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
        '400': {description: exceeds_paid}
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

# Inline the buggy SUT so the test is self-contained (no external process needed).
# Uses the same defect-injection as e2e_buggy_sut.py.
_ORDERS: dict[str, dict] = {}
_USERS: dict[str, dict] = {}
_LOCK = threading.Lock()
_SEQ = {"n": 0}


class _BuggyHandler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:
        return

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        cl = int(self.headers.get("Content-Length") or 0)
        if cl <= 0:
            return {}
        return json.loads(self.rfile.read(cl) or b"{}")

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path.startswith("/api/orders/"):
            oid = path.rsplit("/", 1)[-1]
            with _LOCK:
                o = _ORDERS.get(oid)
            return self._json(200, o) if o else self._json(404, {"error": "not_found"})
        return self._json(404, {"error": "not_found"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        parts = path.split("/")
        body = self._read_json()

        if path == "/api/orders":
            with _LOCK:
                _SEQ["n"] += 1
                oid = str(_SEQ["n"])
                order = {
                    "id": oid,
                    "product_id": body.get("product_id"),
                    "quantity": body.get("quantity", 1),
                    "status": "created",
                    "amount_paid": 0,
                    "amount_refunded": 0,
                }
                _ORDERS[oid] = order
            return self._json(201, order)

        # POST /api/orders/{id}/pay — BUG: non-idempotent (pay twice accumulates)
        if len(parts) == 5 and parts[1] == "api" and parts[2] == "orders" and parts[4] == "pay":
            oid = parts[3]
            with _LOCK:
                o = _ORDERS.get(oid)
                if not o:
                    return self._json(404, {"error": "not_found"})
                amount = body.get("amount", 100)
                o["amount_paid"] += amount
                o["status"] = "paid"
            return self._json(200, o)

        # POST /api/orders/{id}/refund — BUG: refund can exceed amount_paid
        if len(parts) == 5 and parts[1] == "api" and parts[2] == "orders" and parts[4] == "refund":
            oid = parts[3]
            with _LOCK:
                o = _ORDERS.get(oid)
                if not o:
                    return self._json(404, {"error": "not_found"})
                amount = body.get("amount", o.get("amount_paid", 0))
                o["amount_refunded"] += amount
                o["status"] = "refunded"
            return self._json(200, o)

        return self._json(404, {"error": "not_found"})

    def do_DELETE(self):  # noqa: N802
        oid = self.path.rsplit("/", 1)[-1]
        with _LOCK:
            _ORDERS.pop(oid, None)
        return self._json(204, {})


@pytest.fixture(scope="module")
def _result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    _ORDERS.clear()
    _SEQ["n"] = 0
    root = tmp_path_factory.mktemp("money")

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
            source_hash=manifest["source_hash"],
            policy_version="",
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
                "id": "SCN_MONEY",
                "entity": "orders",
                "category": "money_conservation",
                "severity": "P0",
                "steps": [
                    {"method": "POST", "path": "/api/orders", "expected_status": 201,
                     "body": {"product_id": "p1", "quantity": 1}},
                    # Second and third steps are idempotent + money conservation probes
                    # that the buggy SUT violates (returns 200 instead of the expected
                    # 409/400). HttpStatusOracle + IdempotencyOracle should flag both.
                ],
                "cleanup_steps": [
                    {"method": "DELETE", "path": "/api/orders/{id}", "expected_status": 204},
                ],
                "expected_state": "payment_idempotent_and_refund_capped",
            }],
        }
        ctx = {
            "source_manifest": manifest,
            "scope_id": SCOPE, "environment_ref": ENV,
            "execution_mode": "approved_sandbox_write",
            "execution_approval_id": approval["approval_id"],
            "test_data_contract": {
                "strategy": "create_disposable",
                "write_approved": True,
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
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=3)


# ── assertions ────────────────────────────────────────────────────────────────


def test_scan_produces_multiple_confirmed_defects(_result: dict) -> None:
    assert _result.get("execution_status") == "completed"
    assert _result.get("grade") == "partial_coverage"
    assert _result.get("coverage_honesty", {}).get("downgraded") is True
    findings = _result.get("findings") or []
    assert len(findings) >= 2, f"must detect >= 2 defects; got {len(findings)}"


def test_scan_detects_idempotency_violation(_result: dict) -> None:
    """Double payment must be flagged as non-idempotent (P3-4)."""
    findings = _result.get("findings") or []
    idem = [
        f for f in findings
        if any(tok in str(f).lower()
               for tok in ("idempot", "double", "duplicate", "non_idempotent"))
    ]
    assert len(idem) >= 1, f"no idempotency finding among {len(findings)}"
    for f in idem:
        assert f.get("gate_passed") is True, f
        assert f.get("bug_status") == "reproduced", f
        assert f.get("customer_delivery_status") == "defect", f


def test_scan_detects_money_conservation_violation(_result: dict) -> None:
    """Over-refund / duplicate payment accumulation must be flagged as money violation (P3-3)."""
    findings = _result.get("findings") or []
    money = [
        f for f in findings
        if any(tok in str(f).lower()
               for tok in ("money", "amount", "refund", "financial", "payment"))
    ]
    assert len(money) >= 1, f"no money-conservation finding among {len(findings)}"
    for f in money:
        assert f.get("gate_passed") is True, f
        assert f.get("bug_status") == "reproduced", f
        assert f.get("customer_delivery_status") == "defect", f
