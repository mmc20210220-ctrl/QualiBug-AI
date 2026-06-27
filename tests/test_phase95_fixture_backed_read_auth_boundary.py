from __future__ import annotations

import json
from typing import Any

from ai_test_asset_center import grounded_probe_executor as gpe
from ai_test_asset_center.auto_test_data_factory import build_auto_fixture_for_probe
from ai_test_asset_center.grounded_probe_executor import ProbeDecision, _decide_probe, _execute_read_probe


def _read_auth_probe() -> dict[str, Any]:
    return {
        "candidate_id": "QBAUTH-READ-1",
        "risk_type": "anonymous_auth_boundary_probe",
        "execution_policy": "read_only_safe",
        "endpoint": {"method": "GET", "path": "/api/v1/orders/{order_id}"},
        "probe_plan": {
            "auth_boundary": {"actor": "anonymous", "credential_profile": "no_credentials", "expected_status": [401, 403, 404]},
            "negative_headers": ["Authorization", "Cookie", "X-Tenant-Id"],
        },
        "source_refs": [
            {"kind": "endpoint_contract", "file": "openapi.yaml", "quote": "GET /orders/{order_id}"},
            {"kind": "business_rule", "file": "prd.md", "quote": "anonymous users must not read private orders"},
        ],
        "grounding_basis": {"endpoint_contract_refs": 1, "supporting_requirement_refs": 1},
    }


def test_auto_fixture_plans_setup_for_fixture_backed_read_auth_boundary(tmp_path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "openapi.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {
                    "/api/v1/orders": {
                        "post": {
                            "requestBody": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"name": {"type": "string"}, "amount": {"type": "number"}},
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "/api/v1/orders/{order_id}": {"get": {}, "delete": {}},
                },
            }
        ),
        encoding="utf-8",
    )

    bundle = build_auto_fixture_for_probe(_read_auth_probe(), input_dir=input_dir, config={"qualibug_auto_create_test_data": True})

    assert bundle["receipt"]["fixture_backed_read_probe"] is True
    assert bundle["setup_requests"][0]["method"] == "POST"
    assert bundle["setup_requests"][0]["path"] == "/api/v1/orders"
    assert bundle["setup_requests"][0]["bind_response_id_to"] == ["order_id"]
    assert bundle["cleanup_requests"][0]["method"] == "DELETE"
    assert bundle["path_params"]["order_id"].startswith("qb_auto_qbauth_read_1_")


def test_decide_blocks_fixture_backed_read_without_write_sandbox_approval() -> None:
    probe = _read_auth_probe()
    config = {
        "qualibug_auto_create_test_data": True,
        "default_headers": {"Authorization": "Bearer sandbox-admin", "X-Tenant-Id": "tenant-a"},
        "_auto_fixture_runtime": {
            probe["candidate_id"]: {
                "path_params": {"order_id": "qb_auto_client_1"},
                "setup_requests": [{"method": "POST", "path": "/api/v1/orders", "body": {"id": "qb_auto_client_1"}, "bind_response_id_to": ["order_id"]}],
            }
        },
    }

    decision = _decide_probe(
        probe,
        base_url="http://sandbox",
        config=config,
        options={"execute_readonly": True, "allow_write_sandbox": False, "approval_id": ""},
    )

    assert decision.decision == "blocked"
    assert decision.reason == "fixture_backed_read_probe_requires_test_environment_write_execution_enabled"


def test_fixture_backed_read_auth_boundary_uses_control_setup_then_negative_get(monkeypatch) -> None:
    probe = _read_auth_probe()
    config = {
        "qualibug_auto_create_test_data": True,
        "allow_write_probes": True,
        "disposable_sandbox": {"enabled": True, "cleanup_strategy": "qualibug_auto_fixture_cleanup"},
        "default_headers": {"Authorization": "Bearer sandbox-admin", "X-Tenant-Id": "tenant-a", "X-Trace-Id": "keep"},
        "_auto_fixture_runtime": {
            probe["candidate_id"]: {
                "path_params": {"order_id": "qb_auto_client_1"},
                "setup_requests": [
                    {
                        "purpose": "create_disposable_qb_auto_fixture",
                        "method": "POST",
                        "path": "/api/v1/orders",
                        "body": {"id": "qb_auto_client_1", "name": "fixture"},
                        "bind_response_id_to": ["order_id"],
                    }
                ],
                "cleanup_requests": [
                    {"method": "DELETE", "path": "/api/v1/orders/{order_id}", "path_params": {"order_id": "qb_auto_client_1"}}
                ],
                "receipt": {"primary_fixture_id": "qb_auto_client_1"},
            }
        },
    }
    decision = ProbeDecision(
        candidate_id=probe["candidate_id"],
        risk_type=probe["risk_type"],
        method="GET",
        path="/api/v1/orders/{order_id}",
        execution_policy="read_only_safe",
        decision="execute_readonly",
        reason="eligible_read_only_probe_with_fixture_setup:test_environment_write_execution_approved",
        request={"method": "GET", "path": "/api/v1/orders/qb_auto_client_1", "url": "http://sandbox/api/v1/orders/qb_auto_client_1", "runtime_fixture_setup_required": True},
    )
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if method == "POST":
            return {"status_code": 201, "payload": {"id": "srv_order_private_1"}, "duration_ms": 1}
        if method == "GET":
            return {"status_code": 200, "payload": {"order_id": "srv_order_private_1", "owner": "tenant-a"}, "duration_ms": 1}
        return {"status_code": 204, "payload": {}, "duration_ms": 1}

    monkeypatch.setattr(gpe, "_http_request", fake_http)

    result = _execute_read_probe(probe, decision, config, "http://sandbox", timeout=3.0)

    setup_call = next(c for c in calls if c["method"] == "POST")
    target_call = next(c for c in calls if c["method"] == "GET")
    cleanup_call = next(c for c in calls if c["method"] == "DELETE")
    assert setup_call["headers"]["Authorization"] == "Bearer sandbox-admin"
    assert cleanup_call["headers"]["Authorization"] == "Bearer sandbox-admin"
    assert target_call["url"].endswith("/api/v1/orders/srv_order_private_1")
    assert "Authorization" not in target_call["headers"]
    assert "X-Tenant-Id" not in target_call["headers"]
    assert target_call["headers"]["X-Trace-Id"] == "keep"
    assert result["fixture_receipts"][0]["runtime_binding"]["response_id"] == "srv_order_private_1"
    assert result["verification"]["verdict"] == "validated_candidate"
