"""P3 integration test: security boundary configuration error (P3-16).

The SUT exposes a /api/debug/config endpoint that returns internal server state
without requiring authentication. Production systems must never expose such
endpoints publicly. The contract asserts expected_status=403 for an unauthenticated
caller; the buggy SUT returns 200, leaking internal configuration.
"""

from __future__ import annotations

import json, threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT = "p3_security_boundary"
SCOPE = "orders"; ENV = "staging"

OPENAPI = """openapi: 3.0.0
info:
  title: Leaky Service
  version: 1.0.0
paths:
  /api/orders:
    post:
      summary: Create order
      responses:
        '201': {description: created}
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
  /api/debug/config:
    get:
      summary: Internal debug config (MUST be admin-only)
      security:
        - bearerAuth: []
      responses:
        '200': {description: admin_ok}
        '403': {description: forbidden}
"""

_orders: dict[str, dict] = {}
_lock = threading.Lock()
_seq = {"n": 0}


class _LeakyHandler(BaseHTTPRequestHandler):
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
            body = self._read(); qty = body.get("quantity", 1)
            with _lock:
                _seq["n"] += 1; oid = str(_seq["n"])
                _orders[oid] = {"id": oid, "quantity": qty, "status": "created"}
            return self._json(201, _orders[oid])
        return self._json(404, {"error": "nf"})

    def do_GET(self):
        # BUG: /api/debug/config exposed without auth — returns internal state
        if self.path == "/api/debug/config":
            return self._json(200, {
                "db_host": "prod-db.internal:5432",
                "db_password": "super-secret-123",
                "admin_api_key": "sk_live_abc123",
                "debug_mode": True,
            })
        return self._json(404, {"error": "nf"})

    def do_DELETE(self):
        oid = self.path.rsplit("/api/orders/", 1)[-1]
        _orders.pop(oid, None)
        return self._json(204, {})


@pytest.fixture(scope="module")
def _result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    _orders.clear(); _seq["n"] = 0
    root = tmp_path_factory.mktemp("sec")

    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign, source_snapshot_hash
    from ai_test_asset_center.execution_approvals import issue_execution_approval
    from ai_test_asset_center.__main__ import scan

    manifest = register_source_asset(
        PROJECT, "leaky-openapi", OPENAPI, source_type="openapi", root=root,
        actor={"name": "qa_lead", "role": "qa_lead"},
    )
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _LeakyHandler)
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
            target_base_url=base, execution_mode="safe_read_only",
            expires_at_utc=(datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            actor={"name": "qa_lead", "role": "qa_lead"},
        )
        contract = {
            "execution_policy": "safe_read_only",
            "actor": {"id": "qa_lead"},
            "scenarios": [{
                "id": "SCN_SEC",
                "entity": "system",
                "category": "security_boundary",
                "severity": "P0",
                "steps": [
                    # Debug endpoint must be forbidden for unauthenticated users.
                    # Buggy SUT returns 200 with internal secrets.
                    {"method": "GET", "path": "/api/debug/config", "expected_status": 403},
                ],
                "expected_state": "debug_endpoint_blocked_for_public",
            }],
        }
        ctx = {
            "source_manifest": manifest, "scope_id": SCOPE, "environment_ref": ENV,
            "execution_mode": "safe_read_only", "execution_approval_id": approval["approval_id"],
            "test_data_contract": {"strategy": "safe_read_only"},
            "runtime_scenario_contract": contract,
        }
        result = scan(PROJECT, root=root, prd_text="", api_doc_text=OPENAPI, base_url=base, campaign_context=ctx)
        return result
    finally:
        srv.shutdown(); srv.server_close(); thread.join(timeout=3)


def test_scan_completed(_result: dict) -> None:
    assert _result.get("execution_status") == "completed"
    assert len(_result.get("findings") or []) >= 1


def test_security_boundary_violation_detected(_result: dict) -> None:
    findings = _result.get("findings") or []
    sec = [f for f in findings if any(
        t in str(f).lower() for t in ("debug", "config", "secret", "auth", "403", "forbidden", "security"))
    ]
    assert len(sec) >= 1, f"no security boundary finding among {len(findings)}"


def test_confirmed_defect_with_evidence(_result: dict) -> None:
    for f in (_result.get("findings") or []):
        assert f.get("gate_passed") is True, f
        assert f.get("bug_status") == "reproduced", f
        assert f.get("customer_delivery_status") == "defect", f
        assert bool(f.get("raw_evidence")), f
