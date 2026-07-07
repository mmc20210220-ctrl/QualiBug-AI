from __future__ import annotations

import json

from ai_test_asset_center.auto_test_data_factory import build_auto_fixture_for_probe
from ai_test_asset_center.grounded_probe_executor import (
    _append_query_surface_get_fallbacks,
    _headers_for_probe,
    _runtime_query_surface_fallback_response,
    _verify_observation,
    _verify_write_observation,
)


def _grounded_write_probe(risk_type: str, mutation: dict) -> dict:
    return {
        "candidate_id": "QBTEST-1",
        "risk_type": risk_type,
        "execution_policy": "disposable_sandbox_required",
        "endpoint": {"method": "POST", "path": "/api/v1/orders"},
        "probe_plan": {
            "mutation": mutation,
            "expected_status": [400, 409, 422],
        },
        "source_refs": [
            {"kind": "endpoint_contract", "file": "openapi.yaml", "section": "POST /api/v1/orders", "quote": "orders endpoint"},
            {"kind": "business_rule", "file": "prd.md", "section": "Order", "quote": "amount and quantity must obey business boundaries"},
        ],
        "grounding_basis": {"endpoint_contract_refs": 1, "supporting_requirement_refs": 1},
    }


def test_auto_fixture_materializes_resource_mutation_into_request_body() -> None:
    probe = _grounded_write_probe(
        "conservation_probe",
        {"mutation_kind": "resource_negative_value", "field_selector": "resource", "value": -1},
    )

    bundle = build_auto_fixture_for_probe(probe, config={"qualibug_auto_create_test_data": True})

    body = bundle["request_body"]
    assert body["amount"] == -1
    assert body["quantity"] == -1
    assert body["qualibug_mutation_trace"]["mutation_kind"] == "resource_negative_value"
    assert bundle["mutation_application"]["applied"] is True
    assert bundle["receipt"]["mutation_applied"] is True
    assert set(bundle["receipt"]["mutation_applied_fields"]) >= {"amount", "quantity"}


def test_auto_fixture_replaces_idempotency_placeholders_with_concrete_replay_key() -> None:
    probe = _grounded_write_probe(
        "idempotency_replay_probe",
        {"mutation_kind": "duplicate_idempotency_key", "field_selector": "idempotency", "value": "<SAME_AS_PREVIOUS_ATTEMPT>"},
    )

    bundle = build_auto_fixture_for_probe(probe, config={"qualibug_auto_create_test_data": True})

    body = bundle["request_body"]
    assert body["idempotency_key"].startswith("qb_auto_idempotency_key_")
    assert body["business_key"] == body["idempotency_key"]
    assert "<SAME_AS_PREVIOUS_ATTEMPT>" not in str(body)
    assert bundle["mutation_application"]["applied"] is True


def test_auto_fixture_materializes_server_base_path_for_setup_and_cleanup(tmp_path) -> None:
    probe = _grounded_write_probe(
        "conservation_probe",
        {"mutation_kind": "resource_negative_value", "field_selector": "resource", "value": -1},
    )
    probe["endpoint"]["path"] = "/orders/{order_id}"
    (tmp_path / "openapi.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "servers": [{"url": "https://benchmark.example.test/api/v1/ecommerce"}],
                "paths": {
                    "/orders": {
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
                    "/orders/{order_id}": {"get": {}, "delete": {}},
                },
            }
        ),
        encoding="utf-8",
    )

    bundle = build_auto_fixture_for_probe(
        probe,
        input_dir=tmp_path,
        config={"qualibug_auto_create_test_data": True},
    )

    assert bundle["setup_requests"][0]["path"] == "/api/v1/ecommerce/orders"
    assert bundle["cleanup_requests"][0]["path"] == "/api/v1/ecommerce/orders/{order_id}"


def test_phase95_auth_boundary_variants_strip_credentials_and_validate_business_data() -> None:
    probe = {
        "candidate_id": "QBAUTH-95A-0001",
        "risk_type": "anonymous_auth_boundary_probe",
        "execution_policy": "read_only_safe",
        "endpoint": {"method": "GET", "path": "/api/v1/orders"},
        "probe_plan": {
            "auth_boundary": {
                "actor": "anonymous",
                "credential_profile": "no_credentials",
                "expected_status": [401, 403, 404],
            }
        },
    }
    config = {
        "qualibug_auto_create_test_data": False,
        "default_headers": {
            "Authorization": "Bearer secret",
            "Cookie": "sid=secret",
            "X-Tenant-Id": "tenant-a",
            "X-Trace-Id": "keep-me",
        },
    }

    headers = _headers_for_probe(probe, config)
    assert "Authorization" not in headers
    assert "Cookie" not in headers
    assert "X-Tenant-Id" not in headers
    assert headers["X-Trace-Id"] == "keep-me"

    verdict = _verify_observation(probe, {"status_code": 200, "payload": {"data": [{"order_id": "o-1"}]}})
    assert verdict["verdict"] == "validated_candidate"
    assert "anonymous access boundary" in verdict["reason"]

    protected = _verify_observation(probe, {"status_code": 403, "payload": {"error": "forbidden"}})
    assert protected["verdict"] == "falsified_or_protected"


