from __future__ import annotations

from typing import Any

from ai_test_asset_center import grounded_probe_executor as gpe
from ai_test_asset_center.grounded_probe_executor import ProbeDecision, _execute_write_probe


def _probe() -> dict[str, Any]:
    return {
        "candidate_id": "QB-TARGET-BODY-1",
        "risk_type": "state_transition_probe",
        "execution_policy": "disposable_sandbox_required",
        "endpoint": {"method": "PATCH", "path": "/api/v1/orders/{order_id}"},
        "probe_plan": {"expected_status": [400, 403, 409, 422]},
        "source_refs": [
            {"kind": "endpoint_contract", "file": "openapi.yaml", "quote": "PATCH /orders/{order_id}"},
            {"kind": "business_rule", "file": "prd.md", "quote": "cancelled orders cannot be resumed"},
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
        request={"method": ep["method"], "path": ep["path"]},
    )


def test_main_write_request_body_binds_runtime_fixture_ids_from_customer_override(monkeypatch) -> None:
    probe = _probe()
    config = {
        "qualibug_auto_create_test_data": True,
        "default_headers": {"Authorization": "Bearer sandbox-admin"},
        "request_bodies": {
            probe["candidate_id"]: {
                "order_id": "{order_id}",
                "order_ref": "qb_auto_target_body_1",
                "status": "cancelled",
            }
        },
        "_auto_fixture_runtime": {
            probe["candidate_id"]: {
                "path_params": {"order_id": "qb_auto_target_body_1"},
                "setup_requests": [
                    {
                        "purpose": "create_order",
                        "method": "POST",
                        "path": "/api/v1/orders",
                        "body": {"id": "qb_auto_target_body_1"},
                        "bind_response_id_to": ["order_id"],
                    }
                ],
                "snapshots": {"after": []},
                "cleanup_requests": [],
                "receipt": {"primary_fixture_id": "qb_auto_target_body_1"},
            }
        },
    }
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "body": body, "headers": dict(headers)})
        if method == "POST":
            return {"status_code": 201, "payload": {"order_id": "srv_target_body_123"}, "duration_ms": 1}
        return {"status_code": 409, "payload": {"error": "invalid transition"}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)

    result = _execute_write_probe(probe, _decision(probe), config, "http://sandbox", timeout=3.0)

    target_call = next(c for c in calls if c["method"] == "PATCH")
    assert target_call["url"].endswith("/api/v1/orders/srv_target_body_123")
    assert target_call["body"]["order_id"] == "srv_target_body_123"
    assert target_call["body"]["order_ref"] == "srv_target_body_123"
    assert target_call["body"]["status"] == "cancelled"
    assert result["request"]["body"]["order_id"] == "srv_target_body_123"
    assert result["request"]["body"]["order_ref"] == "srv_target_body_123"
    assert result["request"]["body_runtime_binding"]["bound"] is True
    assert result["request"]["body_runtime_binding"]["source"] == "runtime_target_request_body"
