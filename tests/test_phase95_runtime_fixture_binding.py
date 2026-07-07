from __future__ import annotations

from typing import Any

from ai_test_asset_center import grounded_probe_executor as gpe
from ai_test_asset_center.grounded_probe_executor import ProbeDecision, _execute_write_probe


def _probe(risk_type: str = "state_transition_probe") -> dict[str, Any]:
    return {
        "candidate_id": "QBFIX-1",
        "risk_type": risk_type,
        "execution_policy": "disposable_sandbox_required",
        "endpoint": {"method": "PATCH", "path": "/api/v1/orders/{order_id}"},
        "probe_plan": {"expected_status": [401, 403, 404, 409, 422]},
        "source_refs": [
            {"kind": "endpoint_contract", "file": "openapi.yaml", "quote": "PATCH /orders/{order_id}"},
            {"kind": "business_rule", "file": "prd.md", "quote": "terminal state transition must be rejected"},
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
        decision="execute_write_sandbox",
        reason="eligible_disposable_sandbox_write_probe",
        request={"method": ep["method"], "path": ep["path"], "url": "http://sandbox/api/v1/orders/qb_auto_client_1"},
    )


def _config(probe: dict[str, Any], *, auth: bool = True) -> dict[str, Any]:
    headers = {"Authorization": "Bearer sandbox-admin", "X-Tenant-Id": "tenant-a"} if auth else {}
    return {
        "qualibug_auto_create_test_data": True,
        "default_headers": headers,
        "_auto_fixture_runtime": {
            probe["candidate_id"]: {
                "mode": "auto_generated_by_qualibug",
                "request_body": {
                    "id": "qb_auto_client_1",
                    "object_id": "qb_auto_client_1",
                    "order_id": "qb_auto_client_1",
                    "status": "cancelled",
                },
                "path_params": {"order_id": "qb_auto_client_1"},
                "headers": {},
                "setup_requests": [
                    {
                        "purpose": "create_disposable_qb_auto_fixture",
                        "method": "POST",
                        "path": "/api/v1/orders",
                        "body": {"id": "qb_auto_client_1", "name": "fixture"},
                        "bind_response_id_to": ["order_id"],
                    }
                ],
                "snapshots": {
                    "before": [{"method": "GET", "path": "/api/v1/orders/{order_id}", "path_params": {"order_id": "qb_auto_client_1"}}],
                    "after": [{"method": "GET", "path": "/api/v1/orders/{order_id}", "path_params": {"order_id": "qb_auto_client_1"}}],
                },
                "cleanup_requests": [
                    {"method": "DELETE", "path": "/api/v1/orders/{order_id}", "path_params": {"order_id": "qb_auto_client_1"}, "purpose": "cleanup_qb_auto_fixture"}
                ],
                "receipt": {"primary_fixture_id": "qb_auto_client_1"},
            }
        },
    }


def test_setup_response_id_rebinds_main_request_snapshots_and_cleanup(monkeypatch) -> None:
    probe = _probe()
    config = _config(probe)
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if method == "POST":
            return {"status_code": 201, "payload": {"id": "srv_order_123"}, "duration_ms": 1}
        return {"status_code": 200, "payload": {"id": "srv_order_123", "status": "ok"}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)

    result = _execute_write_probe(probe, _decision(probe), config, "http://sandbox", timeout=3.0)

    assert config["_auto_fixture_runtime"]["QBFIX-1"]["path_params"]["order_id"] == "srv_order_123"
    assert config["_auto_fixture_runtime"]["QBFIX-1"]["request_body"]["order_id"] == "srv_order_123"
    assert result["request"]["url"].endswith("/api/v1/orders/srv_order_123")
    assert any(c["method"] == "PATCH" and c["url"].endswith("/api/v1/orders/srv_order_123") for c in calls)
    assert any(c["method"] == "GET" and c["url"].endswith("/api/v1/orders/srv_order_123") for c in calls)
    assert any(c["method"] == "DELETE" and c["url"].endswith("/api/v1/orders/srv_order_123") for c in calls)
    assert result["fixture_receipts"][0]["runtime_binding"]["response_id"] == "srv_order_123"


def test_auth_boundary_fixture_setup_uses_control_headers_but_target_is_negative_actor(monkeypatch) -> None:
    probe = _probe("anonymous_auth_boundary_probe")
    probe["probe_plan"] = {
        "auth_boundary": {"actor": "anonymous", "credential_profile": "no_credentials", "expected_status": [401, 403, 404]},
        "negative_headers": ["Authorization", "Cookie", "X-Tenant-Id"],
    }
    config = _config(probe)
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if method == "POST":
            return {"status_code": 201, "payload": {"id": "srv_private_1"}, "duration_ms": 1}
        return {"status_code": 200, "payload": {"id": "srv_private_1", "owner": "tenant-a"}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)

    _execute_write_probe(probe, _decision(probe), config, "http://sandbox", timeout=3.0)

    setup_call = next(c for c in calls if c["method"] == "POST")
    target_call = next(c for c in calls if c["method"] == "PATCH")
    cleanup_call = next(c for c in calls if c["method"] == "DELETE")
    assert setup_call["headers"]["Authorization"] == "Bearer sandbox-admin"
    assert cleanup_call["headers"]["Authorization"] == "Bearer sandbox-admin"
    assert "Authorization" not in target_call["headers"]
    assert "X-Tenant-Id" not in target_call["headers"]


def test_setup_request_falls_back_to_next_path_candidate_on_route_not_found(monkeypatch) -> None:
    probe = _probe()
    config = _config(probe)
    config["_auto_fixture_runtime"][probe["candidate_id"]]["setup_requests"] = [
        {
            "purpose": "create_disposable_qb_auto_fixture",
            "method": "POST",
            "path": "/api/v1/orders",
            "body": {"id": "qb_auto_client_1", "name": "fixture"},
            "bind_response_id_to": ["order_id"],
        },
        {
            "purpose": "create_dependency_fixture_address",
            "method": "POST",
            "path": "/api/addresses",
            "path_candidates": ["/api/addresses", "/addresses"],
            "body": {"receiver": "qb_auto_receiver", "phone": "15500000000"},
            "bind_response_id_to": ["address_id"],
        },
    ]
    config["_auto_fixture_runtime"][probe["candidate_id"]]["request_body"]["address_id"] = "qb_auto_address_1"
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if method == "POST" and url.endswith("/api/v1/orders"):
            return {"status_code": 201, "payload": {"id": "srv_order_123"}, "duration_ms": 1}
        if method == "POST" and url.endswith("/api/addresses"):
            return {"status_code": 404, "payload": {"error": "not found"}, "duration_ms": 1}
        if method == "POST" and url.endswith("/addresses"):
            return {"status_code": 201, "payload": {"id": "srv_address_456"}, "duration_ms": 1}
        return {"status_code": 200, "payload": {"id": "srv_order_123", "status": "ok"}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)

    result = _execute_write_probe(probe, _decision(probe), config, "http://sandbox", timeout=3.0)

    address_receipt = result["fixture_receipts"][1]
    assert address_receipt["accepted"] is True
    assert address_receipt["path"] == "/addresses"
    assert address_receipt["path_candidates_tried"] == ["/api/addresses", "/addresses"]
    assert any(c["url"].endswith("/api/addresses") for c in calls)
    assert any(c["url"].endswith("/addresses") for c in calls)


def test_later_setup_request_uses_runtime_id_from_earlier_setup_response(monkeypatch) -> None:
    probe = _probe()
    config = _config(probe)
    config["_auto_fixture_runtime"][probe["candidate_id"]]["request_body"]["addressId"] = "qb_auto_address_1"
    config["_auto_fixture_runtime"][probe["candidate_id"]]["path_params"]["address_id"] = "qb_auto_address_1"
    config["_auto_fixture_runtime"][probe["candidate_id"]]["setup_requests"] = [
        {
            "purpose": "create_dependency_fixture_address",
            "method": "POST",
            "path": "/api/v1/addresses",
            "body": {"receiver": "qb_auto_receiver", "phone": "15500000000"},
            "bind_response_id_to": ["address_id"],
        },
        {
            "purpose": "create_disposable_qb_auto_fixture",
            "method": "POST",
            "path": "/api/v1/orders",
            "body": {"addressId": "qb_auto_address_1", "status": "draft"},
            "bind_response_id_to": ["order_id"],
        },
    ]
    config["_auto_fixture_runtime"][probe["candidate_id"]]["cleanup_requests"] = [
        {
            "method": "POST",
            "path": "/api/v1/orders/{order_id}/cancel",
            "path_params": {"order_id": "qb_auto_client_1"},
            "purpose": "cleanup_qb_auto_fixture",
        }
    ]
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if method == "POST" and url.endswith("/api/v1/addresses"):
            return {"status_code": 201, "payload": {"id": "srv_addr_456"}, "duration_ms": 1}
        if method == "POST" and url.endswith("/api/v1/orders"):
            assert body["addressId"] == "srv_addr_456"
            return {"status_code": 201, "payload": {"id": "srv_order_123"}, "duration_ms": 1}
        if method == "POST" and url.endswith("/api/v1/orders/srv_order_123/cancel"):
            return {"status_code": 200, "payload": {"id": "srv_order_123", "status": "CANCELLED"}, "duration_ms": 1}
        return {"status_code": 200, "payload": {"id": "srv_order_123", "status": "ok"}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)

    _execute_write_probe(probe, _decision(probe), config, "http://sandbox", timeout=3.0)

    order_setup_call = next(c for c in calls if c["method"] == "POST" and c["url"].endswith("/api/v1/orders"))
    cleanup_call = next(c for c in calls if c["method"] == "POST" and c["url"].endswith("/cancel"))
    assert order_setup_call["body"]["addressId"] == "srv_addr_456"
    assert cleanup_call["url"].endswith("/api/v1/orders/srv_order_123/cancel")
