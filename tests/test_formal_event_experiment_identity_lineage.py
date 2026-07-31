from __future__ import annotations

from ai_test_asset_center.formal_event_surface import _compile_event_protocol


def test_formal_event_protocol_preserves_durable_binding_identity() -> None:
    identity = {
        "schema_version": "qualibug.formal-event-binding-identity-bridge.v1",
        "status": "BOUND",
        "event_contract_ref": "event-contract:order-created",
        "implementation_binding_ref": "implementation:order-create",
        "action_surface_binding_ref": "action:post-orders",
        "observer_binding_ref": "observer:order-created",
        "runtime_plan_ref": "runtime-plan:order-create",
        "runtime_materialization_ref": "materialization:order-create",
        "binding_authority": "enterprise_binding_identity_graph",
        "identity_reselection_allowed": False,
        "token_overlap_is_authoritative": False,
    }
    contract = {
        "contract_id": "event-contract:order-created",
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
        "trigger_body": {"orderType": "STANDARD"},
    }
    property_spec = {
        "actor_ref": "actor:admin",
        "event_contract": contract,
        "formal_event_binding_identity": identity,
    }

    compiled = _compile_event_protocol(
        {
            "property_spec": property_spec,
            "operation_ref": "api:POST:/orders",
            "treatment_actor_ref": "actor:admin",
            "operation": {
                "method": "POST",
                "path": "/orders",
                "request_example": {"orderType": "STANDARD"},
            },
        }
    )

    assert compiled["status"] == "COMPILED"
    assertion_property = compiled["assertion"]["property"]
    assert assertion_property["formal_event_binding_identity"] == identity
    assert assertion_property["event_contract"] == contract
