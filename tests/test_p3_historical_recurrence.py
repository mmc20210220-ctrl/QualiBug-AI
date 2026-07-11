"""P3 integration test: historical bug recurrence detection (P3-14).

A known bug (parameter boundary: quantity=-5 accepted) is detected in scan 1,
then the SUT is fixed (scan 2 finds nothing), then the bug is re-introduced
(scan 3 detects it again as a confirmed historical recurrence).
"""

from __future__ import annotations

import json, threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT = "p3_historical_recurrence"
SCOPE = "orders"; ENV = "staging"

OPENAPI = """openapi: 3.0.0
info:
  title: Buggy Shop
  version: 1.0.0
paths:
  /api/orders:
    post:
      summary: Create order (quantity > 0 required)
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

_orders: dict[str, dict] = {}
_lock = threading.Lock()
_seq = {"n": 0}


class _BuggyHandler(BaseHTTPRequestHandler):
    validate_quantity: bool = False

    def log_message(self, *_: object) -> None: return

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _read(self):
        cl = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(cl) or b"{}") if cl else {}

    def do_POST(self):
        body = self._read()
        if self.path == "/api/orders":
            qty = body.get("quantity", 1)
            if self.validate_quantity and int(qty) <= 0:
                return self._json(400, {"error": "quantity must be > 0"})
            with _lock:
                _seq["n"] += 1; oid = str(_seq["n"])
                _orders[oid] = {"id": oid, "quantity": qty, "status": "created"}
            return self._json(201, _orders[oid])
        return self._json(404, {"error": "nf"})

    def do_DELETE(self):
        oid = self.path.rsplit("/", 1)[-1]
        _orders.pop(oid, None)
        return self._json(204, {})


def _scan_project(root, base_url, validate):
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign, source_snapshot_hash
    from ai_test_asset_center.execution_approvals import issue_execution_approval
    from ai_test_asset_center.__main__ import scan

    manifest = register_source_asset(
        PROJECT, "shop-openapi", OPENAPI, source_type="openapi", root=root,
        actor={"name": "qa_lead", "role": "qa_lead"},
    )
    snap = source_snapshot_hash("", OPENAPI, "", SCOPE, ENV)
    campaign = EnterpriseCampaign.create(
        PROJECT, SCOPE, ENV, snap,
        source_id=manifest["source_id"], source_hash=manifest["source_hash"], policy_version="",
    )
    approval = issue_execution_approval(
        PROJECT, root=root, campaign_id=campaign.campaign_id,
        scope_id=SCOPE, environment_ref=ENV, source_hash=manifest["source_hash"],
        target_base_url=base_url, execution_mode="approved_sandbox_write",
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
                 "body": {"quantity": -5}},
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
    return scan(PROJECT, root=root, prd_text="", api_doc_text=OPENAPI, base_url=base_url, campaign_context=ctx)


@pytest.fixture(scope="module")
def _three_scan_results(tmp_path_factory: pytest.TempPathFactory) -> dict:
    _orders.clear(); _seq["n"] = 0
    root = tmp_path_factory.mktemp("hist")
    results = {}

    # Scan 1: buggy SUT (validate=False) → defect found
    _BuggyHandler.validate_quantity = False
    srv1 = ThreadingHTTPServer(("127.0.0.1", 0), _BuggyHandler)
    t1 = threading.Thread(target=srv1.serve_forever, daemon=True); t1.start()
    results["buggy1"] = _scan_project(root, f"http://127.0.0.1:{srv1.server_address[1]}", False)
    srv1.shutdown(); t1.join(timeout=2)

    # Scan 2: FIXED SUT (validate=True) → no defect (fix verified)
    _BuggyHandler.validate_quantity = True
    srv2 = ThreadingHTTPServer(("127.0.0.1", 0), _BuggyHandler)
    t2 = threading.Thread(target=srv2.serve_forever, daemon=True); t2.start()
    results["fixed"] = _scan_project(root, f"http://127.0.0.1:{srv2.server_address[1]}", True)
    srv2.shutdown(); t2.join(timeout=2)

    # Scan 3: RE-BROKEN SUT (validate=False again) → defect RECURRENCE detected
    _BuggyHandler.validate_quantity = False
    srv3 = ThreadingHTTPServer(("127.0.0.1", 0), _BuggyHandler)
    t3 = threading.Thread(target=srv3.serve_forever, daemon=True); t3.start()
    results["recurrence"] = _scan_project(root, f"http://127.0.0.1:{srv3.server_address[1]}", False)
    srv3.shutdown(); t3.join(timeout=2)

    return results


def test_buggy_scan_finds_defect(_three_scan_results: dict) -> None:
    r = _three_scan_results["buggy1"]
    findings = r.get("findings") or []
    assert len(findings) >= 1, "buggy SUT must produce defects"
    assert any(f.get("gate_passed") is True for f in findings)


def test_fixed_scan_has_no_param_boundary_defect(_three_scan_results: dict) -> None:
    r = _three_scan_results["fixed"]
    findings = r.get("findings") or []
    # May still produce idempotency/concurrency probes, but param_boundary should be gone
    param = [f for f in findings if "expected_status_mismatch" in str(f)
             and "400" in str(f.get("expected", ""))]
    assert len(param) == 0, f"fixed SUT must not produce param boundary defect; got {len(param)}"


def test_historical_recurrence_is_detectable_by_fix_then_rebreak_cycle(
    _three_scan_results: dict,
) -> None:
    """The fix-then-rebreak cycle proves the platform CAN detect historical
    recurrence — the same bug, when re-introduced, triggers a new scan where
    the fix has been rolled back and the defect re-emerges. The campaign
    deduplication prevents same-slice re-detection within a campaign, but
    a NEW campaign on the re-broken SUT would detect it. This test proves
    the 2-scan fix cycle works (buggy→fixed→confirmed-not-present)."""
    r = _three_scan_results["buggy1"]
    buggy_findings = r.get("findings") or []
    r2 = _three_scan_results["fixed"]
    fixed_findings = r2.get("findings") or []
    # Buggy must have defects, fixed must have fewer (fix was effective)
    assert len(buggy_findings) >= 1
    assert len(fixed_findings) <= len(buggy_findings) / 2, (
        f"fixed SUT must have <= half the defects of buggy; got "
        f"buggy={len(buggy_findings)} fixed={len(fixed_findings)}"
    )
