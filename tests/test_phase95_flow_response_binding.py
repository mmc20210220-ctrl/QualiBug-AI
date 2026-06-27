from __future__ import annotations

from typing import Any

from ai_test_asset_center import grounded_probe_executor as gpe
from ai_test_asset_center.grounded_probe_executor import ProbeDecision, _execute_flow_probe


def _flow_probe() -> dict[str, Any]:
    return {
        "candidate_id": "QBFLOW-BIND-1",
        "risk_type": "illegal_order_inversion_flow",
        "execution_policy": "disposable_sandbox_required",
        "endpoint": {"method": "POST", "path": "/api/v1/orders/{order_id}/approve"},
        "probe_plan": {
            "strategy": "illegal_order_inversion_flow",
            "flow_scenario": {
                "strategy": "illegal_order_inversion_flow",
                "steps": [
                    {"action": "create", "method": "POST", "path": "/api/v1/orders"},
                    {"action": "approve_without_payment", "method": "POST", "path": "/api/v1/orders/{order_id}/approve"},
                ],
            },
        },
        "source_refs": [
            {"kind": "endpoint_contract", "file": "openapi.yaml", "quote": "POST /orders/{order_id}/approve"},
            {"kind": "business_rule", "file": "prd.md", "quote": "orders must be paid before approval"},
        ],
        "grounding_basis": {"endpoint_contract_refs": 1, "supporting_requirement_refs": 1},
    }


def _decision(probe: dict[str, Any]) -> ProbeDecision:
    ep = probe["endpoint"]
    return ProbeDecision(
        candidate_id=probe["candidate_id"],
        risk_type=probe["risk_type"],
        method=ep["method"],
        path=ep["path"],
        execution_policy="disposable_sandbox_required",
        decision="execute_flow_sandbox",
        reason="eligible_multi_step_flow_probe",
        request={"method": ep["method"], "path": ep["path"], "url": "http://sandbox/api/v1/orders/qb_auto_flow_1/approve"},
    )


def _config(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "qualibug_auto_create_test_data": True,
        "default_headers": {"Authorization": "Bearer sandbox-admin", "X-Tenant-Id": "tenant-a"},
        "_auto_fixture_runtime": {
            probe["candidate_id"]: {
                "request_body": {"order_id": "qb_auto_flow_1", "status": "draft"},
                "path_params": {"order_id": "qb_auto_flow_1"},
                "setup_requests": [],
                "snapshots": {
                    "after": [
                        {
                            "method": "GET",
                            "path": "/api/v1/orders/{order_id}",
                            "path_params": {"order_id": "qb_auto_flow_1"},
                            "observer_kind": "api_get_after_flow",
                        }
                    ]
                },
                "cleanup_requests": [
                    {"method": "DELETE", "path": "/api/v1/orders/{order_id}", "path_params": {"order_id": "qb_auto_flow_1"}}
                ],
                "receipt": {"primary_fixture_id": "qb_auto_flow_1"},
            }
        },
    }


def test_flow_step_response_id_rebinds_following_step_and_after_snapshot(monkeypatch) -> None:
    probe = _flow_probe()
    config = _config(probe)
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if method == "POST" and url.endswith("/api/v1/orders"):
            return {"status_code": 201, "payload": {"id": "srv_flow_order_123"}, "duration_ms": 1}
        if method == "POST" and url.endswith("/api/v1/orders/srv_flow_order_123/approve"):
            return {"status_code": 200, "payload": {"id": "srv_flow_order_123", "status": "approved"}, "duration_ms": 1}
        return {"status_code": 200, "payload": {"id": "srv_flow_order_123", "status": "approved"}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)

    result = _execute_flow_probe(probe, _decision(probe), config, "http://sandbox", timeout=3.0)

    assert any(c["method"] == "POST" and c["url"].endswith("/api/v1/orders") for c in calls)
    assert any(c["method"] == "POST" and c["url"].endswith("/api/v1/orders/srv_flow_order_123/approve") for c in calls)
    assert any(c["method"] == "GET" and c["url"].endswith("/api/v1/orders/srv_flow_order_123") for c in calls)
    assert config["_auto_fixture_runtime"]["QBFLOW-BIND-1"]["path_params"]["order_id"] == "srv_flow_order_123"
    assert result["responses"][0]["runtime_binding"]["response_id"] == "srv_flow_order_123"
    assert result["responses"][0]["runtime_binding"]["path_params"] == ["order_id"]


def test_flow_does_not_overwrite_bound_resource_id_with_later_step_side_effect_id(monkeypatch) -> None:
    probe = _flow_probe()
    config = _config(probe)
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if method == "POST" and url.endswith("/api/v1/orders"):
            return {"status_code": 201, "payload": {"id": "srv_flow_order_123"}, "duration_ms": 1}
        if method == "POST" and url.endswith("/api/v1/orders/srv_flow_order_123/approve"):
            return {"status_code": 200, "payload": {"id": "approval_side_effect_999"}, "duration_ms": 1}
        return {"status_code": 200, "payload": {"id": "srv_flow_order_123"}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)

    result = _execute_flow_probe(probe, _decision(probe), config, "http://sandbox", timeout=3.0)

    assert config["_auto_fixture_runtime"]["QBFLOW-BIND-1"]["path_params"]["order_id"] == "srv_flow_order_123"
    assert result["responses"][1]["runtime_binding"] == {}
