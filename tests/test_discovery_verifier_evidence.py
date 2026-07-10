from ai_test_asset_center import discovery_engine as discovery_module
from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine


def _engine(monkeypatch):
    engine = object.__new__(AutonomousDiscoveryEngine)
    engine.findings = []
    engine._budget_learning_context = {"project_id": "test_project"}
    engine._budget_feedback_summary = {}
    monkeypatch.setattr(discovery_module, "persist_budget_feedback_profile", lambda *args, **kwargs: None)
    return engine


def test_stage_verify_confirms_server_error_from_http_evidence(monkeypatch):
    engine = _engine(monkeypatch)

    findings = engine.stage_verify([
        {
            "hypothesis_id": "H-500",
            "title": "server should not throw exception",
            "severity": "P0",
            "expected_behavior": "API must not return 500",
            "evidence": {
                "calls": [
                    {
                        "call": "GET /api/orders/42",
                        "results": {
                            "admin": {"status": 500, "body": {"error": "NullPointerException"}},
                            "viewer": {"status": 403, "body": {}},
                            "no_auth": {"status": 401, "body": {}},
                        },
                    }
                ]
            },
        }
    ])

    assert len(findings) == 1
    assert findings[0].verdict == "confirmed"
    assert findings[0].confidence >= 0.9
    assert "HTTP500" in findings[0].actual


def test_stage_verify_downgrades_synthetic_id_500_to_inconclusive(monkeypatch):
    engine = _engine(monkeypatch)

    findings = engine.stage_verify([
        {
            "hypothesis_id": "H-SYN-500",
            "title": "server should not throw exception",
            "severity": "P0",
            "expected_behavior": "API must not return 500",
            "evidence": {
                "calls": [
                    {
                        "call": "GET /api/patients/1",
                        "path": "/api/patients/1",
                        "synthetic_id": True,
                        "results": {
                            "admin": {"status": 500, "body": {"error": "invalid uuid"}},
                            "viewer": {"status": 403, "body": {}},
                            "no_auth": {"status": 401, "body": {}},
                        },
                    }
                ]
            },
        }
    ])

    assert findings[0].verdict == "inconclusive"
    assert findings[0].evidence.get("probe_quality_gate") == "synthetic_or_unresolved_id"
    assert "合成" in findings[0].actual or "伪影" in findings[0].actual


def test_stage_verify_downgrades_unresolved_route_500_to_inconclusive(monkeypatch):
    engine = _engine(monkeypatch)

    findings = engine.stage_verify([
        {
            "hypothesis_id": "H-UNRES-500",
            "title": "server should not throw exception",
            "severity": "P0",
            "expected_behavior": "API must not return 500",
            "evidence": {
                "calls": [
                    {
                        "call": "GET /api/contracts/1",
                        "path": "/api/contracts/1",
                        "unresolved_route": True,
                        "synthetic_id": True,
                        "results": {
                            "admin": {"status": 500, "body": {"ok": False}},
                            "viewer": {"status": 403, "body": {}},
                            "no_auth": {"status": 401, "body": {}},
                        },
                    }
                ]
            },
        }
    ])

    assert findings[0].verdict == "inconclusive"
    assert findings[0].evidence.get("probe_quality") == "synthetic_or_unresolved_id"


def test_stage_verify_bare_5xx_without_expected_error_is_inconclusive(monkeypatch):
    engine = _engine(monkeypatch)

    findings = engine.stage_verify([
        {
            "hypothesis_id": "H-BARE-500",
            "title": "order amount conservation after refund",
            "severity": "P1",
            "expected_behavior": "refund must keep ledger balance conserved",
            "evidence": {
                "calls": [
                    {
                        "call": "POST /api/orders/42/refund",
                        "path": "/api/orders/42/refund",
                        "results": {
                            "admin": {"status": 500, "body": {"error": "boom"}},
                            "viewer": {"status": 403, "body": {}},
                            "no_auth": {"status": 401, "body": {}},
                        },
                    }
                ]
            },
        }
    ])

    assert findings[0].verdict == "inconclusive"
    assert findings[0].evidence.get("probe_quality_gate") == "bare_5xx_without_expected_error"


def test_stage_verify_confirms_auth_bypass_only_with_business_data(monkeypatch):
    engine = _engine(monkeypatch)

    findings = engine.stage_verify([
        {
            "hypothesis_id": "H-AUTH",
            "title": "anonymous auth permission bypass exposes order data",
            "severity": "P0",
            "expected_behavior": "anonymous access should be rejected with 401 or 403",
            "evidence": {
                "calls": [
                    {
                        "call": "GET /api/orders/42",
                        "results": {
                            "admin": {"status": 200, "body": {"data": {"id": 42, "amount": 100}}},
                            "viewer": {"status": 403, "body": {}},
                            "no_auth": {"status": 200, "body": {"data": {"id": 42, "amount": 100}}},
                        },
                    }
                ]
            },
        }
    ])

    assert findings[0].verdict == "confirmed"
    assert findings[0].confidence >= 0.9
    assert "业务数据" in findings[0].actual


def test_stage_verify_falsifies_when_auth_boundary_is_enforced(monkeypatch):
    engine = _engine(monkeypatch)

    findings = engine.stage_verify([
        {
            "hypothesis_id": "H-AUTH-OK",
            "title": "anonymous auth permission bypass",
            "severity": "P1",
            "expected_behavior": "anonymous access should be rejected",
            "evidence": {
                "calls": [
                    {
                        "call": "GET /api/orders/42",
                        "results": {
                            "admin": {"status": 200, "body": {"data": {"id": 42}}},
                            "viewer": {"status": 403, "body": {}},
                            "no_auth": {"status": 401, "body": {}},
                        },
                    }
                ]
            },
        }
    ])

    assert findings[0].verdict == "falsified"
    assert findings[0].confidence >= 0.8
    assert "认证边界" in findings[0].actual or "无认证被拒绝" in findings[0].actual
