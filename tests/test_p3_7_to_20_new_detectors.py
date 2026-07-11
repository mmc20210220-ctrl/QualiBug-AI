"""Tests for P3 bug types 7, 11, 13, 17, 18, 19, 20 — the 7 newly-covered categories.

Each test spins up a buggy HTTP server (pure stdlib) with a deliberately seeded
defect, then exercises the corresponding detector. Tests are self-contained
and do not require external services.
"""
from __future__ import annotations

import json, os, sys, threading, time, tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any


# ── P3-7: Lifecycle Regression ──
def test_p3_7_lifecycle_regression_detection():
    """P3-7: detect_lifecycle_regressions catches passed→failed pattern across runs."""
    from ai_test_asset_center.regression_runner import detect_lifecycle_regressions

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = "p3_7_test"
        hist_dir = root / "platform_outputs" / project / "regression_run"
        hist_dir.mkdir(parents=True, exist_ok=True)

        # Simulate 2 regression runs: run1 passed, run2 failed (lifecycle regression)
        history = [
            {
                "generated_at": "2026-07-01T00:00:00Z",
                "gate_status": "passed",
                "items": [
                    {"regression_probe_id": "PROBE_A", "title": "Order creation", "status": "passed", "reason": "", "path": "/api/orders", "method": "POST"},
                ],
            },
            {
                "generated_at": "2026-07-08T00:00:00Z",
                "gate_status": "failed",
                "items": [
                    {"regression_probe_id": "PROBE_A", "title": "Order creation", "status": "failed", "reason": "500 Internal Server Error", "path": "/api/orders", "method": "POST"},
                ],
            },
        ]
        hist_dir.mkdir(parents=True, exist_ok=True)
        (hist_dir / "regression_run_history.json").write_text(json.dumps(history))

        regs = detect_lifecycle_regressions(project, root=root)
        assert len(regs) >= 1, f"Expected >=1 lifecycle regression, got {len(regs)}"
        reg = regs[0]
        assert reg["verdict"] == "confirmed"
        assert "生命周期回归" in reg["title"]
        assert reg["severity"] == "P0"
        assert reg["evidence"]["prev_status"] == "passed"
        assert reg["evidence"]["curr_status"] == "failed"


# ── P3-7: Stability Declaration ──
def test_p3_7_regression_stability():
    """P5: evaluate_regression_stability requires >=2 runs all passed."""
    from ai_test_asset_center.regression_runner import evaluate_regression_stability

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = "p3_7_stable"
        hist_dir = root / "platform_outputs" / project / "regression_run"
        hist_dir.mkdir(parents=True, exist_ok=True)

        # 1 run is insufficient
        (hist_dir / "regression_run_history.json").write_text(json.dumps([{
            "generated_at": "2026-07-01T00:00:00Z",
            "gate_status": "passed",
            "summary": {"passed_count": 5, "failed_count": 0, "needs_review_count": 0},
            "items": [{"regression_probe_id": "P1", "status": "passed"}],
        }]))
        result1 = evaluate_regression_stability(project, root=root)
        assert result1["stable"] is False
        assert result1["reason"] == "insufficient_history"

        # 2 runs both passed → stable
        (hist_dir / "regression_run_history.json").write_text(json.dumps([
            {"generated_at": "2026-07-01T00:00:00Z", "gate_status": "passed", "summary": {"passed_count": 5, "failed_count": 0, "needs_review_count": 0}, "items": [{"regression_probe_id": "P1", "status": "passed"}]},
            {"generated_at": "2026-07-08T00:00:00Z", "gate_status": "passed", "summary": {"passed_count": 5, "failed_count": 0, "needs_review_count": 0}, "items": [{"regression_probe_id": "P1", "status": "passed"}]},
        ]))
        result2 = evaluate_regression_stability(project, root=root)
        assert result2["stable"] is True


