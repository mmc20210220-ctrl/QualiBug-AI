from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.binding_identity_asset_projection import (
    finalize_binding_identity_projection,
)


BINDING_ID = "binding:create-order"
ACTION_REF = "action-surface:create-order"
OBSERVER_REF = "observer-binding:order-created"
EVENT_CONTRACT_REF = "event-contract:order-created"
PLAN_ID = "runtime-plan:create-order"


def _asset(materialized_observer_ref: str = OBSERVER_REF) -> tuple[dict, dict]:
    asset = {
        "binding_identity_graph": {
            "action_surface_bindings": [],
            "contract_field_bindings": [],
            "runtime_value_bindings": [],
            "formal_ui_surface_bindings": [],
            "observer_bindings": [
                {
                    "observer_binding_id": OBSERVER_REF,
                    "implementation_binding_ref": BINDING_ID,
                    "binding_kind": "SOURCE_EVENT_DELIVERY_OBSERVER",
                    "event_contract_ref": EVENT_CONTRACT_REF,
                }
            ],
        },
        "binding_identity_gate": {"status": "PASS", "metrics": {}},
        "binding_identity_unknowns": [],
        "behavior_implementation_bindings": [
            {
                "binding_id": BINDING_ID,
                "action_surface_bindings": [],
            }
        ],
        "runtime_plans": [
            {
                "plan_id": PLAN_ID,
                "formal_runtime_plan": True,
                "action_entry": {
                    "action_surface_binding_ref": ACTION_REF,
                },
                "request_template": {
                    "path_parameters": [],
                    "query_parameters": [],
                    "header_parameters": [],
                    "cookie_parameters": [],
                    "body_fields": [],
                    "form_fields": [],
                },
                "binding_identity_refs": {
                    "observer_binding_refs": [OBSERVER_REF]
                },
            }
        ],
        "runtime_materializations": [
            {
                "materialization_id": "materialization:create-order",
                "runtime_plan_ref": PLAN_ID,
                "formal_runtime_materialization": True,
                "request_value_bindings": [],
                "binding_identity_refs": {
                    "action_surface_binding_ref": ACTION_REF,
                    "observer_binding_refs": [materialized_observer_ref],
                },
            }
        ],
        "runtime_plan_gate": {"status": "PASS", "entry_allowed": True},
        "runtime_materialization_gate": {
            "status": "PASS",
            "entry_allowed": True,
        },
        "relationships": [],
        "coverage_gaps": [],
    }
    return asset, {}


def test_event_observer_relationships_and_metrics_are_projected() -> None:
    asset, model = _asset()

    finalize_binding_identity_projection(asset, model)

    assert asset["binding_identity_gate"]["status"] == "PASS"
    metrics = asset["binding_identity_gate"]["metrics"]
    assert metrics["formal_event_observer_binding_count"] == 1
    assert metrics["observer_identity_drift_count"] == 0
    relations = {
        (row["relation"], row["from"], row["to"])
        for row in asset["binding_identity_relationships"]
    }
    assert (
        "implementation_binding_to_observer",
        BINDING_ID,
        OBSERVER_REF,
    ) in relations
    assert (
        "observer_to_event_contract",
        OBSERVER_REF,
        EVENT_CONTRACT_REF,
    ) in relations
    assert asset["summary"]["binding_identity_formal_event_observer_count"] == 1


def test_event_observer_identity_drift_closes_binding_gate() -> None:
    asset, model = _asset(materialized_observer_ref="observer-binding:other")

    finalize_binding_identity_projection(asset, model)

    assert asset["binding_identity_gate"]["entry_allowed"] is False
    assert (
        asset["binding_identity_gate"]["status"]
        == "BLOCKED_BINDING_IDENTITY_INCOMPLETE"
    )
    assert asset["binding_identity_gate"]["metrics"][
        "observer_identity_drift_count"
    ] == 1
    assert asset["summary"]["binding_identity_observer_drift_count"] == 1
