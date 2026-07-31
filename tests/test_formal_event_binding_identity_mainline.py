from __future__ import annotations

from ai_test_asset_center.formal_event_binding_evidence_projection import (
    project_formal_event_binding_evidence,
)
from ai_test_asset_center.formal_event_binding_identity_bridge import (
    project_formal_event_binding_identities,
)
from ai_test_asset_center.formal_event_binding_receipt_bridge import (
    EVIDENCE_IDENTITY_KEY,
    attach_event_binding_identity_to_receipt,
)
from ai_test_asset_center.formal_event_surface import OBSERVER_ID
from ai_test_asset_center.observer_contracts_base import (
    build_observer_receipt,
    validate_observer_receipt,
)
from ai_test_asset_center.source_event_obligation_binding import (
    compile_obligations_with_source_event,
)


IMPLEMENTATION_REF = "implementation:order-create"
ACTION_REF = "action:post-orders"
OBSERVER_REF = "observer:order-created"
PLAN_REF = "runtime-plan:order-create"
MATERIALIZATION_REF = "materialization:order-create"
SCENARIO_REF = "scenario:order-create"
CONTRACT_REF = "event-contract:order-created"
INTERFACE_REF = "api:POST:/orders"
ACTOR_REF = "actor:admin"
FIELD_REF = "field:body.orderType"
VALUE_REF = "value:body.orderType"


def _identity_asset() -> dict:
    return {
        "binding_identity_graph": {
            "action_surface_bindings": [
                {
                    "action_surface_binding_id": ACTION_REF,
                    "surface_kind": "HTTP_API",
                    "interface_id": INTERFACE_REF,
                    "status": "BOUND",
                    "authoritative": True,
                    "primary": True,
                }
            ],
            "observer_bindings": [
                {
                    "observer_binding_id": OBSERVER_REF,
                    "binding_kind": "SOURCE_EVENT_DELIVERY_OBSERVER",
                    "event_contract_ref": CONTRACT_REF,
                    "implementation_binding_ref": IMPLEMENTATION_REF,
                    "interface_id": INTERFACE_REF,
                    "actor_ref": ACTOR_REF,
                    "status": "BOUND",
                    "authoritative": True,
                }
            ],
        },
        "binding_identity_relationships": [
            {
                "from": IMPLEMENTATION_REF,
                "to": ACTION_REF,
                "relation": "implementation_binding_to_action_surface",
                "status": "accepted",
            }
        ],
        "runtime_plans": [
            {
                "plan_id": PLAN_REF,
                "scenario_ref": SCENARIO_REF,
                "implementation_binding_ref": IMPLEMENTATION_REF,
                "status": "TEMPLATE_READY",
                "formal_runtime_plan": True,
                "action_entry": {
                    "action_surface_binding_ref": ACTION_REF,
                },
                "binding_identity_refs": {
                    "action_surface_binding_ref": ACTION_REF,
                    "observer_binding_refs": [OBSERVER_REF],
                    "contract_field_binding_refs": [FIELD_REF],
                    "runtime_value_binding_refs": [VALUE_REF],
                },
            }
        ],
        "runtime_materializations": [
            {
                "materialization_id": MATERIALIZATION_REF,
                "runtime_plan_ref": PLAN_REF,
                "status": "DRAFT_READY",
                "formal_runtime_materialization": True,
                "request_draft": {
                    "action_surface_binding_ref": ACTION_REF,
                },
                "binding_identity_refs": {
                    "action_surface_binding_ref": ACTION_REF,
                    "observer_binding_refs": [OBSERVER_REF],
                    "contract_field_binding_refs": [FIELD_REF],
                    "runtime_value_binding_refs": [VALUE_REF],
                },
            }
        ],
    }