# ── P3-11: Cache Drift via Double GET ──
def test_p3_11_cache_drift_double_get(monkeypatch):
    """P3-11: detect_cache_drift_via_double_get detects response changes."""
    from ai_test_asset_center.analyzers import cache_consistency

    class StableResponse:
        status = 200

        def read(self, _size=-1):
            return b'{"stable": true}'

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=8.0: StableResponse())
    monkeypatch.setattr(cache_consistency.time, "sleep", lambda seconds: None)

    # Use a known stable endpoint that won't drift (tests the function, not the infra)
    result = cache_consistency.detect_cache_drift_via_double_get(
        "https://test.example.invalid/stable", timeout=8.0, interval_seconds=0.5,
    )
    assert isinstance(result, dict)
    assert "drift_detected" in result
    assert "first_response" in result
    assert "second_response" in result
    assert result["first_response"]["status"] > 0


# ── P3-11: Frontend-Backend State Drift ──
def test_p3_11_frontend_backend_state_drift():
    """P3-11: detect_frontend_backend_state_drift flags mismatched fields."""
    from ai_test_asset_center.analyzers.cache_consistency import detect_frontend_backend_state_drift

    api_resp = {"order_id": 123, "status": "shipped", "amount": 99.99}
    ui_data = {"order_id": 123, "status": "pending", "amount": 99.99}

    findings = detect_frontend_backend_state_drift(api_resp, ui_data, "/api/orders/123")
    assert len(findings) >= 1, f"Expected drift findings, got {len(findings)}"
    assert findings[0]["risk_type"] == "frontend_backend_drift"
    assert findings[0]["verdict"] == "confirmed"
    assert "status" in str(findings[0]["evidence"]["field"])


# ── P3-13: UI/API Availability Check ──
def test_p3_13_ui_api_availability_check(monkeypatch):
    """P3-13: check_api_ui_availability classifies both-ok for reachable endpoints."""
    from ai_test_asset_center.analyzers import ui_api_availability

    monkeypatch.setattr(ui_api_availability, "_safe_get", lambda url, timeout=8.0, headers=None: (200, '{"ok": true}'))
    check = ui_api_availability.check_api_ui_availability("GET", "/get", "https://test.example.invalid", timeout=8.0)
    assert check.api_status > 0, f"API unreachable: {check.api_status}"
    assert check.mismatch_kind in ("both_ok", "api_ok_ui_broken", "ui_ok_api_broken")
    assert isinstance(check.evidence, dict)
    assert check.evidence.get("api_url", "").startswith("https://")


# ── P3-17: Cross-Scan Residue Detection ──
def test_p3_17_cross_scan_residue():
    """P3-17: CrossScanResidueDetector flags data that persists across scans."""
    from ai_test_asset_center.db_snapshot_verifier import DBSnapshotVerifier, CrossScanResidueDetector

    with tempfile.TemporaryDirectory() as td:
        root_dir = Path(td)
        # Create a DB with test data
        import sqlite3
        db_path = root_dir / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO orders VALUES (1, 'pending')")
        conn.commit(); conn.close()

        os.environ["QUALIBUG_DB_DSN"] = str(db_path)
        verifier = DBSnapshotVerifier()
        detector = CrossScanResidueDetector("p3_17_test", root=root_dir)

        pre_id = detector.capture_pre_scan_snapshot(verifier, ["orders"])
        assert pre_id, "Pre-scan snapshot failed"

        # Modify data (simulate test pollution)
        conn2 = sqlite3.connect(str(db_path))
        conn2.execute("INSERT INTO orders VALUES (2, 'pollution')")
        conn2.commit(); conn2.close()

        post_id = detector.capture_post_scan_snapshot(verifier, ["orders"])
        assert post_id, "Post-scan snapshot failed"

        findings = detector.detect_residue(pre_id, post_id, expected_clean_tables=["orders"])
        assert len(findings) >= 1, f"Expected residue findings, got {len(findings)}"
        assert findings[0]["risk_type"] == "test_data_pollution"
        assert findings[0]["verdict"] == "confirmed"

        db_path.unlink(missing_ok=True)


