from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.event_contract_implementation_authority import (
    prepare_formal_event_contract_authority,
)


INTERFACE_ID = "api:POST:/orders"
CONTRACT_ID = "event-contract:order-created"


def _contract(*, valid: bool) -> dict:
    row = {
        "contract_id": CONTRACT_ID,
        "source_refs": [
            {
                "source_id": "event-doc",
                "locator": "events.order-created",
                "quote_hash": "sha256:order-created",
            }
        ],
        "operation_ref": INTERFACE_ID,
        "actor_ref": "actor:admin",
        "observer_path": "/test/events",
        "events_path": "$.items",
        "event_id_field": "id",
        "event_type_field": "type",
        "correlation_field": "orderId",
        "correlation_query_parameter": "orderId",
        "correlation_source": {
            "location": "treatment_response",
            "path": "$.id",
        },
        "expected_event_type": "OrderCreated",
        "expected_min_count": 1,
        "expected_max_count": 1,
        "observation_window_ms": 3000,
        "status": "accepted",
    }
    if not valid:
        row.pop("expected_event_type")
    return row


def _model() -> dict:
    return {
        "business_behaviors": [
            {
                "behavior_id": "behavior:create-order",
                "actor_refs": ["actor:admin"],
            }
        ],
        "behavior_implementation_bindings": [
            {
                "binding_id": "binding:create-order",
                "behavior_ref": "behavior:create-order",
                "primary_api_interface_ref": INTERFACE_ID,
            }
        ],
    }


def test_corrected_contract_replaces_retained_invalid_candidate() -> None:
    invalid = _contract(valid=False)
    asset = {
        "interfaces": [
            {
                "interface_id": INTERFACE_ID,
                "method": "POST",
                "path": "/orders",
                "operation_id": "createOrder",
            }
        ],
        "event_formal_contracts": [invalid],
        "coverage_gaps": [],
    }
    model = _model()

    first_unknowns = prepare_formal_event_contract_authority(asset, model)
    assert len(first_unknowns) == 1
    assert asset["event_formal_contracts"] == []
    assert asset["event_formal_contract_candidates"] == [invalid]
    assert len(asset["coverage_gaps"]) == 1

    corrected = _contract(valid=True)
    asset["event_formal_contracts"] = [corrected]
    second_unknowns = prepare_formal_event_contract_authority(asset, model)

    assert second_unknowns == []
    assert asset["event_formal_contract_candidates"] == [corrected]
    assert len(asset["event_formal_contracts"]) == 1
    assert asset["event_formal_contracts"][0]["contract_id"] == CONTRACT_ID
    assert asset["event_formal_contracts"][0]["expected_event_type"] == "OrderCreated"
    assert asset["event_formal_contract_validation_failures"] == []
    assert asset["coverage_gaps"] == []
