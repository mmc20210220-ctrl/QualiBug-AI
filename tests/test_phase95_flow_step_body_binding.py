from __future__ import annotations

from typing import Any

from ai_test_asset_center import grounded_probe_executor as gpe
from ai_test_asset_center.grounded_probe_executor import ProbeDecision, _execute_flow_probe


def _grounding() -> dict[str, Any]:
    return {
        "source_refs": [
            {"kind": "endpoint_contract", "file": "openapi.yaml", "quote": "POST /orders then POST /orders/{order_id}/approve"},
            {"kind": "business_rule", "file": "prd.md", "quote": "approval requests must reference the created order id"},
        ],
        "grounding_basis": {"endpoint_contract_refs": 1, "supporting_requirement_refs": 1},
    }


def _probe() -> dict[str, Any]:
    return {
        "candidate_id": "QB-FLOW-BODY-1",
        "risk_type": "business_flow_sequence_probe",
        "execution_policy": "disposable_sandbox_required",
        "endpoint": {"method": "POST", "path": "/api/v1/orders/{order_id}/approve"},
        "probe_plan": {
            "strategy": "illegal_order_inversion_flow",
            "flow_scenario": {
                "strategy": "illegal_order_inversion_flow",
                "steps": [
                    {
                        "action": "create_draft_order",
                        "method": "POST",
                        "path": "/api/v1/orders",
                        "body": {"client_order_id": "qb_auto_flow_body_1", "status": "draft"},
                    },
                    {
                        "action": "approve_before_required_review",
                        "method": "POST",
                        "path": "/api/v1/orders/{order_id}/approve",
                        "body": {"order_id": "{order_id}", "order_ref": "qb_auto_flow_body_1", "decision": "approve"},
                    },
                ],
            },
        },
        **_grounding(),
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
        reason="eligible_multi_step_flow_probe",
        request={"method": ep["method"], "path": ep["path"]},
    )


def _config(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "qualibug_auto_create_test_data": True,
        "default_headers": {"Authorization": "Bearer sandbox-admin"},
        "_auto_fixture_runtime": {
            probe["candidate_id"]: {
                "request_body": {"order_id": "qb_auto_flow_body_1", "decision": "approve"},
                "path_params": {"order_id": "qb_auto_flow_body_1"},
                "setup_requests": [],
                "snapshots": {"after": []},
                "cleanup_requests": [],
                "receipt": {"primary_fixture_id": "qb_auto_flow_body_1"},
            }
        },
    }


def test_flow_steps_use_step_specific_bodies_and_bind_runtime_ids(monkeypatch) -> None:
    probe = _probe()
    config = _config(probe)
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "body": body, "headers": dict(headers)})
        if url.endswith("/api/v1/orders"):
            return {"status_code": 201, "payload": {"order_id": "srv_flow_body_123"}, "duration_ms": 1}
        if url.endswith("/api/v1/orders/srv_flow_body_123/approve"):
            return {"status_code": 200, "payload": {"id": "approval_side_effect_9"}, "duration_ms": 1}
        return {"status_code": 404, "payload": {"error": "unexpected"}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)

    result = _execute_flow_probe(probe, _decision(probe), config, "http://sandbox", timeout=3.0)

    create_call = calls[0]
    approve_call = calls[1]
    assert create_call["body"] == {"client_order_id": "qb_auto_flow_body_1", "status": "draft"}
    assert approve_call["url"].endswith("/api/v1/orders/srv_flow_body_123/approve")
    assert approve_call["body"]["order_id"] == "srv_flow_body_123"
    assert approve_call["body"]["order_ref"] == "srv_flow_body_123"
    assert approve_call["body"]["decision"] == "approve"
    assert result["responses"][1]["request_body_runtime_binding"]["bound"] is True
    assert result["responses"][1]["request_body_runtime_binding"]["source"] == "step.body"


def test_flow_steps_fall_back_to_shared_body_when_step_body_is_absent(monkeypatch) -> None:
    probe = _probe()
    probe["probe_plan"]["flow_scenario"]["steps"][1].pop("body")
    config = _config(probe)
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "body": body})
        if url.endswith("/api/v1/orders"):
            return {"status_code": 201, "payload": {"order_id": "srv_flow_shared_456"}, "duration_ms": 1}
        return {"status_code": 200, "payload": {"id": "ok"}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)

    result = _execute_flow_probe(probe, _decision(probe), config, "http://sandbox", timeout=3.0)

    approve_call = calls[1]
    assert approve_call["body"]["order_id"] == "srv_flow_shared_456"
    assert approve_call["body"]["decision"] == "approve"
    assert result["responses"][1]["request_body_runtime_binding"]["source"] == "shared_probe_body"
