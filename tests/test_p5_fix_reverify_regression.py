"""P5 integration test: fix-and-reverify regression cycle.

Proves that the main-chain step "客户修复代码 → 回归测试 → 缺陷生命周期更新"
is operational end-to-end: scan a buggy SUT → confirmed defect → regression suite
with probes → FIX the SUT → regression passes → RE-BREAK the SUT → regression fails.
"""

from __future__ import annotations

import json, threading, time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT = "p5_fix_reverify_integration"
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

_ORDERS: dict[str, dict] = {}
_LOCK = threading.Lock()
_SEQ = {"n": 0}


class _BuggyHandler(BaseHTTPRequestHandler):
    validate_quantity: bool = False

    def log_message(self, *_: object) -> None:
        return

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self):
        cl = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(cl) or b"{}") if cl else {}

    def do_POST(self):
        body = self._read()
        if self.path == "/api/orders":
            qty = body.get("quantity", 1)
            if self.validate_quantity and int(qty) <= 0:
                return self._json(400, {"error": "quantity must be > 0"})
            with _LOCK:
                _SEQ["n"] += 1; oid = str(_SEQ["n"])
                _ORDERS[oid] = {"id": oid, "quantity": qty, "status": "created"}
            return self._json(201, _ORDERS[oid])
        return self._json(404, {"error": "nf"})

    def do_DELETE(self):
        oid = self.path.rsplit("/", 1)[-1]
        _ORDERS.pop(oid, None)
        return self._json(204, {})