def test_readonly_ownership_scope_probe_validates_accepted_negative_list_response() -> None:
    probe = {
        "candidate_id": "QBSCOPE-LIST-1",
        "risk_type": "ownership_scope_probe",
        "execution_policy": "read_only_safe",
        "endpoint": {"method": "GET", "path": "/api/v1/orders/list?page_size=50000"},
        "source_refs": [
            {"kind": "endpoint_contract", "file": "API.md", "section": "/orders/list", "quote": "list endpoints must validate tenant and owner scope"},
            {"kind": "business_rule", "file": "BUSINESS_RULES.md", "section": "C28", "quote": "large list surfaces must be limited and scope-filtered"},
        ],
    }

    verdict = _verify_observation(
        probe,
        {
            "status_code": 200,
            "payload": {
                "ok": True,
                "observed_bug_id": "ORD-C28-028",
                "expected_should_have_rejected": True,
                "actual_behavior": "accepted_or_returned_business_data",
                "resource": {
                    "id": "srv_order_1",
                    "tenant_id": "tenant-a",
                    "owner_user_id": "foreign-owner",
                    "status": "accepted_despite_negative_probe",
                },
            },
        },
    )

    assert verdict["verdict"] == "validated_candidate"
    assert "read-only negative scope probe" in verdict["reason"]


def test_conservation_probe_validates_accepted_negative_write_payload_marker() -> None:
    probe = _grounded_write_probe(
        "conservation_probe",
        {"mutation_kind": "resource_negative_value", "field_selector": "resource", "value": -1},
    )

    verdict = _verify_write_observation(
        probe,
        [
            {
                "status_code": 200,
                "payload": {
                    "ok": True,
                    "observed_bug_id": "ORD-C08-008",
                    "expected_should_have_rejected": True,
                    "actual_behavior": "accepted_or_returned_business_data",
                    "resource": {
                        "id": "srv_order_1",
                        "status": "accepted_despite_negative_probe",
                        "resource_qty": 1,
                    },
                },
            }
        ],
        {"before": [{"status_code": 200, "payload": {"records": []}}], "after": [{"status_code": 200, "payload": {"records": []}}]},
    )

    assert verdict["verdict"] == "validated_candidate"
    assert "business payload shows" in verdict["reason"]


def test_write_observation_promotes_db_snapshot_diff_into_db_evidence() -> None:
    probe = _grounded_write_probe(
        "conservation_probe",
        {"mutation_kind": "resource_negative_value", "field_selector": "resource", "value": -1},
    )

    verdict = _verify_write_observation(
        probe,
        [
            {
                "status_code": 200,
                "payload": {
                    "ok": True,
                    "observed_bug_id": "ORD-C08-DB",
                    "expected_should_have_rejected": True,
                    "actual_behavior": "accepted_or_returned_business_data",
                    "resource": {"id": "srv_order_1", "status": "accepted_despite_negative_probe"},
                },
            }
        ],
        {
            "before": [],
            "after": [],
            "db": {
                "diffs": [{"table": "orders", "detail": "orders: 1->2 (+1 -0 ~0)", "added_rows": 1, "removed_rows": 0, "modified_rows": 0}],
                "before_snapshots": [{"table": "orders", "row_count": 1}],
                "after_snapshots": [{"table": "orders", "row_count": 2}],
            },
        },
    )

    assert verdict["db_evidence"]["business_operation"] == "POST /api/v1/orders"
    assert verdict["db_evidence"]["table"] == "orders"
    assert "1->2" in verdict["db_evidence"]["db_assertion"]


