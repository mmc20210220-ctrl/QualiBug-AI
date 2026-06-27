from __future__ import annotations

from typing import Any

from ai_test_asset_center import grounded_probe_executor as gpe
from ai_test_asset_center.grounded_probe_executor import ProbeDecision, _decide_probe, _execute_flow_probe, _execute_read_probe


def _grounding() -> dict[str, Any]:
    return {
        "source_refs": [
            {"kind": "endpoint_contract", "file": "openapi.yaml", "quote": "GET /orders/{order_id}?include=audit"},
            {"kind": "business_rule", "file": "prd.md", "quote": "users must not see another tenant order audit"},
        ],
        "grounding_basis": {"endpoint_contract_refs": 1, "supporting_requirement_refs": 1},
    }


def test_runtime_read_request_renders_endpoint_query_with_bound_fixture_ids(monkeypatch) -> None:
    probe = {
        "candidate_id": "QB-QUERY-READ-1",
        "risk_type": "anonymous_auth_boundary_probe",
        "execution_policy": "read_only_safe",
        "endpoint": {"method": "GET", "path": "/api/v1/orders/{order_id}", "query": {"include": "audit", "order": "{order_id}"}},
        "probe_plan": {"auth_boundary": {"actor": "anonymous"}},
        **_grounding(),
    }
    config = {
        "qualibug_auto_create_test_data": True,
        "allow_write_probes": True,
        "default_headers": {"Authorization": "Bearer sandbox-admin"},
        "_auto_fixture_runtime": {
            probe["candidate_id"]: {
                "path_params": {"order_id": "qb_auto_order_query_1"},
                "setup_requests": [
                    {"method": "POST", "path": "/api/v1/orders", "body": {"id": "qb_auto_order_query_1"}, "bind_response_id_to": ["order_id"]}
                ],
                "cleanup_requests": [],
                "receipt": {"primary_fixture_id": "qb_auto_order_query_1"},
            }
        },
    }
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if method == "POST":
            return {"status_code": 201, "payload": {"order_id": "srv_order_query_123"}, "duration_ms": 1}
        return {"status_code": 403, "payload": {"error": "forbidden"}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)

    decision = _decide_probe(probe, base_url="http://sandbox", config=config, options={"execute_readonly": True, "allow_write_sandbox": True})
    result = _execute_read_probe(probe, decision, config, "http://sandbox", timeout=3.0)

    target = next(c for c in calls if c["method"] == "GET")
    assert target["url"].endswith("/api/v1/orders/srv_order_query_123?include=audit&order=srv_order_query_123")
    assert "Authorization" not in target["headers"]
    assert result["request"]["path"].endswith("?include=audit&order=srv_order_query_123")
    assert result["request"]["query"] == {"include": "audit", "order": "{order_id}"}


def test_config_query_params_are_applied_to_main_runtime_write_request(monkeypatch) -> None:
    probe = {
        "candidate_id": "QB-QUERY-WRITE-1",
        "risk_type": "state_transition_probe",
        "execution_policy": "disposable_sandbox_required",
        "endpoint": {"method": "PATCH", "path": "/api/v1/orders/{order_id}"},
        "probe_plan": {"expected_status": [409, 422]},
        **_grounding(),
    }
    config = {
        "allow_write_probes": True,
        "disposable_sandbox": {"enabled": True, "cleanup_strategy": "qualibug_auto_fixture_cleanup"},
        "path_params": {probe["candidate_id"]: {"order_id": "srv_order_query_456"}},
        "request_bodies": {probe["candidate_id"]: {"status": "cancelled"}},
        "query_params": {probe["candidate_id"]: {"include": "audit", "order": "{order_id}"}},
    }
    decision = _decide_probe(probe, base_url="http://sandbox", config=config, options={"allow_write_sandbox": True, "approval_id": ""})

    assert decision.decision == "execute_write_sandbox"
    assert decision.request["url"].endswith("/api/v1/orders/srv_order_query_456?include=audit&order=srv_order_query_456")
    assert decision.request["path"].endswith("?include=audit&order=srv_order_query_456")


def test_flow_steps_render_step_queries_after_response_id_binding(monkeypatch) -> None:
    probe = {
        "candidate_id": "QB-QUERY-FLOW-1",
        "risk_type": "business_flow_sequence_probe",
        "execution_policy": "disposable_sandbox_required",
        "endpoint": {"method": "POST", "path": "/api/v1/orders/{order_id}/approve"},
        "probe_plan": {
            "strategy": "illegal_order_inversion_flow",
            "flow_scenario": {
                "strategy": "illegal_order_inversion_flow",
                "steps": [
                    {"action": "create", "method": "POST", "path": "/api/v1/orders"},
                    {"action": "approve", "method": "POST", "path": "/api/v1/orders/{order_id}/approve", "query": {"order": "{order_id}", "mode": "strict"}},
                ],
            },
        },
        **_grounding(),
    }
    config = {
        "qualibug_auto_create_test_data": True,
        "default_headers": {"Authorization": "Bearer sandbox-admin"},
        "_auto_fixture_runtime": {
            probe["candidate_id"]: {
                "request_body": {"order_id": "qb_auto_flow_query_1"},
                "path_params": {"order_id": "qb_auto_flow_query_1"},
                "setup_requests": [],
                "snapshots": {"after": []},
                "cleanup_requests": [],
                "receipt": {"primary_fixture_id": "qb_auto_flow_query_1"},
            }
        },
    }
    calls: list[str] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append(url)
        if url.endswith("/api/v1/orders"):
            return {"status_code": 201, "payload": {"order_id": "srv_flow_query_789"}, "duration_ms": 1}
        return {"status_code": 200, "payload": {"id": "accepted"}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)
    ep = probe["endpoint"]
    decision = ProbeDecision(probe["candidate_id"], probe["risk_type"], ep["method"], ep["path"], "disposable_sandbox_required", "execute_write_sandbox", "eligible", {})

    result = _execute_flow_probe(probe, decision, config, "http://sandbox", timeout=3.0)

    assert any(u.endswith("/api/v1/orders/srv_flow_query_789/approve?order=srv_flow_query_789&mode=strict") for u in calls)
    assert result["responses"][1]["flow_path"].endswith("?order=srv_flow_query_789&mode=strict")
