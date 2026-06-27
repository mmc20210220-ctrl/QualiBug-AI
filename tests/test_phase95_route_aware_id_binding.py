from __future__ import annotations

from typing import Any

from ai_test_asset_center import grounded_probe_executor as gpe
from ai_test_asset_center.grounded_probe_executor import ProbeDecision, _execute_flow_probe, _execute_write_probe


def _write_probe() -> dict[str, Any]:
    return {
        "candidate_id": "QB-ROUTE-ID-1",
        "risk_type": "state_transition_probe",
        "execution_policy": "disposable_sandbox_required",
        "endpoint": {"method": "PATCH", "path": "/api/v1/orders/{order_id}"},
        "probe_plan": {"expected_status": [400, 403, 409, 422]},
        "source_refs": [
            {"kind": "endpoint_contract", "file": "openapi.yaml", "quote": "PATCH /orders/{order_id}"},
            {"kind": "business_rule", "file": "prd.md", "quote": "orders may not be mutated after terminal state"},
        ],
        "grounding_basis": {"endpoint_contract_refs": 1, "supporting_requirement_refs": 1},
    }


def _write_decision(probe: dict[str, Any]) -> ProbeDecision:
    ep = probe["endpoint"]
    return ProbeDecision(
        candidate_id=probe["candidate_id"],
        risk_type=probe["risk_type"],
        method=ep["method"],
        path=ep["path"],
        execution_policy="disposable_sandbox_required",
        decision="execute_write_sandbox",
        reason="eligible_disposable_sandbox_write_probe",
        request={"method": ep["method"], "path": ep["path"], "url": "http://sandbox/api/v1/orders/qb_auto_order_1"},
    )


def _write_config(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "qualibug_auto_create_test_data": True,
        "default_headers": {"Authorization": "Bearer sandbox-admin"},
        "_auto_fixture_runtime": {
            probe["candidate_id"]: {
                "request_body": {"order_id": "qb_auto_order_1", "status": "cancelled"},
                "path_params": {"order_id": "qb_auto_order_1"},
                "setup_requests": [
                    {
                        "method": "POST",
                        "path": "/api/v1/orders",
                        "body": {"id": "qb_auto_order_1"},
                        "bind_response_id_to": ["order_id"],
                    }
                ],
                "snapshots": {
                    "after": [
                        {
                            "method": "GET",
                            "path": "/api/v1/orders/{order_id}",
                            "path_params": {"order_id": "qb_auto_order_1"},
                        }
                    ]
                },
                "cleanup_requests": [
                    {"method": "DELETE", "path": "/api/v1/orders/{order_id}", "path_params": {"order_id": "qb_auto_order_1"}}
                ],
                "receipt": {"primary_fixture_id": "qb_auto_order_1"},
            }
        },
    }


def test_setup_binding_prefers_order_id_over_unrelated_nested_customer_id(monkeypatch) -> None:
    probe = _write_probe()
    config = _write_config(probe)
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "body": body})
        if method == "POST" and url.endswith("/api/v1/orders"):
            return {
                "status_code": 201,
                "payload": {
                    "customer": {"id": "cust_should_not_bind"},
                    "order": {"id": "srv_order_route_123"},
                },
                "duration_ms": 1,
            }
        return {"status_code": 200, "payload": {"id": "ok"}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)

    result = _execute_write_probe(probe, _write_decision(probe), config, "http://sandbox", timeout=3.0)

    assert config["_auto_fixture_runtime"][probe["candidate_id"]]["path_params"]["order_id"] == "srv_order_route_123"
    assert result["request"]["url"].endswith("/api/v1/orders/srv_order_route_123")
    assert any(c["method"] == "PATCH" and c["url"].endswith("/api/v1/orders/srv_order_route_123") for c in calls)
    assert not any("cust_should_not_bind" in c["url"] for c in calls)


def _flow_probe() -> dict[str, Any]:
    return {
        "candidate_id": "QB-ROUTE-FLOW-1",
        "risk_type": "business_flow_sequence_probe",
        "execution_policy": "disposable_sandbox_required",
        "endpoint": {"method": "POST", "path": "/api/v1/orders/{order_id}/approve"},
        "probe_plan": {
            "strategy": "illegal_order_inversion_flow",
            "flow_scenario": {
                "strategy": "illegal_order_inversion_flow",
                "steps": [
                    {"action": "create", "method": "POST", "path": "/api/v1/orders"},
                    {"action": "approve", "method": "POST", "path": "/api/v1/orders/{order_id}/approve"},
                ],
            },
        },
        "source_refs": [
            {"kind": "endpoint_contract", "file": "openapi.yaml", "quote": "POST /orders/{order_id}/approve"},
            {"kind": "business_rule", "file": "prd.md", "quote": "approval must follow payment"},
        ],
        "grounding_basis": {"endpoint_contract_refs": 1, "supporting_requirement_refs": 1},
    }


def _flow_decision(probe: dict[str, Any]) -> ProbeDecision:
    ep = probe["endpoint"]
    return ProbeDecision(
        candidate_id=probe["candidate_id"],
        risk_type=probe["risk_type"],
        method=ep["method"],
        path=ep["path"],
        execution_policy="disposable_sandbox_required",
        decision="execute_write_sandbox",
        reason="eligible_multi_step_flow_probe",
        request={"method": ep["method"], "path": ep["path"], "url": "http://sandbox/api/v1/orders/qb_auto_flow_1/approve"},
    )


def _flow_config(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "qualibug_auto_create_test_data": True,
        "default_headers": {"Authorization": "Bearer sandbox-admin"},
        "_auto_fixture_runtime": {
            probe["candidate_id"]: {
                "request_body": {"order_id": "qb_auto_flow_1", "status": "draft"},
                "path_params": {"order_id": "qb_auto_flow_1"},
                "setup_requests": [],
                "snapshots": {"after": []},
                "cleanup_requests": [],
                "receipt": {"primary_fixture_id": "qb_auto_flow_1"},
            }
        },
    }


def test_flow_binding_prefers_route_resource_id_from_nested_payload(monkeypatch) -> None:
    probe = _flow_probe()
    config = _flow_config(probe)
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "body": body})
        if method == "POST" and url.endswith("/api/v1/orders"):
            return {
                "status_code": 201,
                "payload": {
                    "customer": {"id": "cust_should_not_bind"},
                    "order": {"id": "srv_flow_order_route_456"},
                },
                "duration_ms": 1,
            }
        return {"status_code": 200, "payload": {"approval": {"id": "approval_1"}}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)

    result = _execute_flow_probe(probe, _flow_decision(probe), config, "http://sandbox", timeout=3.0)

    assert result["responses"][0]["runtime_binding"]["response_id"] == "srv_flow_order_route_456"
    assert any(c["method"] == "POST" and c["url"].endswith("/api/v1/orders/srv_flow_order_route_456/approve") for c in calls)
    assert not any("cust_should_not_bind" in c["url"] for c in calls)