def test_query_surface_fallback_does_not_override_working_post_search(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body=None, timeout: float = 10.0) -> dict:
        calls.append((method, url))
        return {"status_code": 200, "payload": {"ok": True, "data": []}, "duration_ms": 1}

    monkeypatch.setattr("ai_test_asset_center.grounded_probe_executor._http_request", fake_http)
    responses = [{"status_code": 200, "payload": {"ok": True, "data": []}, "duration_ms": 1}]

    _append_query_surface_get_fallbacks(
        probe={"source_refs": []},
        config={},
        original_method="POST",
        original_path="/search",
        first_response=responses[0],
        responses=responses,
        headers={},
        body={},
        base_url="http://sandbox",
        timeout=1.0,
    )

    assert len(responses) == 1
    assert calls == []


def test_query_surface_fallback_tries_get_only_after_unknown_post_surface(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body=None, timeout: float = 10.0) -> dict:
        calls.append((method, url))
        return {
            "status_code": 200,
            "payload": {
                "ok": True,
                "observed_bug_id": "ORD-C30-030",
                "expected_should_have_rejected": True,
                "actual_behavior": "accepted_or_returned_business_data",
                "resource": {"id": "srv_search_1", "status": "accepted_despite_negative_probe"},
            },
            "duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.grounded_probe_executor._http_request", fake_http)
    responses = [{"status_code": 404, "payload": {"error": "unknown_runtime_surface"}, "duration_ms": 1}]

    _append_query_surface_get_fallbacks(
        probe={
            "risk_type": "ownership_scope_probe",
            "source_refs": [
                {"section": "/api/v1/orders/search?keyword=", "quote": "search must validate tenant scope"},
            ],
        },
        config={},
        original_method="POST",
        original_path="/search",
        first_response=responses[0],
        responses=responses,
        headers={},
        body={},
        base_url="http://sandbox",
        timeout=1.0,
    )

    assert calls == [("GET", "http://sandbox/api/v1/orders/search?keyword=")]
    assert responses[1]["fallback_from_method"] == "POST"
    assert responses[1]["fallback_reason"] == "post_query_surface_unknown_runtime_surface"
    verdict = _verify_write_observation({"risk_type": "ownership_scope_probe"}, responses, {"before": [], "after": []})
    assert verdict["verdict"] == "validated_candidate"
    assert "safe GET fallback" in verdict["reason"]


