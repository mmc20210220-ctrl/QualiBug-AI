from __future__ import annotations

from ai_test_asset_center.behavior_ir_hypothesis_coverage import (
    build_behavior_ir_coverage_map,
    build_exhaustive_obligation_matrix,
)
from ai_test_asset_center.behavior_ir import empty_behavior_ir
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir
from ai_test_asset_center.state_audit_planner import (
    build_readonly_state_audit_obligations,
)


def test_bare_operation_does_not_create_risk_family_coverage() -> None:
    behavior_ir = {
        "operations": [{
            "id": "op_create_item",
            "method": "POST",
            "path": "/items",
            "source_refs": [{"source_id": "api", "locator": "POST /items"}],
        }],
        "actors": [],
        "entities": [],
        "invariants": [],
        "relations": [],
        "states": [],
    }

    coverage = build_behavior_ir_coverage_map(behavior_ir)

    assert not [row for row in coverage["nodes"] if row["node_type"] == "operation"]


def test_operation_entity_update_is_not_state_transition_coverage() -> None:
    behavior_ir = {
        "operations": [{"id": "op_update_item", "method": "PATCH", "path": "/items/:id"}],
        "actors": [],
        "entities": [{"id": "entity_item", "name": "item"}],
        "invariants": [],
        "states": [],
        "relations": [{
            "id": "rel_update_item",
            "relation_type": "transitions",
            "from_ref": "op_update_item",
            "to_ref": "entity_item",
            "operation_ref": "op_update_item",
        }],
    }

    coverage = build_behavior_ir_coverage_map(behavior_ir)

    assert not coverage["nodes"]


def test_unbound_forbidden_transition_is_not_mapped_to_get_audit() -> None:
    behavior_ir = {
        "operations": [{
            "id": "get_order",
            "method": "GET",
            "path": "/orders/:id",
        }],
        "actors": [{
            "id": "actor_admin",
            "role": "admin",
            "credential_secret_ref": "secret_ref:actor:admin",
        }],
        "relations": [],
        "invariants": [{
            "id": "inv_forbidden_order_transition",
            "description": "An order must not move from PAID to CANCELLED.",
            "expression": {
                "kind": "forbidden_state_transition",
                "operands": [{
                    "entity": "order",
                    "from_state": "PAID",
                    "to_state": "CANCELLED",
                }],
            },
            "source_refs": [{
                "source_id": "business_rules",
                "locator": "order:PAID-/->CANCELLED",
            }],
        }],
    }

    assert build_readonly_state_audit_obligations(behavior_ir) == []


def test_prose_only_postcondition_is_reported_as_a_gap_not_scheduled() -> None:
    behavior_ir = {
        "operations": [{
            "id": "read_order",
            "method": "GET",
            "path": "/orders",
        }],
        "actors": [{"id": "actor_admin", "runtime_bound": True}],
        "relations": [],
        "invariants": [{
            "id": "inv_prose_only",
            "expression": {
                "kind": "postcondition",
                "operands": [{
                    "entity_ref": "order",
                    "field": "",
                    "expected_value": "",
                }],
            },
        }],
    }
    report: dict[str, object] = {}

    obligations = build_exhaustive_obligation_matrix(behavior_ir, report=report)

    assert not [row for row in obligations if row.get("risk_family") == "invariant"]
    assert report["matrix_skipped"]["invariant_postcondition_unbound"] == 1


def test_unbound_postcondition_is_a_behavior_ir_gap_before_obligation_compile() -> None:
    behavior_ir = empty_behavior_ir(project_id="unbound-postcondition-test")
    behavior_ir.update({
        "operations": [{
            "id": "read_order",
            "method": "GET",
            "path": "/orders",
            "read_write": "read",
        }],
        "actors": [{
            "id": "actor_admin",
            "role": "admin",
            "runtime_bound": True,
            "credential_secret_ref": "secret_ref:actor:admin",
        }],
        "relations": [],
        "invariants": [{
            "id": "inv_unbound_postcondition",
            "expression": {
                "kind": "postcondition",
                "operands": [{"entity_ref": "order", "field": "", "expected_value": ""}],
            },
            "source_refs": [{"source_id": "rules", "locator": "rule:1"}],
        }],
    })

    result = compile_obligations_from_behavior_ir(behavior_ir)

    assert not [
        row
        for row in result["obligations"]
        if row.get("risk_family") == "state"
        and row.get("property", {}).get("invariant_ref") == "inv_unbound_postcondition"
    ]
    assert any(
        row.get("code") == "SOURCE_POSTCONDITION_EFFECT_UNBOUND"
        and row.get("subject_ref") == "inv_unbound_postcondition"
        for row in result["coverage_gaps"]
    )


def test_invariant_postcondition_keeps_its_assertion_kind() -> None:
    behavior_ir = empty_behavior_ir(project_id="invariant-postcondition-kind-test")
    behavior_ir.update({
        "operations": [{
            "id": "read_order",
            "method": "GET",
            "path": "/orders",
            "read_write": "read",
            "source_refs": [{"source_id": "api", "locator": "GET /orders"}],
        }],
        "actors": [{
            "id": "actor_admin",
            "role": "admin",
            "account_ref": "admin@example.test",
            "runtime_bound": True,
            "credential_secret_ref": "secret_ref:test_accounts:admin",
        }],
    })
    obligation = {
        "obligation_id": "obl_invariant_postcondition_kind",
        "risk_family": "invariant",
        "property": {
            "template": "invariant_violation_detection",
            "invariant_kind": "postcondition",
            "operation_ref": "read_order",
            "expression": {
                "kind": "postcondition",
                "operands": [{"field": "status", "expected_value": "PAID"}],
            },
        },
        "required_actors": ["actor_admin"],
        "required_operations": ["read_order"],
        "required_observers": ["http_response", "entity_state"],
        "cleanup_requirement": "not_required",
        "source_refs": [{"source_id": "rules", "locator": "rule:1"}],
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=behavior_ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["reason_code"] == "FIELD_LEVEL_RULE_NOT_EXECUTABLE"
    assert experiment["compile_receipt"]["detail"] == "postcondition_missing_field_observer"