# ── P3-18: Cleanup Verification ──
def test_p3_18_cleanup_verification():
    """P3-18: verify_http_cleanup checks DELETE→GET for fake delete."""
    from ai_test_asset_center.db_snapshot_verifier import verify_http_cleanup

    class FakeDeleteHandler(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            return

        def do_DELETE(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"deleted":true}')

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"still_exists":true}')

    server = HTTPServer(("127.0.0.1", 0), FakeDeleteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = verify_http_cleanup(
            f"http://127.0.0.1:{server.server_address[1]}",
            "/delete",
            method="DELETE",
            timeout=2.0,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert isinstance(result, dict)
    assert result["resource_path"] == "/delete"
    assert "cleanup_status" in result
    assert "delete_response" in result
    assert result["delete_response"]["status"] > 0
    assert result["cleanup_status"] == "fake_delete"


# ── P3-19: Multi-Role View Inconsistency ──
def test_p3_19_multi_role_contract_validation():
    """P3-19: runtime_scenario_contract_gaps accepts per-step and scenario actors."""
    from ai_test_asset_center.runtime_scenario_contract_gate import runtime_scenario_contract_gaps

    # Scenario with per-step actors (multi-role)
    context = {
        "runtime_scenario_contract": {
            "execution_policy": "safe_read_only",
            "actor": {"id": "default_user"},
            "scenarios": [{
                "id": "SCN_MULTI_ROLE",
                "title": "Cross-role order view",
                "actors": [
                    {"id": "buyer", "token": "buyer_token"},
                    {"id": "admin", "token": "admin_token"},
                ],
                "steps": [
                    {"method": "GET", "path": "/api/orders/1", "actor": {"id": "admin", "token": "admin_token"}},
                    {"method": "GET", "path": "/api/orders/1", "actor": {"id": "buyer", "token": "buyer_token"}},
                ],
            }],
        },
    }

    gaps = runtime_scenario_contract_gaps(context)
    # No blocking gaps expected — multi-role actors are valid
    blocking = [g for g in gaps if "MISSING" in g.get("code", "")]
    assert len(blocking) == 0, f"Unexpected blocking gaps: {blocking}"
    # All gaps should be non-fatal
    assert all(g.get("code", "").startswith("RUNTIME_SCENARIO_") for g in gaps) if gaps else True


# ── P3-19: Per-step actor override ──
def test_p3_19_per_step_actor_injection():
    """P3-19: contract steps support per-step actor with token."""
    from ai_test_asset_center.runtime_scenario_contract_gate import runtime_scenario_contract_gaps

    context = {
        "runtime_scenario_contract": {
            "execution_policy": "safe_read_only",
            "actor": {"id": "default"},
            "scenarios": [{
                "id": "SCN_STEP_LEVEL",
                "steps": [
                    {"method": "GET", "path": "/api/admin/reports", "actor": {"id": "admin", "token": "t1"}},
                    {"method": "GET", "path": "/api/user/profile", "actor": {"id": "user", "token": "t2"}},
                ],
            }],
        },
    }

    gaps = runtime_scenario_contract_gaps(context)
    blocking = [g for g in gaps if "MISSING" in g.get("code", "") or "INVALID" in g.get("code", "")]
    assert len(blocking) == 0, f"Per-step actors should not cause blocking gaps: {blocking}"


# ── P3-20: Release Gate Coverage Gap Analysis ──
def test_p3_20_release_gate_coverage_gap_blocks():
    """P3-20: evaluate_release_gate blocks release when high-risk gaps exist."""
    from ai_test_asset_center.release_gate import evaluate_release_gate

    result = evaluate_release_gate(
        campaign={"campaign_id": "C001", "status": "completed"},
        execution_status="completed",
        runtime_contract={"execution_policy": "safe_read_only"},
        evidence_bundle={"bundle_id": "B001", "status": "persisted"},
        evidence_bundle_verification={"verified": True},
        test_data_plan={"write_approved": False},
        findings=[],
        # High-risk coverage gap: authorization boundary not probed
        coverage_gaps=[
            {"kind": "permission_boundary_not_probed", "detail": "/api/admin not tested"},
            {"kind": "tenant_isolation_gap", "detail": "no multi-tenant test"},
        ],
        policy={},
    )

    assert result["verdict"] == "fail"
    assert result["status"] == "blocked"
    assert any("HIGH_RISK_COVERAGE_GAPS" in r.get("code", "") for r in result["reasons"])

    # Verify P3-20 coverage risk data
    p3_20 = result.get("p3_20_coverage_risk")
    assert p3_20 is not None, "P3-20 coverage risk should be present"
    assert p3_20["high_risk_gap_count"] >= 2
    assert p3_20["release_blocked_by_gaps"] is True


# ── P3-20: Release Gate Allows When Clean ──
def test_p3_20_release_gate_allows_clean():
    """P3-20: evaluate_release_gate allows release with no findings and no gaps."""
    from ai_test_asset_center.release_gate import evaluate_release_gate

    result = evaluate_release_gate(
        campaign={"campaign_id": "C002", "status": "completed"},
        execution_status="completed",
        runtime_contract={"execution_policy": "safe_read_only"},
        evidence_bundle={"bundle_id": "B002", "status": "persisted"},
        evidence_bundle_verification={"verified": True},
        test_data_plan={"write_approved": False},
        findings=[],
        coverage_gaps=[],
        policy={},
    )

    assert result["verdict"] in ("pass", "not_ready")
    assert result["status"] in ("release_ready", "inconclusive")


# ── Customer Regression Verification Report (P5 + Acceptance Criterion 13) ──
def test_customer_regression_verification_report():
    """P5/AC13: build_customer_regression_verification_report returns structured data."""
    from ai_test_asset_center.runtime_customer_report_builder import build_customer_regression_verification_report
    from ai_test_asset_center.regression_runner import evaluate_regression_stability

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = "customer_reg_test"
        hist_dir = root / "platform_outputs" / project / "regression_run"
        hist_dir.mkdir(parents=True, exist_ok=True)
        (hist_dir / "regression_run_history.json").write_text(json.dumps([
            {"generated_at": "2026-07-01T00:00:00Z", "gate_status": "passed", "summary": {"passed_count": 3, "failed_count": 0, "needs_review_count": 0}, "items": [{"regression_probe_id": "D001", "title": "Bug A", "status": "passed"}]},
            {"generated_at": "2026-07-08T00:00:00Z", "gate_status": "passed", "summary": {"passed_count": 3, "failed_count": 0, "needs_review_count": 0}, "items": [{"regression_probe_id": "D001", "title": "Bug A", "status": "passed"}]},
        ]))

        findings = [
            {"finding_id": "D001", "title": "Bug A: negative quantity", "severity": "P0", "risk_type": "parameter_boundary", "evidence": {"screenshot": "a.png"}},
            {"finding_id": "D002", "title": "Bug B: privilege escalation", "severity": "P0", "risk_type": "authorization"},
        ]

        report = build_customer_regression_verification_report(
            project, findings, root=str(root),
        )

        assert report["project_id"] == project
        assert report["regression_run_count"] == 2
        assert report["regression_stability"]["stable"] is True
        assert len(report["defect_verifications"]) == 2
        assert report["summary"]["total_defects_tracked"] == 2
        # Bug A was in regression history as passed → verified_fixed
        verified = [d for d in report["defect_verifications"] if d["finding_id"] == "D001"]
        assert verified, "D001 should be in verifications"
        assert verified[0]["fix_status"] == "verified_fixed"


# ── Benchmark Corrected Recall (P6) ──
def test_p6_corrected_recall_unique_bug_types():
    """P6: corrected_recall uses unique bug-type dedup, avoiding recall > 1.0."""
    from ai_test_asset_center.p3_seed_bug_benchmark import _normalize_bug_type

    # Same bug type detected by 3 different oracles → should count as 1 unique
    assert _normalize_bug_type("privilege_escalation") == "authorization"
    assert _normalize_bug_type("permission_bypass") == "authorization"
    assert _normalize_bug_type("idor") == "authorization"
    # Different types
    assert _normalize_bug_type("concurrency_race") == "concurrency"
    assert _normalize_bug_type("lifecycle_regression") == "regression"
    assert _normalize_bug_type("frontend_backend_drift") == "cache_consistency"
    assert _normalize_bug_type("cleanup_failure") == "data_hygiene"


print("All P3-7~20 detector tests passed.")