def test_query_surface_fallback_uses_input_api_path_for_post_search(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, str, object]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body=None, timeout: float = 10.0) -> dict:
        calls.append((method, url, body))
        return {
            "status_code": 200,
            "payload": {
                "ok": True,
                "observed_bug_id": "ORD-C30-030",
                "expected_should_have_rejected": True,
                "actual_behavior": "accepted_or_returned_business_data",
                "resource": {"id": "srv_search_1", "status": "accepted_despite_negative_probe"},
            },
            "duration_ms": 1,
        }

    (tmp_path / "API.md").write_text(
        "### 30. /api/v1/orders/search?keyword=\n"
        "C30 search must validate tenant scope.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("ai_test_asset_center.grounded_probe_executor._http_request", fake_http)
    responses = [{"status_code": 404, "payload": {"error": "unknown_runtime_surface"}, "duration_ms": 1}]

    _append_query_surface_get_fallbacks(
        probe={"risk_type": "ownership_scope_probe", "endpoint": {"capability_code": "C30"}, "source_refs": []},
        config={"input_dir": str(tmp_path)},
        original_method="POST",
        original_path="/search",
        first_response=responses[0],
        responses=responses,
        headers={},
        body={"tenant_id": "foreign"},
        base_url="http://sandbox",
        timeout=1.0,
    )

    assert calls == [("POST", "http://sandbox/api/v1/orders/search?keyword=", {"tenant_id": "foreign"})]
    assert responses[1]["fallback_from_path"] == "/search"
    assert responses[1]["fallback_reason"] == "query_surface_input_path_after_unknown_runtime_surface"
    verdict = _verify_write_observation({"risk_type": "ownership_scope_probe"}, responses, {"before": [], "after": []})
    assert verdict["verdict"] == "validated_candidate"


def test_read_query_surface_fallback_keeps_working_search_path(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body=None, timeout: float = 10.0) -> dict:
        calls.append((method, url))
        return {"status_code": 200, "payload": {"ok": True}, "duration_ms": 1}

    monkeypatch.setattr("ai_test_asset_center.grounded_probe_executor._http_request", fake_http)
    first = {"status_code": 200, "payload": {"ok": True}, "duration_ms": 1}

    response = _runtime_query_surface_fallback_response(
        probe={"source_refs": [{"section": "/api/v1/orders/search?keyword=", "quote": "scope search"}]},
        method="GET",
        original_path="/search",
        first_response=first,
        headers={},
        base_url="http://sandbox",
        timeout=1.0,
    )

    assert response is first
    assert calls == []


def test_read_query_surface_fallback_rebinds_unknown_generic_search_path(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body=None, timeout: float = 10.0) -> dict:
        calls.append((method, url))
        return {
            "status_code": 200,
            "payload": {
                "ok": True,
                "observed_bug_id": "ORD-C30-030",
                "expected_should_have_rejected": True,
                "actual_behavior": "accepted_or_returned_business_data",
                "resource": {"id": "srv_search_1", "status": "accepted_despite_negative_probe"},
            },
            "duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.grounded_probe_executor._http_request", fake_http)

    response = _runtime_query_surface_fallback_response(
        probe={
            "risk_type": "ownership_scope_probe",
            "source_refs": [{"section": "/api/v1/orders/search?keyword=", "quote": "search must validate tenant scope"}],
        },
        method="GET",
        original_path="/search",
        first_response={"status_code": 404, "payload": {"error": "unknown_runtime_surface"}, "duration_ms": 1},
        headers={},
        base_url="http://sandbox",
        timeout=1.0,
    )

    assert calls == [("GET", "http://sandbox/api/v1/orders/search?keyword=")]
    assert response["fallback_from_path"] == "/search"
    assert response["fallback_reason"] == "query_surface_unknown_runtime_surface"
    verdict = _verify_observation({"risk_type": "ownership_scope_probe", "endpoint": {"method": "GET", "path": "/search"}}, response)
    assert verdict["verdict"] == "validated_candidate"


def test_read_query_surface_fallback_uses_contract_post_search_when_declared(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body=None, timeout: float = 10.0) -> dict:
        calls.append((method, url))
        return {
            "status_code": 200,
            "payload": {
                "ok": True,
                "observed_bug_id": "ORD-C30-030",
                "expected_should_have_rejected": True,
                "actual_behavior": "accepted_or_returned_business_data",
                "resource": {"id": "srv_search_1", "status": "accepted_despite_negative_probe"},
            },
            "duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.grounded_probe_executor._http_request", fake_http)

    response = _runtime_query_surface_fallback_response(
        probe={
            "risk_type": "ownership_scope_probe",
            "source_refs": [{"kind": "endpoint_contract", "section": "POST /search", "quote": "search can be POST in this system"}],
        },
        method="GET",
        original_path="/search",
        first_response={"status_code": 404, "payload": {"error": "unknown_runtime_surface"}, "duration_ms": 1},
        headers={},
        base_url="http://sandbox",
        timeout=1.0,
    )

    assert calls == [("POST", "http://sandbox/search")]
    assert response["fallback_from_method"] == "GET"
    assert response["fallback_reason"] == "query_surface_contract_method_after_unknown_runtime_surface"
    verdict = _verify_observation({"risk_type": "ownership_scope_probe", "endpoint": {"method": "GET", "path": "/search"}}, response)
    assert verdict["verdict"] == "validated_candidate"


def test_read_query_surface_fallback_probes_post_for_full_api_search_path(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(method: str, url: str, headers: dict[str, str], body=None, timeout: float = 10.0) -> dict:
        calls.append((method, url))
        return {
            "status_code": 200,
            "payload": {
                "ok": True,
                "observed_bug_id": "ORD-C21-021",
                "expected_should_have_rejected": True,
                "actual_behavior": "accepted_or_returned_business_data",
                "resource": {"id": "srv_search_1", "status": "accepted_despite_negative_probe"},
            },
            "duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.grounded_probe_executor._http_request", fake_http)

    response = _runtime_query_surface_fallback_response(
        probe={"risk_type": "ownership_scope_probe", "source_refs": []},
        method="GET",
        original_path="/api/v1/orders/search?keyword=",
        first_response={"status_code": 404, "payload": {"error": "unknown_runtime_surface"}, "duration_ms": 1},
        headers={},
        base_url="http://sandbox",
        timeout=1.0,
    )

    assert calls == [("POST", "http://sandbox/api/v1/orders/search?keyword=")]
    assert response["fallback_from_method"] == "GET"
    assert response["fallback_reason"] == "query_surface_post_contract_probe_after_unknown_get_surface"
    verdict = _verify_observation({"risk_type": "ownership_scope_probe", "endpoint": {"method": "GET", "path": "/api/v1/orders/search?keyword="}}, response)
    assert verdict["verdict"] == "validated_candidate"
