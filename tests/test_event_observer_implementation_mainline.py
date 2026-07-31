from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.event_observer_implementation_projection import (
    project_formal_event_observers,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.event_observer_runtime_projection import (
    project_event_observers_into_materializations,
    project_event_observers_into_runtime_plans,
)


INTERFACE_ID = "api:POST:/orders"
BEHAVIOR_ID = "behavior:create-order"
BINDING_ID = "binding:create-order"
ACTOR_ID = "actor:admin"
CONTRACT_ID = "event-contract:order-created"


def _contract(actor_ref: str = ACTOR_ID) -> dict:
    return {
        "schema_version": "qualibug.formal-event-contract.v1",
        "contract_id": CONTRACT_ID,
        "status": "accepted",
        "source_refs": [
            {
                "source_id": "event-doc",
                "locator": "event-contracts.order-created",
                "kind": "formal_event_contract",
            }
        ],
        "operation_ref": INTERFACE_ID,
        "actor_ref": actor_ref,
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
    }


def _behavior(actor_ref: str = ACTOR_ID) -> dict:
    return {
        "behavior_id": BEHAVIOR_ID,
        "status": "CONFIRMED",
        "actor_refs": [actor_ref],
        "preconditions": [],
        "state_effects": [],
        "data_effects": [],
    }


def _binding() -> dict:
    return {
        "binding_id": BINDING_ID,
        "behavior_ref": BEHAVIOR_ID,
        "primary_api_interface_ref": INTERFACE_ID,
        "authoritative_api_interface_count": 1,
        "api_operation_bindings": [
            {
                "binding_id": "api-binding:create-order",
                "interface_id": INTERFACE_ID,
                "method": "POST",
                "path": "/orders",
                "operation_id": "createOrder",
                "status": "BOUND",
                "authoritative": True,
            }
        ],
        "condition_observer_bindings": [],
        "effect_observer_bindings": [],
        "response_observer_bindings": [],
        "status": "PARTIAL",
        "scenario_planning_ready": False,
    }


def _asset(contract: dict | None = None) -> dict:
    return {
        "interfaces": [
            {
                "interface_id": INTERFACE_ID,
                "method": "POST",
                "path": "/orders",
                "operation_id": "createOrder",
            }
        ],
        "actors": [
            {
                "actor_id": ACTOR_ID,
                "name": "管理员",
                "credential_ref": "credential:admin",
                "runtime_bound": True,
            },
            {
                "actor_id": "actor:auditor",
                "name": "审计员",
                "credential_ref": "credential:auditor",
                "runtime_bound": True,
            },
        ],
        "event_formal_contracts": [contract or _contract()],
    }


def test_formal_event_contract_closes_effect_observer_gap() -> None:
    unknowns = [
        {
            "unknown_id": "unknown:effect",
            "kind": "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED",
            "reason_code": "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED",
            "behavior_ref": BEHAVIOR_ID,
            "blocks_scenario_planning": True,
        }
    ]

    bindings, projected_unknowns, _conflicts, gate = project_formal_event_observers(
        _asset(),
        [_behavior()],
        [_binding()],
        unknowns,
        [],
        {"status": "PARTIAL_IMPLEMENTATION_BINDING", "metrics": {}},
    )

    binding = bindings[0]
    assert binding["scenario_planning_ready"] is True
    assert binding["status"] == "BOUND"
    assert gate["status"] == "PASS"
    assert gate["entry_allowed"] is True
    assert gate["metrics"]["formal_event_observer_binding_count"] == 1
    assert not any(
        row["kind"] == "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED"
        for row in projected_unknowns
    )

    slot = binding["effect_observer_bindings"][0]
    observer = slot["bindings"][0]
    assert slot["status"] == "BOUND"
    assert observer["binding_kind"] == "SOURCE_EVENT_DELIVERY_OBSERVER"
    assert observer["observer_id"] == "source_event_delivery_reader"
    assert observer["adapter"] == "event_observer_http"
    assert observer["event_contract_ref"] == CONTRACT_ID
    assert observer["interface_id"] == INTERFACE_ID
    assert observer["actor_ref"] == ACTOR_ID
    assert observer["automatic_topic_inference_allowed"] is False
    assert observer["automatic_broker_selection_allowed"] is False
    assert binding["primary_api_interface_ref"] == INTERFACE_ID


def test_event_contract_for_other_actor_does_not_block_behavior() -> None:
    asset = _asset(_contract(actor_ref="actor:auditor"))
    unknowns = [
        {
            "unknown_id": "unknown:effect",
            "kind": "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED",
            "reason_code": "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED",
            "behavior_ref": BEHAVIOR_ID,
            "blocks_scenario_planning": True,
        }
    ]

    bindings, projected_unknowns, _conflicts, gate = project_formal_event_observers(
        asset,
        [_behavior()],
        [_binding()],
        unknowns,
        [],
        {"status": "PARTIAL_IMPLEMENTATION_BINDING", "metrics": {}},
    )

    assert bindings[0]["formal_event_observer_bindings"] == []
    assert bindings[0]["scenario_planning_ready"] is False
    assert gate["status"] == "PARTIAL_IMPLEMENTATION_BINDING"
    assert any(
        row["kind"] == "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED"
        for row in projected_unknowns
    )
    assert not any(
        row["kind"].startswith("IMPLEMENTATION_EVENT_OBSERVER")
        for row in projected_unknowns
    )


def _event_observer_candidate() -> dict:
    return {
        "observer_binding_id": "observer-binding:event",
        "binding_kind": "SOURCE_EVENT_DELIVERY_OBSERVER",
        "observer_id": "source_event_delivery_reader",
        "surface": "event_stream",
        "adapter": "event_observer_http",
        "event_contract_ref": CONTRACT_ID,
        "interface_id": INTERFACE_ID,
        "actor_ref": ACTOR_ID,
        "observer_path": "/test/events",
        "expected_event_type": "OrderCreated",
        "expected_min_count": 1,
        "expected_max_count": 1,
        "observation_window_ms": 3000,
        "event_contract": _contract(),
        "status": "BOUND",
        "authoritative": True,
    }


def _runtime_asset(extra_unknown: bool = False) -> tuple[dict, dict]:
    contract = {
        "contract_id": "execution-contract:create-order",
        "status": "REQUIREMENTS_READY",
        "oracle_plan": {
            "condition_observers": [],
            "effect_observers": [
                {
                    "slot_ref": "event-slot:create-order",
                    "purpose": "EFFECT_OBSERVER",
                    "status": "BOUND",
                    "bindings": [_event_observer_candidate()],
                }
            ],
            "response_observers": [],
        },
    }
    plan = {
        "plan_id": "runtime-plan:create-order",
        "execution_contract_ref": contract["contract_id"],
        "status": "INCOMPLETE",
        "formal_runtime_plan": False,
        "oracle_query_templates": {
            "templates": [],
            "oracle_templates_compiled": False,
        },
        "snapshot_template": {
            "after_snapshot_required": False,
            "after_oracle_template_refs": [],
        },
        "binding_identity_refs": {},
        "unresolved_runtime_plan_semantics": [
            "RUNTIME_PLAN_ORACLE_TEMPLATE_UNRESOLVED"
        ],
    }
    unknowns = [
        {
            "unknown_id": "runtime-unknown:oracle",
            "runtime_plan_ref": plan["plan_id"],
            "contract_ref": contract["contract_id"],
            "reason_code": "RUNTIME_PLAN_ORACLE_TEMPLATE_UNRESOLVED",
            "blocks_runtime_plan": True,
        }
    ]
    if extra_unknown:
        unknowns.append(
            {
                "unknown_id": "runtime-unknown:credential",
                "runtime_plan_ref": plan["plan_id"],
                "contract_ref": contract["contract_id"],
                "reason_code": "RUNTIME_PLAN_CREDENTIAL_REF_AMBIGUOUS",
                "blocks_runtime_plan": True,
            }
        )
    asset = {
        "scenario_execution_contracts": [contract],
        "runtime_plans": [plan],
        "runtime_plan_unknowns": unknowns,
        "runtime_plan_relationships": [],
        "runtime_plan_gate": {
            "status": "BLOCKED_RUNTIME_PLAN_INCOMPLETE",
            "entry_allowed": False,
            "runtime_plan_ready": False,
            "metrics": {},
        },
    }
    return asset, {}


def test_event_oracle_template_resolves_only_generic_oracle_gap() -> None:
    asset, model = _runtime_asset()

    project_event_observers_into_runtime_plans(asset, model)

    plan = asset["runtime_plans"][0]
    template = plan["oracle_query_templates"]["templates"][0]
    assert template["template_kind"] == "SOURCE_EVENT_DELIVERY_OBSERVATION"
    assert template["observer_binding_ref"] == "observer-binding:event"
    assert template["network_call_compiled"] is False
    assert plan["status"] == "TEMPLATE_READY"
    assert plan["formal_runtime_plan"] is True
    assert asset["runtime_plan_unknowns"] == []
    assert asset["runtime_plan_gate"]["status"] == "PASS"
    assert asset["runtime_plan_gate"]["entry_allowed"] is True


def test_event_projection_does_not_hide_unrelated_runtime_gap() -> None:
    asset, model = _runtime_asset(extra_unknown=True)

    project_event_observers_into_runtime_plans(asset, model)

    plan = asset["runtime_plans"][0]
    assert plan["status"] == "INCOMPLETE"
    assert plan["formal_runtime_plan"] is False
    assert asset["runtime_plan_gate"]["entry_allowed"] is False
    assert [
        row["reason_code"] for row in asset["runtime_plan_unknowns"]
    ] == ["RUNTIME_PLAN_CREDENTIAL_REF_AMBIGUOUS"]


def test_event_observer_identity_reaches_materialization_draft() -> None:
    asset, model = _runtime_asset()
    project_event_observers_into_runtime_plans(asset, model)
    asset["runtime_materializations"] = [
        {
            "materialization_id": "materialization:create-order",
            "runtime_plan_ref": "runtime-plan:create-order",
            "status": "DRAFT_READY",
            "formal_runtime_materialization": True,
            "assertion_drafts": [],
            "binding_identity_refs": {},
        }
    ]
    asset["runtime_materialization_gate"] = {
        "status": "PASS",
        "entry_allowed": True,
        "runtime_materialization_ready": True,
        "metrics": {},
    }

    project_event_observers_into_materializations(asset, model)

    materialization = asset["runtime_materializations"][0]
    draft = materialization["assertion_drafts"][0]
    assert draft["draft_kind"] == "SOURCE_EVENT_DELIVERY_ASSERTION_DRAFT"
    assert draft["observer_binding_ref"] == "observer-binding:event"
    assert draft["assertion_executable"] is False
    assert draft["network_call_allowed"] is False
    assert materialization["binding_identity_refs"]["observer_binding_refs"] == [
        "observer-binding:event"
    ]
