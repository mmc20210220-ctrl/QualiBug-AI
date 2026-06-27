from __future__ import annotations

from typing import Any

from ai_test_asset_center import grounded_probe_executor as gpe
from ai_test_asset_center.grounded_probe_executor import ProbeDecision, _execute_write_probe


def _probe() -> dict[str, Any]:
    return {
        "candidate_id": "QB-CHAIN-FIXTURE-1",
        "risk_type": "state_transition_probe",
        "execution_policy": "disposable_sandbox_required",
        "endpoint": {"method": "PATCH", "path": "/api/v1/orders/{order_id}/lines/{line_id}"},
        "probe_plan": {"expected_status": [400, 403, 409, 422]},
        "source_refs": [
            {"kind": "endpoint_contract", "file": "openapi.yaml", "quote": "PATCH /orders/{order_id}/lines/{line_id}"},
            {"kind": "business_rule", "file": "prd.md", "quote": "line items cannot be mutated after submit"},
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
        request={"method": ep["method"], "path": ep["path"], "url": "http://sandbox/api/v1/orders/qb_auto_order_1/lines/qb_auto_line_1"},
    )


def _config(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "qualibug_auto_create_test_data": True,
        "default_headers": {"Authorization": "Bearer sandbox-admin"},
        "_auto_fixture_runtime": {
            probe["candidate_id"]: {
                "request_body": {"order_id": "qb_auto_order_1", "line_id": "qb_auto_line_1", "status": "cancelled"},
                "path_params": {"order_id": "qb_auto_order_1", "line_id": "qb_auto_line_1"},
                "setup_requests": [
                    {
                        "purpose": "create_order",
                        "method": "POST",
                        "path": "/api/v1/orders",
                        "body": {"id": "qb_auto_order_1"},
                        "bind_response_id_to": ["order_id"],
                    },
                    {
                        "purpose": "create_line_item_under_order",
                        "method": "POST",
                        "path": "/api/v1/orders/{order_id}/lines",
                        "path_params": {"order_id": "qb_auto_order_1"},
                        "body": {"id": "qb_auto_line_1"},
                        "bind_response_id_to": ["line_id"],
                    },
                ],
                "snapshots": {
                    "after": [
                        {
                            "method": "GET",
                            "path": "/api/v1/orders/{order_id}/lines/{line_id}",
                            "path_params": {"order_id": "qb_auto_order_1", "line_id": "qb_auto_line_1"},
                            "query": {"include": "audit", "line": "{line_id}"},
                        }
                    ]
                },
                "cleanup_requests": [
                    {
                        "purpose": "cleanup_line_item",
                        "method": "DELETE",
                        "path": "/api/v1/orders/{order_id}/lines/{line_id}",
                        "path_params": {"order_id": "qb_auto_order_1", "line_id": "qb_auto_line_1"},
                        "query": {"hard": "true", "line": "{line_id}"},
                    }
                ],
                "receipt": {"primary_fixture_id": "qb_auto_order_1"},
            }
        },
    }


def test_chained_setup_uses_prior_response_ids_for_later_fixture_paths_snapshots_and_cleanup(monkeypatch) -> None:
    probe = _probe()
    config = _config(probe)
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "body": body, "headers": dict(headers)})
        if method == "POST" and url.endswith("/api/v1/orders"):
            return {"status_code": 201, "payload": {"order": {"id": "srv_order_chain_123"}}, "duration_ms": 1}
        if method == "POST" and url.endswith("/api/v1/orders/srv_order_chain_123/lines"):
            return {"status_code": 201, "payload": {"line": {"id": "srv_line_chain_456"}}, "duration_ms": 1}
        return {"status_code": 200, "payload": {"id": "ok"}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)

    result = _execute_write_probe(probe, _decision(probe), config, "http://sandbox", timeout=3.0)

    urls = [c["url"] for c in calls]
    assert "http://sandbox/api/v1/orders/srv_order_chain_123/lines" in urls
    assert any(u.endswith("/api/v1/orders/srv_order_chain_123/lines/srv_line_chain_456") for u in urls)
    assert any(
        u.endswith("/api/v1/orders/srv_order_chain_123/lines/srv_line_chain_456?include=audit&line=srv_line_chain_456")
        for u in urls
    )
    assert any(
        u.endswith("/api/v1/orders/srv_order_chain_123/lines/srv_line_chain_456?hard=true&line=srv_line_chain_456")
        for u in urls
    )
    assert result["request"]["url"].endswith("/api/v1/orders/srv_order_chain_123/lines/srv_line_chain_456")
    assert result["fixture_receipts"][1]["path"] == "/api/v1/orders/srv_order_chain_123/lines"
    assert result["cleanup_receipts"][0]["path"].endswith("?hard=true&line=srv_line_chain_456")
