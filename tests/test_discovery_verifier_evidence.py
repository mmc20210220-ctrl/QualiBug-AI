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