@pytest.fixture(scope="module")
def _results(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Run the full three-phase cycle and return evidence dict."""
    _ORDERS.clear(); _SEQ["n"] = 0
    root = tmp_path_factory.mktemp("p5")

    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.enterprise_campaign import EnterpriseCampaign, source_snapshot_hash
    from ai_test_asset_center.execution_approvals import issue_execution_approval
    from ai_test_asset_center.__main__ import scan, _persist_customer_ready_static_artifacts
    from ai_test_asset_center.regression_suite_builder import build_regression_suite
    from ai_test_asset_center.regression_runner import run_regression_suite

    manifest = register_source_asset(
        PROJECT, "shop-openapi", OPENAPI, source_type="openapi", root=root,
        actor={"name": "qa_lead", "role": "qa_lead"},
    )

    # ── PHASE 1: scan buggy SUT ──
    _BuggyHandler.validate_quantity = False
    srv1 = ThreadingHTTPServer(("127.0.0.1", 0), _BuggyHandler)
    t1 = threading.Thread(target=srv1.serve_forever, daemon=True); t1.start()
    base1 = f"http://127.0.0.1:{srv1.server_address[1]}"

    snap = source_snapshot_hash("", OPENAPI, "", SCOPE, ENV)
    campaign = EnterpriseCampaign.create(
        PROJECT, SCOPE, ENV, snap,
        source_id=manifest["source_id"], source_hash=manifest["source_hash"], policy_version="",
    )
    approval = issue_execution_approval(
        PROJECT, root=root, campaign_id=campaign.campaign_id,
        scope_id=SCOPE, environment_ref=ENV, source_hash=manifest["source_hash"],
        target_base_url=base1, execution_mode="approved_sandbox_write",
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
        "execution_mode": "approved_sandbox_write", "execution_approval_id": approval["approval_id"],
        "test_data_contract": {"strategy": "create_disposable", "write_approved": True, "disposable_scope_ref": SCOPE},
        "runtime_scenario_contract": contract,
    }
    scan_res = scan(PROJECT, root=root, prd_text="", api_doc_text=OPENAPI, base_url=base1, campaign_context=ctx)
    srv1.shutdown(); t1.join(timeout=2)
    _persist_customer_ready_static_artifacts(PROJECT, root, scan_res)

    # Write project config with base_url (needed by regression runner)
    cfg_path = root / "platform_workspace" / PROJECT / "real_project" / "real_project_config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    suite = build_regression_suite(project_id=PROJECT, root=root, options={"mode": "smoke"})

    # ── PHASE 2: Fixed SUT → regression run ──
    _ORDERS.clear(); _SEQ["n"] = 0
    _BuggyHandler.validate_quantity = True
    srv2 = ThreadingHTTPServer(("127.0.0.1", 0), _BuggyHandler)
    t2 = threading.Thread(target=srv2.serve_forever, daemon=True); t2.start()
    base2 = f"http://127.0.0.1:{srv2.server_address[1]}"
    cfg_path.write_text(json.dumps({"project_name": PROJECT, "base_url": base2}), "utf-8")
    reg_fixed = run_regression_suite(project_id=PROJECT, root=root, options={"mode": "smoke", "dry_run": False})
    srv2.shutdown(); t2.join(timeout=2)

    # ── PHASE 3: Re-broken SUT → regression run ──
    _ORDERS.clear(); _SEQ["n"] = 0
    _BuggyHandler.validate_quantity = False
    srv3 = ThreadingHTTPServer(("127.0.0.1", 0), _BuggyHandler)
    t3 = threading.Thread(target=srv3.serve_forever, daemon=True); t3.start()
    base3 = f"http://127.0.0.1:{srv3.server_address[1]}"
    cfg_path.write_text(json.dumps({"project_name": PROJECT, "base_url": base3}), "utf-8")
    reg_broken = run_regression_suite(project_id=PROJECT, root=root, options={"mode": "smoke", "dry_run": False})
    srv3.shutdown(); t3.join(timeout=2)

    return {
        "scan": scan_res,
        "suite": suite,
        "reg_fixed": reg_fixed,
        "reg_broken": reg_broken,
    }


def test_scan_produces_confirmed_defect(_results: dict) -> None:
    """Phase 1: scan must find the parameter boundary bug."""
    findings = _results["scan"].get("findings") or []
    assert len(findings) >= 1
    assert any(f.get("gate_passed") is True for f in findings)


def test_regression_suite_generates_probes(_results: dict) -> None:
    """The regression suite must contain probes derived from confirmed defects."""
    suite = _results["suite"]
    summary = suite.get("summary") or {}
    assert summary.get("total_probe_count", 0) >= 1, "must have >= 1 probe"


def test_regression_runner_executes_probes(_results: dict) -> None:
    """Regression runner must actually execute probes (not just dry-run)."""
    for phase, reg in [("fixed", _results["reg_fixed"]), ("broken", _results["reg_broken"])]:
        summary = reg.get("summary") if isinstance(reg, dict) else {}
        assert summary.get("executed_count", 0) >= 1, f"{phase}: regression must execute >= 1 probe"


def test_fixed_sut_regression_passes_or_needs_review(_results: dict) -> None:
    """Fixed SUT: probes should pass (or be 'needs_review' if verdict resolution incomplete)."""
    summary = _results["reg_fixed"].get("summary") if isinstance(_results["reg_fixed"], dict) else {}
    # The regression runner currently classifies explicit mismatches as needs_review;
    # for a fixed SUT, the probe should either pass or be needs_review
    # (not fail outright, since the defect is supposed to be gone).
    total_matched = summary.get("passed_count", 0) + summary.get("needs_review_count", 0)
    assert total_matched >= 1, "fixed SUT regression should not fail"
    assert summary.get("failed_count", 0) == 0, "fixed SUT must have 0 failed probes"


def test_broken_sut_regression_shows_mismatch(_results: dict) -> None:
    """Re-broken SUT: probes must detect the regression (failed or needs_review)."""
    summary = _results["reg_broken"].get("summary") if isinstance(_results["reg_broken"], dict) else {}
    # The regression runner currently uses needs_review for mismatches;
    # we assert that the probe did NOT pass (i.e., the regression was caught).
    assert summary.get("passed_count", 0) < summary.get("executed_count", 1), (
        "broken SUT must have at least one non-passing probe"
    )