def _event_contract() -> dict:
    return {
        "contract_id": CONTRACT_REF,
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


def _behavior_ir() -> dict:
    return {
        "operations": [
            {
                "id": INTERFACE_REF,
                "method": "POST",
                "path": "/orders",
                "confidence": 1.0,
                "source_refs": [{"source_id": "api", "locator": "POST /orders"}],
            }
        ],
        "actors": [
            {
                "id": ACTOR_REF,
                "confidence": 1.0,
                "source_refs": [{"source_id": "roles", "locator": "admin"}],
            }
        ],
        "invariants": [
            {
                "id": "invariant:event-order-created",
                "expression": {
                    "kind": "event_delivery_contract",
                    "operator": "must_match_declared_event_delivery",
                },
                "event_contract_id": CONTRACT_REF,
                "event_contract": _event_contract(),
                "event_actor_ref": ACTOR_REF,
                "operation_refs": [INTERFACE_REF],
                "status": "accepted",
                "confidence": 1.0,
                "source_refs": [
                    {"source_id": "event-doc", "locator": "OrderCreated"}
                ],
            }
        ],
        "relations": [
            {
                "id": "relation:order-produces-event",
                "relation_type": "produces",
                "operation_ref": INTERFACE_REF,
                "from_ref": INTERFACE_REF,
                "to_ref": "invariant:event-order-created",
                "actor_ref": ACTOR_REF,
                "status": "accepted",
                "source_refs": [
                    {"source_id": "event-doc", "locator": "OrderCreated"}
                ],
            }
        ],
    }


def test_event_identity_closes_across_graph_plan_and_materialization() -> None:
    model, receipt = project_formal_event_binding_identities(
        _behavior_ir(), _identity_asset()
    )

    assert receipt["status"] == "BOUND"
    assert receipt["bound_count"] == 1
    invariant = model["invariants"][0]
    identity = invariant["formal_event_binding_identity"]
    assert identity["implementation_binding_ref"] == IMPLEMENTATION_REF
    assert identity["action_surface_binding_ref"] == ACTION_REF
    assert identity["observer_binding_ref"] == OBSERVER_REF
    assert identity["runtime_plan_ref"] == PLAN_REF
    assert identity["runtime_materialization_ref"] == MATERIALIZATION_REF
    assert identity["contract_field_binding_refs"] == [FIELD_REF]
    assert identity["runtime_value_binding_refs"] == [VALUE_REF]


def test_event_identity_drift_blocks_formal_obligation() -> None:
    asset = _identity_asset()
    asset["runtime_materializations"][0]["binding_identity_refs"][
        "runtime_value_binding_refs"
    ] = ["value:other"]
    model, identity_receipt = project_formal_event_binding_identities(
        _behavior_ir(), asset
    )

    assert identity_receipt["status"] == "BLOCKED"
    assert model["invariants"][0]["event_binding_identity_status"] == "BLOCKED"
    compiled = compile_obligations_with_source_event(
        model,
        base_compile=lambda _model: {
            "obligations": [],
            "coverage_gaps": [],
            "by_family": {},
        },
    )
    assert compiled["source_event_obligation_receipt"]["status"] == "BLOCKED"
    assert compiled["obligations"] == []
    assert compiled["coverage_gaps"][0]["code"] == (
        "FORMAL_EVENT_MATERIALIZATION_VALUE_IDENTITY_DRIFT"
    )


def test_bound_event_obligation_carries_durable_identity() -> None:
    model, _receipt = project_formal_event_binding_identities(
        _behavior_ir(), _identity_asset()
    )
    compiled = compile_obligations_with_source_event(
        model,
        base_compile=lambda _model: {
            "obligations": [],
            "coverage_gaps": [],
            "by_family": {},
        },
    )

    assert compiled["source_event_obligation_receipt"]["status"] == "COMPILED"
    assert compiled["source_event_obligation_receipt"][
        "binding_identity_obligation_count"
    ] == 1
    obligation = compiled["obligations"][0]
    identity = obligation["property"]["formal_event_binding_identity"]
    assert identity["observer_binding_ref"] == OBSERVER_REF
    assert identity["action_surface_binding_ref"] == ACTION_REF
    assert identity["runtime_plan_ref"] == PLAN_REF
    assert identity["runtime_materialization_ref"] == MATERIALIZATION_REF


def test_event_receipt_remains_content_addressed_after_identity_attachment() -> None:
    model, _receipt = project_formal_event_binding_identities(
        _behavior_ir(), _identity_asset()
    )
    identity = model["invariants"][0]["formal_event_binding_identity"]
    original = build_observer_receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        evidence={"source_event_delivery_observation": {"coverage_complete": True}},
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    enriched = attach_event_binding_identity_to_receipt(
        original,
        {
            "assertion": {
                "property": {"formal_event_binding_identity": identity}
            }
        },
    )

    validated = validate_observer_receipt(enriched)
    assert validated == enriched
    projected = enriched["evidence"][EVIDENCE_IDENTITY_KEY]
    assert projected["observer_binding_ref"] == OBSERVER_REF
    assert projected["runtime_materialization_ref"] == MATERIALIZATION_REF
    assert "event_payload" not in projected
    assert "correlation_value" not in projected


def test_real_event_receipt_projects_identity_topology_without_new_finding() -> None:
    model, _receipt = project_formal_event_binding_identities(
        _behavior_ir(), _identity_asset()
    )
    identity = model["invariants"][0]["formal_event_binding_identity"]
    receipt = build_observer_receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        evidence={
            "source_event_delivery_observation": {"coverage_complete": True},
            EVIDENCE_IDENTITY_KEY: identity,
        },
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    result = {
        "experiment_execution": {
            "results": [
                {
                    "experiment_id": "experiment-1",
                    "observer_receipts": [receipt],
                }
            ]
        },
        "evidence_graphs": [
            {
                "graph_id": "graph-1",
                "experiment_id": "experiment-1",
                "nodes": [
                    {
                        "node_id": receipt["receipt_id"],
                        "node_type": "observer_receipt",
                        "observer_id": OBSERVER_ID,
                    }
                ],
                "edges": [],
                "coverage": {},
            }
        ],
        "execution_trace_summaries": [
            {"experiment_id": "experiment-1"}
        ],
        "findings": [],
    }

    projected = project_formal_event_binding_evidence(result)

    graph = projected["evidence_graphs"][0]
    node_ids = {row["node_id"] for row in graph["nodes"]}
    assert OBSERVER_REF in node_ids
    assert ACTION_REF in node_ids
    assert PLAN_REF in node_ids
    assert MATERIALIZATION_REF in node_ids
    edge_types = {row["edge_type"] for row in graph["edges"]}
    assert "receipt_proves_observer_binding" in edge_types
    assert "runtime_plan_to_materialization" in edge_types
    assert graph["coverage"]["formal_event_binding_identity_count"] == 1
    assert projected["formal_event_binding_evidence_receipt"]["status"] == (
        "PROJECTED"
    )
    assert projected["formal_event_binding_evidence_receipt"][
        "new_findings_created"
    ] == 0
    assert projected["findings"] == []
