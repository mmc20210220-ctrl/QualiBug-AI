from __future__ import annotations

from ai_test_asset_center.behavior_ir import (
    _content_addressed_id,
    _fact_node,
    _source_ref,
    empty_behavior_ir,
)
from ai_test_asset_center.obligation_compiler_base import related_operations
from ai_test_asset_center.semantic_operation_binding import (
    bind_accepted_semantic_operations,
)


def _operation(
    operation_ref: str,
    *,
    operation_id: str,
    interface_ref: str,
    path: str,
) -> dict:
    return _fact_node(
        node_id=operation_ref,
        typed_fields={
            "operation_id": operation_id,
            "method": "POST",
            "path": path,
            "source_operation_refs": [interface_ref, operation_id],
            "request_schema": {"type": "object", "properties": {}},
            "request_example": {},
            "response_schema": {},
            "parameters": [],
            "field_dictionary": [],
            "security": [],
            "summary": "",
            "description": "",
            "tags": [],
            "side_effect_class": "write",
            "read_write": "write",
            "entity_refs": [],
            "affected_fields": [],
            "examples": [],
        },
        source_refs=[_source_ref("api_spec", locator=f"POST {path}")],
        confidence=0.9,
        derivation="explicit",
    )


def _invariant() -> dict:
    return _fact_node(
        node_id="bir_inv_order_submit",
        typed_fields={
            "description": "Submitted orders must satisfy the declared business rule",
            "expression": {
                "kind": "business_rule",
                "operator": "must_hold",
                "operands": [],
                "raw": "Submitted orders must satisfy the declared business rule",
            },
            "operation_refs": [],
            "source_rule_refs": ["rule:order-submit"],
        },
        source_refs=[_source_ref("prd", locator="rule:order-submit")],
        confidence=0.8,
        derivation="explicit",
    )


def _behavior_ir(*operations: dict) -> dict:
    model = empty_behavior_ir(project_id="semantic-binding-test")
    model["operations"] = list(operations)
    model["invariants"] = [_invariant()]
    model["model_id"] = _content_addressed_id(model)
    return model


def _asset(interface_ref: str, *, status: str = "accepted") -> dict:
    return {
        "relationships": [{
            "edge_id": "edge:order-submit",
            "from": "rule:order-submit",
            "to": interface_ref,
            "relation": "rule_to_interface",
            "status": status,
            "confidence": 0.92,
            "derivation": "agent_semantic_mapping",
            "evidence_gate": "behavior_ir_ids_and_runtime_oracle_required",
            "source_id": "agent_semantic_linker",
            "evidence": {
                "rule_source_id": "prd",
                "interface_source_id": "api_spec",
                "supporting_fact_refs": ["fact:order-submit"],
                "runtime_verification_required": True,
            },
        }],
    }


def test_exact_semantic_identity_creates_formal_operation_join() -> None:
    model = _behavior_ir(_operation(
        "bir_op_submit_order",
        operation_id="submitOrder",
        interface_ref="interface:submit-order",
        path="/orders/{id}/submit",
    ))

    bound, receipt = bind_accepted_semantic_operations(
        model,
        _asset("interface:submit-order"),
    )

    invariant = bound["invariants"][0]
    assert invariant["operation_refs"] == ["bir_op_submit_order"]
    assert invariant["semantic_operation_binding_refs"] == ["edge:order-submit"]
    assert invariant["operation_binding_authority"] == (
        "exact_source_or_accepted_agent_semantic_identity"
    )
    assert receipt["status"] == "BOUND"
    assert receipt["bound_invariant_count"] == 1
    assert receipt["added_relation_count"] == 1

    operations = related_operations(
        bound,
        node_ref="bir_inv_order_submit",
        relation_types={"observes", "conserves", "transitions"},
    )
    assert [row["id"] for row in operations] == ["bir_op_submit_order"]


def test_unresolved_interface_identity_fails_closed_and_stays_visible() -> None:
    model = _behavior_ir(_operation(
        "bir_op_submit_order",
        operation_id="submitOrder",
        interface_ref="interface:submit-order",
        path="/orders/{id}/submit",
    ))

    bound, receipt = bind_accepted_semantic_operations(
        model,
        _asset("interface:not-in-ir"),
    )

    assert bound["invariants"][0]["operation_refs"] == []
    assert receipt["status"] == "BOUND_WITH_GAPS"
    assert receipt["unresolved_interface_refs"] == ["interface:not-in-ir"]
    assert receipt["added_relation_count"] == 0


def test_ambiguous_interface_identity_never_selects_one_operation() -> None:
    model = _behavior_ir(
        _operation(
            "bir_op_submit_order_v1",
            operation_id="submitOrderV1",
            interface_ref="interface:submit-order",
            path="/v1/orders/{id}/submit",
        ),
        _operation(
            "bir_op_submit_order_v2",
            operation_id="submitOrderV2",
            interface_ref="interface:submit-order",
            path="/v2/orders/{id}/submit",
        ),
    )

    bound, receipt = bind_accepted_semantic_operations(
        model,
        _asset("interface:submit-order"),
    )

    assert bound["invariants"][0]["operation_refs"] == []
    assert receipt["status"] == "BOUND_WITH_GAPS"
    assert receipt["ambiguous_interface_refs"] == {
        "interface:submit-order": [
            "bir_op_submit_order_v1",
            "bir_op_submit_order_v2",
        ]
    }
    assert receipt["added_relation_count"] == 0


def test_rejected_semantic_edge_never_enters_behavior_ir() -> None:
    model = _behavior_ir(_operation(
        "bir_op_submit_order",
        operation_id="submitOrder",
        interface_ref="interface:submit-order",
        path="/orders/{id}/submit",
    ))

    bound, receipt = bind_accepted_semantic_operations(
        model,
        _asset("interface:submit-order", status="rejected"),
    )

    assert bound["invariants"][0]["operation_refs"] == []
    assert receipt["status"] == "NO_ACCEPTED_LINKS"
    assert receipt["accepted_edge_count"] == 0


def test_public_discovery_runtime_installs_binding_before_plan_compilation() -> None:
    from ai_test_asset_center import discovery_runtime
    from ai_test_asset_center import discovery_runtime_planning
    from ai_test_asset_center import discovery_runtime_semantic_binding
    from ai_test_asset_center.discovery_runtime_semantic_binding import (
        build_behavior_ir_with_semantic_operation_bindings,
    )

    assert discovery_runtime_planning.build_behavior_ir_from_knowledge_asset is (
        build_behavior_ir_with_semantic_operation_bindings
    )
    # The public entry point is the binding-layer plan wrapper (it binds the
    # scan UI/event/performance/stability contract contexts around one
    # planning call) and it must delegate to the planning authority; the
    # wrapper never replaces compilation.
    assert discovery_runtime.build_discovery_plan is (
        discovery_runtime_semantic_binding.build_discovery_plan
    )

    class _ProbeInputs:
        campaign_context: dict = {}

    delegated: list[tuple] = []
    sentinel = object()
    original_plan = discovery_runtime_planning.build_discovery_plan

    def _recording_plan(inputs, campaign_handle):
        delegated.append((inputs, campaign_handle))
        return sentinel

    discovery_runtime_planning.build_discovery_plan = _recording_plan
    try:
        handle = object()
        result = discovery_runtime.build_discovery_plan(_ProbeInputs(), handle)
    finally:
        discovery_runtime_planning.build_discovery_plan = original_plan

    assert result is sentinel
    assert len(delegated) == 1
    assert delegated[0][1] is handle
