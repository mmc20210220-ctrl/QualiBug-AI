from __future__ import annotations

from ai_test_asset_center.auto_test_data_factory import build_auto_fixture_for_probe
from ai_test_asset_center.grounded_probe_executor import _headers_for_probe, _verify_observation


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
