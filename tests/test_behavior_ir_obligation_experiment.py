"""Phase 1–3: Behavior IR, obligations, experiments, assertions, contract oracles."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from ai_test_asset_center.assertion_dsl import evaluate_assertion
from ai_test_asset_center.behavior_ir import (
    BehaviorIRError,
    SCHEMA_VERSION,
    build_behavior_ir_from_knowledge_asset,
    empty_behavior_ir,
    migrate_behavior_ir_v1_to_v2,
    validate_behavior_ir,
)
from ai_test_asset_center.behavior_ir_core import _request_schema_for_operation
from ai_test_asset_center.contract_oracles import (
    demote_heuristic_business_oracle_finding,
    evaluate_contract_oracle,
)
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation, compile_experiments
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir
from ai_test_asset_center.obligation_compiler_base import _cleanup_requirement
from ai_test_asset_center.runtime_binding_graph import (
    _request_example,
    apply_binding,
    build_binding_plan,
    declared_action_compensators,
    declared_effect_observers,
    extract_placeholders,
    unresolved_placeholders,
)
from ai_test_asset_center.adaptive_discovery_planner import plan_obligation_round


ROOT = Path(__file__).resolve().parents[1]
NEW_MODULES = [
    ROOT / "ai_test_asset_center" / "behavior_ir.py",
    ROOT / "ai_test_asset_center" / "test_obligation.py",
    ROOT / "ai_test_asset_center" / "obligation_compiler.py",
    ROOT / "ai_test_asset_center" / "experiment_contract.py",
    ROOT / "ai_test_asset_center" / "experiment_compiler.py",
    ROOT / "ai_test_asset_center" / "runtime_binding_graph.py",
    ROOT / "ai_test_asset_center" / "assertion_dsl.py",
    ROOT / "ai_test_asset_center" / "contract_oracles.py",
    ROOT / "ai_test_asset_center" / "adaptive_discovery_planner.py",
]


def test_missing_observer_contract_blocks_before_compile() -> None:
    operation = {
        "id": "read_resource",
        "method": "GET",
        "path": "/api/resources",
        "read_write": "read",
        "source_refs": [{"source_id": "api", "kind": "api_operation", "locator": "GET /api/resources"}],
    }
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl_missing_observer",
            "risk_family": "validation",
            "property": {"operation_ref": "read_resource"},
            "required_operations": ["read_resource"],
            "required_actors": ["actor-public"],
            "required_observers": [],
            "source_refs": list(operation["source_refs"]),
        },
        behavior_ir={
            "operations": [operation],
            "actors": [{"id": "actor-public", "role": "public"}],
            "relations": [],
            "conflicts": [],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"] == {
        "status": "BLOCKED",
        "reason_code": "BLOCKED_MISSING_OBSERVER",
        "detail": "none",
    }


def test_request_example_does_not_become_required_request_schema() -> None:
    schema = _request_schema_for_operation(
        {
            "request_example": {
                "documented_example_only": "value",
                "another_example_only": 1,
            }
        }
    )

    body_schema = schema["content"]["application/json"]["schema"]
    assert set(body_schema["properties"]) == {
        "documented_example_only",
        "another_example_only",
    }
    assert "required" not in body_schema


def test_request_body_placeholder_uses_source_declared_runtime_read_binding() -> None:
    operation = {
        "id": "reserve_inventory",
        "method": "POST",
        "path": "/api/inventory/reserve",
        "request_example": {
            "sku": "SKU-1",
            "qty": 1,
            "orderId": "<order_id>",
        },
    }
    behavior_ir = {
        "operations": [
            operation,
            {
                "id": "list_orders",
                "method": "GET",
                "path": "/api/orders",
                "entity_refs": ["entity-order"],
            },
        ],
        "entities": [{"id": "entity-order", "name": "order"}],
        "body_reference_relations": [{
            "operation_ref": "reserve_inventory",
            "body_path": "orderId",
            "target_entity_ref": "entity-order",
            "status": "RESOLVED",
            "source_refs": [{
                "kind": "database_foreign_key",
                "locator": "inventory.order_id -> orders.id",
            }],
        }],
    }

    plan = build_binding_plan(
        operation=operation,
        obligation={},
        behavior_ir=behavior_ir,
    )

    binding = next(row for row in plan if row.get("target") == "order_id")
    assert binding["status"] == "runtime_resolvable"
    assert binding["body_template_paths"] == ["orderId"]
    assert binding["resolver_operations"] == [{
        "operation_ref": "list_orders",
        "method": "GET",
        "path": "/api/orders",
    }]
    assert unresolved_placeholders(operation, plan) == []


def test_request_body_placeholder_without_declared_resolver_is_blocked() -> None:
    operation = {
        "id": "reserve_inventory",
        "method": "POST",
        "path": "/api/inventory/reserve",
        "request_example": {"orderId": "<order_id>"},
    }

    plan = build_binding_plan(
        operation=operation,
        obligation={},
        behavior_ir={"operations": [operation]},
    )

    binding = next(row for row in plan if row.get("target") == "order_id")
    assert binding["status"] == "blocked"
    assert binding["source_priority"] == "body_placeholder_unresolvable"
    assert binding["blocked_reason"] == "BODY_PARAMETER_NOT_SOURCE_BOUND"
    assert "generated_value" not in binding
    assert unresolved_placeholders(operation, plan) == []


def test_fixture_create_without_actor_bound_resolver_does_not_satisfy_binding() -> None:
    operation = {
        "id": "reject_refund",
        "method": "POST",
        "path": "/api/refunds/{id}/reject",
        "read_write": "write",
        "request_example": {},
    }
    create_refund = {
        "id": "create_refund",
        "method": "POST",
        "path": "/api/refunds",
        "read_write": "write",
        "request_example": {
            "orderId": "<order_id>",
            "amount": 100,
            "reason": "customer_requested",
        },
    }
    behavior_ir = {
        "operations": [
            operation,
            create_refund,
            {
                "id": "read_refund",
                "method": "GET",
                "path": "/api/refunds/{id}",
                "read_write": "read",
            },
            {
                "id": "list_orders",
                "method": "GET",
                "path": "/api/orders",
                "read_write": "read",
            },
        ],
        "actors": [{
            "id": "actor-admin",
            "role": "admin",
            "credential_secret_ref": "secret_ref:admin",
        }],
    }

    plan = build_binding_plan(
        operation=operation,
        obligation={},
        behavior_ir=behavior_ir,
    )

    binding = next(row for row in plan if row.get("target") == "id")
    assert binding["status"] == "blocked"
    assert binding["source_priority"] == "path_placeholder_unresolvable"
    assert binding["blocked_reason"] == "PLACEHOLDER_PATH_PARAMETER_NOT_RESOLVED"
    assert "generated_value" not in binding
    assert unresolved_placeholders(operation, plan) == []


def _sample_asset() -> dict:
    return {
        "sources": [{"source_id": "src-api", "filename": "api.md", "source_type": "api"}],
        "permission_matrix": [
            {
                "role": "viewer_role",
                "resource": "/resources/{id}",
                "actions": ["read"],
                "denied_actions": ["write"],
                "scope": "own",
            },
            {"role": "owner_role", "resource": "/resources/{id}", "actions": ["read", "write"], "scope": "own"},
        ],
        "rule_library": [
            {"rule_id": "rule-1", "statement": "resource quantity must be conserved across transfer", "kind": "conservation"},
        ],
        "state_machines": [
            {"entity": "resource", "states": ["draft", "active"]},
        ],
        "objects": [{"name": "resource", "kind": "entity"}],
        "operations": [
            {
                "operation_id": "list_resources",
                "method": "GET",
                "path": "/resources",
                "side_effect_class": "read",
            },
            {
                "operation_id": "get_resource",
                "method": "GET",
                "path": "/resources/{id}",
                "side_effect_class": "read",
            },
            {
                "operation_id": "update_resource",
                "method": "PUT",
                "path": "/resources/{id}",
                "side_effect_class": "write",
            },
        ],
    }


def _relation(
    relation_id: str,
    relation_type: str,
    from_ref: str,
    to_ref: str,
    *,
    operation_ref: str = "",
    actor_ref: str = "",
) -> dict:
    return {
        "id": relation_id,
        "relation_type": relation_type,
        "from_ref": from_ref,
        "to_ref": to_ref,
        "operation_ref": operation_ref,
        "actor_ref": actor_ref,
        "preconditions": [],
        "effects": [],
        "source_refs": [{"source_id": "source-relation-test"}],
    }


def test_behavior_ir_validation_reports_duplicate_node_ids() -> None:
    model = empty_behavior_ir(project_id="duplicate-node-id")
    model["operations"] = [
        {
            "id": "bir_duplicate_operation",
            "operation_id": "read_resource",
            "method": "GET",
            "path": "/api/resources/{id}",
            "source_refs": [],
            "derivation": "explicit",
            "status": "accepted",
        },
        {
            "id": "bir_duplicate_operation",
            "operation_id": "delete_resource",
            "method": "DELETE",
            "path": "/api/resources/{id}",
            "source_refs": [],
            "derivation": "explicit",
            "status": "accepted",
        },
    ]

    errors = validate_behavior_ir(model, require_explicit_relations=False)

    assert "duplicate_node_id:operations:bir_duplicate_operation" in errors


def test_behavior_ir_schema_and_source_refs() -> None:
    ir = build_behavior_ir_from_knowledge_asset(_sample_asset(), project_id="proj-a", source_snapshot_hash="abc")
    assert ir["schema_version"] == SCHEMA_VERSION
    assert validate_behavior_ir(ir) == []
    assert ir["operations"]
    assert ir["actors"]
    assert all(item.get("source_refs") for item in ir["operations"])
    assert all("password" not in str(item).lower() or "secret_ref" in str(item) for item in ir["actors"])
    assert all("credential_secret_ref" in item for item in ir["actors"])
    assert all({
        "operation_ref",
        "actor_ref",
        "preconditions",
        "effects",
        "source_refs",
    }.issubset(relation) for relation in ir["relations"])


def test_obligation_compiler_accounts_for_every_source_invariant() -> None:
    ir = empty_behavior_ir(project_id="all-invariants")
    ir["operations"] = [{
        "id": "op-read-resource",
        "method": "GET",
        "path": "/resources",
        "read_write": "read",
        "source_refs": [{"source_id": "api-source"}],
    }]
    ir["invariants"] = [
        {
            "id": f"invariant-{index}",
            "description": f"source invariant {index}",
            "expression": {
                "kind": "business_rule",
                "operator": "must_hold",
                "operands": [],
                "raw": f"source invariant {index}",
            },
            "source_refs": [{"source_id": f"rule-source-{index}"}],
        }
        for index in range(35)
    ]
    ir["relations"] = [
        _relation(
            f"relation-invariant-{index}",
            "observes",
            "op-read-resource",
            f"invariant-{index}",
            operation_ref="op-read-resource",
        )
        for index in range(35)
    ]

    compiled = compile_obligations_from_behavior_ir(ir)

    assert compiled["obligation_count"] == len(compiled["obligations"]) == 1
    assert sum(compiled["by_family"].values()) == 1
    assert set(compiled["obligations"][0]["property"]["consolidated_invariant_refs"]) == {
        f"invariant-{index}" for index in range(35)
    }
    assert compiled["obligation_consolidation_receipt"]["input_count"] == 35
    assert compiled["obligation_consolidation_receipt"]["output_count"] == 1


def test_behavior_ir_builder_emits_permission_and_compensation_relations() -> None:
    asset = _sample_asset()
    asset["operations"].extend([
        {
            "operation_id": "create_resource",
            "method": "POST",
            "path": "/resources",
            "side_effect_class": "write",
        },
        {
            "operation_id": "delete_resource",
            "method": "DELETE",
            "path": "/resources/{id}",
            "side_effect_class": "write",
        },
    ])
    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="relation-builder-test")
    operations = {row["operation_id"]: row["id"] for row in ir["operations"]}
    actors = {row["role"]: row["id"] for row in ir["actors"]}

    assert any(
        row["relation_type"] == "permits"
        and row["operation_ref"] == operations["update_resource"]
        and row["actor_ref"] == actors["owner_role"]
        for row in ir["relations"]
    )
    assert any(
        row["relation_type"] == "denies"
        and row["operation_ref"] == operations["update_resource"]
        and row["actor_ref"] == actors["viewer_role"]
        for row in ir["relations"]
    )
    assert any(
        row["relation_type"] == "compensates"
        and row["from_ref"] == operations["delete_resource"]
        and row["to_ref"] == operations["create_resource"]
        and row["operation_ref"] == operations["delete_resource"]
        for row in ir["relations"]
    )


def test_behavior_ir_canonicalizes_duplicate_source_operation_ids() -> None:
    asset = {
        "operations": [
            {"method": "POST", "path": "/orders", "operation_id": "shared_action"},
            {"method": "POST", "path": "/refunds", "operation_id": "shared_action"},
        ],
    }

    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="duplicate-operation-id")

    operations = ir["operations"]
    assert len(operations) == 2
    assert len({row["operation_id"] for row in operations}) == 2
    assert all("shared_action" in row["source_operation_refs"] for row in operations)
    assert any(
        conflict.get("conflict_type") == "duplicate_source_operation_id"
        for conflict in ir["conflicts"]
    )


def test_permission_actions_match_explicit_operation_action_not_only_http_method() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "permission_matrix": [
                {
                    "role": "buyer",
                    "resource": "order",
                    "actions": ["cancel"],
                    "decision": "allow",
                },
                {
                    "role": "auditor",
                    "resource": "inventory",
                    "actions": ["modify"],
                    "decision": "deny",
                },
                {
                    "role": "warehouse",
                    "resource": "inventory",
                    "actions": ["adjust"],
                    "decision": "allow",
                },
                {
                    "role": "requester",
                    "resource": "refund",
                    "actions": ["request", "create"],
                    "decision": "allow",
                },
            ],
            "operations": [
                {
                    "operation_id": "cancel_order",
                    "method": "POST",
                    "path": "/orders/{id}/cancel",
                },
                {
                    "operation_id": "reserve_inventory",
                    "method": "POST",
                    "path": "/inventory/reserve",
                },
                {
                    "operation_id": "adjust_inventory",
                    "method": "POST",
                    "path": "/inventory/admin/adjust",
                },
                {
                    "operation_id": "read_inventory",
                    "method": "GET",
                    "path": "/inventory/{sku}",
                },
                {
                    "operation_id": "create_refund",
                    "method": "POST",
                    "path": "/refunds",
                },
                {
                    "operation_id": "approve_refund",
                    "method": "POST",
                    "path": "/refunds/{id}/approve",
                },
                {
                    "operation_id": "create_singular_refund",
                    "method": "POST",
                    "path": "/api/refund",
                },
            ],
        },
        project_id="permission-operation-actions",
    )

    actors = {row["role"]: row["id"] for row in ir["actors"]}
    operations = {row["operation_id"]: row["id"] for row in ir["operations"]}
    decisions = {
        (row["relation_type"], row["actor_ref"], row["operation_ref"])
        for row in ir["relations"]
        if row.get("relation_type") in {"permits", "denies"}
    }
    assert ("permits", actors["buyer"], operations["cancel_order"]) in decisions
    assert ("denies", actors["auditor"], operations["reserve_inventory"]) in decisions
    assert ("denies", actors["auditor"], operations["adjust_inventory"]) in decisions
    assert ("denies", actors["auditor"], operations["read_inventory"]) not in decisions
    assert ("permits", actors["warehouse"], operations["adjust_inventory"]) in decisions
    assert ("permits", actors["warehouse"], operations["reserve_inventory"]) not in decisions
    assert ("permits", actors["requester"], operations["create_refund"]) in decisions
    assert ("permits", actors["requester"], operations["create_singular_refund"]) in decisions
    assert ("permits", actors["requester"], operations["approve_refund"]) not in decisions


def test_behavior_ir_derives_explicit_role_restriction_from_source_contract() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "permission_matrix": [
                {
                    "role": "admin",
                    "resource": "*",
                    "actions": ["*"],
                    "source_id": "role-contract",
                },
                {
                    "role": "buyer",
                    "resource": "profile",
                    "actions": ["read"],
                    "source_id": "role-contract",
                },
            ],
            "operations": [{
                "operation_id": "search_users",
                "method": "GET",
                "path": "/admin/users",
                "summary": "Only admin may use this operation.",
            }],
        },
        project_id="explicit-role-restriction",
    )
    operation = ir["operations"][0]
    actors = {row["role"]: row["id"] for row in ir["actors"]}
    decisions = {
        (row["relation_type"], row["actor_ref"])
        for row in ir["relations"]
        if row.get("operation_ref") == operation["id"]
    }

    assert ("permits", actors["admin"]) in decisions
    assert ("denies", actors["buyer"]) in decisions


def test_obligation_compiler_prefers_runtime_actor_over_role_placeholder() -> None:
    ir = empty_behavior_ir(project_id="runtime-actor-preference")
    ir.update({
        "operations": [{
            "id": "op-read-admin",
            "method": "GET",
            "path": "/admin/resources",
            "read_write": "read",
        }],
        "actors": [
            {"id": "admin-role", "role": "admin", "account_status": "active"},
            {
                "id": "admin-runtime",
                "role": "admin",
                "account_ref": "admin-1",
                "credential_secret_ref": "secret_ref:test_accounts:admin-1",
                "runtime_bound": True,
                "account_status": "active",
            },
            {"id": "buyer-role", "role": "buyer", "account_status": "active"},
            {
                "id": "buyer-runtime",
                "role": "buyer",
                "account_ref": "buyer-1",
                "credential_secret_ref": "secret_ref:test_accounts:buyer-1",
                "runtime_bound": True,
                "account_status": "active",
            },
        ],
        "relations": [
            _relation("permit-admin-role", "permits", "admin-role", "op-read-admin", operation_ref="op-read-admin", actor_ref="admin-role"),
            _relation("permit-admin-runtime", "permits", "admin-runtime", "op-read-admin", operation_ref="op-read-admin", actor_ref="admin-runtime"),
            _relation("deny-buyer-role", "denies", "buyer-role", "op-read-admin", operation_ref="op-read-admin", actor_ref="buyer-role"),
            _relation("deny-buyer-runtime", "denies", "buyer-runtime", "op-read-admin", operation_ref="op-read-admin", actor_ref="buyer-runtime"),
        ],
    })

    authorization = [
        row
        for row in compile_obligations_from_behavior_ir(ir)["obligations"]
        if row["risk_family"] == "authorization"
    ]

    assert len(authorization) == 1
    assert authorization[0]["required_actors"] == [
        "admin-runtime",
        "buyer-runtime",
    ]


def test_role_catalog_actor_without_runtime_binding_is_gap_not_executable_authorization() -> None:
    ir = empty_behavior_ir(project_id="role-catalog-runtime-gap")
    ir.update({
        "operations": [{
            "id": "op-admin-action",
            "method": "POST",
            "path": "/admin/actions",
            "read_write": "write",
        }],
        "actors": [
            {
                "id": "actor-admin-role",
                "role": "admin",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:actor:admin",
            },
            {
                "id": "actor-buyer-role",
                "role": "buyer",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:actor:buyer",
            },
        ],
        "relations": [
            _relation(
                "permit-admin-role",
                "permits",
                "actor-admin-role",
                "op-admin-action",
                operation_ref="op-admin-action",
                actor_ref="actor-admin-role",
            ),
            _relation(
                "deny-buyer-role",
                "denies",
                "actor-buyer-role",
                "op-admin-action",
                operation_ref="op-admin-action",
                actor_ref="actor-buyer-role",
            ),
        ],
    })

    compiled = compile_obligations_from_behavior_ir(ir)

    assert not any(
        obligation["risk_family"] == "authorization"
        for obligation in compiled["obligations"]
    )
    actor_gaps = [
        gap
        for gap in compiled["coverage_gaps"]
        if gap.get("code") == "BLOCKED_MISSING_ACTOR_BINDING"
    ]
    assert {gap["actor_ref"] for gap in actor_gaps} == {
        "actor-admin-role",
        "actor-buyer-role",
    }


def test_runtime_behavior_ir_emits_v2_relations() -> None:
    from ai_test_asset_center import behavior_ir as behavior_ir_module

    ir = empty_behavior_ir(project_id="project-1", source_snapshot_hash="source-sha")

    assert ir["schema_version"] == "qualibug.behavior-ir.v2"
    assert all(
        row["relation_type"] in behavior_ir_module.ALLOWED_RELATION_TYPES
        for row in ir["relations"]
    )


def test_obligation_compiler_binds_state_transition_through_explicit_relation() -> None:
    ir = {
        "schema_version": "qualibug.behavior-ir.v2",
        "model_id": "bir-explicit-transition",
        "sources": [],
        "entities": [],
        "operations": [
            {
                "id": "op-unrelated-write",
                "method": "POST",
                "path": "/resources",
                "read_write": "write",
            },
            {
                "id": "op-explicit-transition",
                "method": "PATCH",
                "path": "/resources/{id}",
                "read_write": "write",
            },
        ],
        "actors": [],
        "states": [
            {"id": "state-draft", "entity_ref": "entity-resource", "name": "draft"},
            {"id": "state-active", "entity_ref": "entity-resource", "name": "active"},
        ],
        "relations": [{
            "id": "relation-activate",
            "relation_type": "transitions",
            "from_ref": "state-draft",
            "to_ref": "state-active",
            "operation_ref": "op-explicit-transition",
            "actor_ref": "",
            "preconditions": [],
            "effects": [],
            "source_refs": [{"source_id": "source-state-machine"}],
        }],
        "invariants": [],
        "observation_surfaces": [],
        "capabilities": [],
        "conflicts": [],
        "coverage_gaps": [],
    }

    compiled = compile_obligations_from_behavior_ir(ir)
    state = next(row for row in compiled["obligations"] if row["risk_family"] == "state")

    assert state["property"]["operation_ref"] == "op-explicit-transition"
    assert state["property"]["from_state_ref"] == "state-draft"
    assert state["property"]["to_state_ref"] == "state-active"
    assert state["relation_refs"] == ["relation-activate"]


def test_state_nodes_without_transition_relation_emit_compile_gap() -> None:
    ir = empty_behavior_ir(project_id="state-gap-test")
    ir.update({
        "operations": [{"id": "op-write", "method": "PATCH", "path": "/resources/{id}", "read_write": "write"}],
        "states": [
            {"id": "state-draft", "entity_ref": "entity-resource", "name": "draft"},
            {"id": "state-active", "entity_ref": "entity-resource", "name": "active"},
        ],
    })

    compiled = compile_obligations_from_behavior_ir(ir)

    assert not any(row["risk_family"] == "state" for row in compiled["obligations"])
    assert any(
        row.get("code") == "BLOCKED_MISSING_IR_RELATION"
        and row.get("subject_ref") == "entity-resource"
        for row in compiled["coverage_gaps"]
    )


def test_behavior_ir_emits_gap_for_unbound_state_transition() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "operations": [{"method": "PATCH", "path": "/orders/{id}", "operation_id": "update_order"}],
            "state_machines": [{
                "source_id": "workflow-source",
                "entity": "order",
                "states": ["draft", "active"],
                "transitions": [{"from": "draft", "to": "active"}],
            }],
        },
        project_id="unbound-state-transition",
    )

    assert not any(row.get("relation_type") == "transitions" for row in ir["relations"])
    assert any(
        row.get("gap_type") == "state_transition_operation_unresolved"
        and row.get("from_state") == "draft"
        and row.get("to_state") == "active"
        and row.get("source_refs", [{}])[0].get("source_id") == "workflow-source"
        for row in ir["coverage_gaps"]
    )


def test_state_name_path_similarity_never_creates_transition_binding() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "operations": [
                {
                    "method": "POST",
                    "path": "/resources/{id}/activate",
                    "operation_id": "activate_resource",
                }
            ],
            "state_machines": [{
                "source_id": "workflow-source",
                "entity": "resource",
                "states": ["draft", "activated"],
                "transitions": [{"from": "draft", "to": "activated"}],
            }],
        },
        project_id="no-state-path-inference",
    )

    assert not any(row.get("relation_type") == "transitions" for row in ir["relations"])
    assert any(
        row.get("gap_type") == "state_transition_operation_unresolved"
        for row in ir["coverage_gaps"]
    )


def test_permission_matrix_never_invents_api_operations_or_request_bodies() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "permission_matrix": [{
                "role": "operator",
                "resource": "resources",
                "actions": ["approve"],
            }],
        },
        project_id="no-permission-operation-inference",
    )

    assert ir["operations"] == []


def test_invariant_entity_text_never_guesses_an_operation_reference() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "operations": [{
                "method": "PATCH",
                "path": "/resources/{id}",
                "operation_id": "update_resource",
            }],
            "rule_library": [{
                "rule_id": "resource-rule",
                "statement": "resource must remain valid",
                "kind": "validation",
                "entity": "resource",
            }],
        },
        project_id="no-invariant-operation-inference",
    )

    invariant = next(row for row in ir["invariants"] if row.get("source_rule_refs"))
    assert not invariant.get("operation_refs")


def test_behavior_ir_preserves_forbidden_state_transition_as_typed_invariant() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "operations": [
                {
                    "method": "POST",
                    "path": "/resources/{id}/reopen",
                    "operation_id": "reopen_resource",
                },
            ],
            "state_machines": [{
                "source_id": "workflow-source",
                "state_machine_id": "resource-workflow",
                "entity": "resource",
                "states": ["active"],
                "forbidden_transitions": [
                    {"from": "closed", "to": "active"},
                ],
            }],
        },
        project_id="forbidden-state-transition",
    )

    invariant = next(
        row
        for row in ir["invariants"]
        if row.get("expression", {}).get("kind") == "forbidden_state_transition"
    )
    assert invariant["expression"]["operator"] == "must_not_transition"
    assert invariant["expression"]["operands"] == [{
        "entity_ref": "resource",
        "from_state": "closed",
        "to_state": "active",
    }]
    assert invariant["source_refs"][0]["source_id"] == "workflow-source"
    assert {row["name"] for row in ir["states"]} >= {"closed", "active"}
    assert not any(
        row.get("relation_type") == "transitions"
        and row.get("from_ref") == next(
            state["id"] for state in ir["states"] if state["name"] == "closed"
        )
        for row in ir["relations"]
    )
    assert any(
        row.get("gap_type") == "forbidden_state_transition_operation_unresolved"
        and row.get("source_refs", [{}])[0].get("source_id") == "workflow-source"
        for row in ir["coverage_gaps"]
    )


def test_behavior_ir_binds_forbidden_transition_only_from_explicit_operation_ref() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "operations": [
                {
                    "method": "POST",
                    "path": "/resources/{id}/reopen",
                    "operation_id": "reopen_resource",
                },
            ],
            "state_machines": [{
                "source_id": "workflow-source",
                "entity": "resource",
                "states": ["closed", "active"],
                "forbidden_transitions": [{
                    "from": "closed",
                    "to": "active",
                    "operation_ref": "reopen_resource",
                }],
            }],
        },
        project_id="bound-forbidden-state-transition",
    )

    invariant = next(
        row
        for row in ir["invariants"]
        if row.get("expression", {}).get("kind") == "forbidden_state_transition"
    )
    operation = next(row for row in ir["operations"] if row["operation_id"] == "reopen_resource")
    assert invariant["operation_refs"] == [operation["id"]]
    assert any(
        row.get("relation_type") == "observes"
        and row.get("to_ref") == invariant["id"]
        and row.get("operation_ref") == operation["id"]
        for row in ir["relations"]
    )
    assert not any(
        row.get("gap_type") == "forbidden_state_transition_operation_unresolved"
        for row in ir["coverage_gaps"]
    )


def test_obligation_compiler_rejects_v1_without_explicit_migration() -> None:
    v1 = empty_behavior_ir(project_id="project-v1")
    v1["schema_version"] = "qualibug.behavior-ir.v1"

    with pytest.raises(BehaviorIRError, match="behavior_ir_v2_required"):
        compile_obligations_from_behavior_ir(v1)


def test_behavior_ir_v1_migration_is_explicit_and_normalizes_relations() -> None:
    v1 = empty_behavior_ir(project_id="project-v1")
    v1["schema_version"] = "qualibug.behavior-ir.v1"
    v1["relations"] = [{
        "id": "relation-v1",
        "relation_type": "observes",
        "from_ref": "operation-1",
        "to_ref": "surface-1",
    }]

    migrated = migrate_behavior_ir_v1_to_v2(v1)

    assert migrated["schema_version"] == "qualibug.behavior-ir.v2"
    assert migrated["relations"][0]["operation_ref"] == ""
    assert migrated["relations"][0]["preconditions"] == []
    assert validate_behavior_ir(migrated) == []


def test_behavior_ir_merges_same_method_path_across_api_and_knowledge_sources() -> None:
    asset = {
        "interfaces": [{
            "interface_id": "markdown_api:POST:/resources/:id/approve",
            "operation_id": "approve_resource",
            "method": "POST",
            "path": "/resources/:id/approve",
            "source_id": "api-document",
            "description": "Approve one source-declared resource",
        }],
        "rule_library": [{
            "rule_id": "rule-approve",
            "statement": "Approval requires the declared request field",
            "kind": "validation",
            "source_id": "product-rule",
        }],
        "relationships": [{
            "edge_id": "edge-rule-approve",
            "relation": "rule_to_interface",
            "from": "rule-approve",
            "to": "markdown_api:POST:/resources/:id/approve",
            "source_id": "knowledge-graph",
        }],
    }
    api_operations = [{
        "operation_id": "post_resources__id_approve",
        "method": "POST",
        "path": "/resources/{id}/approve",
        "request_schema": {
            "type": "object",
            "required": ["reason"],
            "properties": {"reason": {"type": "string"}},
        },
        "source_id": "submitted-api",
    }]

    ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="merged-operation-test",
        api_operations=api_operations,
    )

    assert len(ir["operations"]) == 1
    operation = ir["operations"][0]
    assert set(operation["source_operation_refs"]) == {
        "approve_resource",
        "markdown_api:POST:/resources/:id/approve",
        "post_resources__id_approve",
    }
    assert operation["request_schema"]["required"] == ["reason"]
    relation = next(
        row for row in ir["relations"]
        if row.get("source_relationship_ref") == "edge-rule-approve"
    )
    assert relation["operation_ref"] == operation["id"]


def test_behavior_ir_operation_merge_is_order_invariant_and_keeps_markdown_example() -> None:
    markdown_operation = {
        "interface_id": "markdown_api:POST:/api/cart/items",
        "operation_id": "markdown_cart_item_create",
        "method": "POST",
        "path": "/api/cart/items",
        "source_id": "markdown-api",
        "parameters": ["sku", "qty"],
        "field_dictionary": ["sku", "qty"],
        "source_excerpt": (
            "### POST /api/cart/items\n\n"
            "请求：\n\n"
            "```json\n"
            "{\"sku\":\"SKU-PHONE-001\",\"qty\":1}\n"
            "```"
        ),
    }
    sparse_openapi_operation = {
        "interface_id": "api:POST:/api/cart/items",
        "operation_id": "post_api_cart_items",
        "method": "POST",
        "path": "/api/cart/items",
        "source_id": "openapi",
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "qty": {"type": "integer"},
                        },
                    },
                },
            },
        },
    }

    first = build_behavior_ir_from_knowledge_asset(
        {"interfaces": [markdown_operation, sparse_openapi_operation]},
        project_id="operation-merge-order",
    )
    second = build_behavior_ir_from_knowledge_asset(
        {"interfaces": [sparse_openapi_operation, markdown_operation]},
        project_id="operation-merge-order",
    )

    assert first["model_id"] == second["model_id"]
    assert len(first["operations"]) == 1
    operation = first["operations"][0]
    assert operation["request_example"] == {"sku": "SKU-PHONE-001", "qty": 1}
    json_media = operation["request_schema"]["content"]["application/json"]
    assert json_media["example"] == {"sku": "SKU-PHONE-001", "qty": 1}
    assert set(json_media["schema"]["properties"]) >= {"sku", "qty"}
    assert operation["field_dictionary"] == ["qty", "sku"]
    assert operation["source_operation_refs"] == [
        "api:POST:/api/cart/items",
        "markdown_api:POST:/api/cart/items",
        "markdown_cart_item_create",
        "post_api_cart_items",
    ]


def test_behavior_ir_extracts_yaml_and_curl_request_examples() -> None:
    yaml_ir = build_behavior_ir_from_knowledge_asset(
        {
            "operations": [{
                "method": "POST",
                "path": "/orders",
                "source_excerpt": "```yaml\nsku: SKU-1\nqty: 2\n```",
            }],
        },
        project_id="yaml-request-example",
    )
    curl_ir = build_behavior_ir_from_knowledge_asset(
        {
            "operations": [{
                "method": "POST",
                "path": "/orders",
                "source_excerpt": "curl -X POST -d '{\"sku\":\"SKU-2\",\"qty\":1}' /orders",
            }],
        },
        project_id="curl-request-example",
    )

    assert yaml_ir["operations"][0]["request_example"] == {"sku": "SKU-1", "qty": 2}
    assert curl_ir["operations"][0]["request_example"] == {"sku": "SKU-2", "qty": 1}


def test_behavior_ir_never_inherits_request_body_from_sibling_operation() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "operations": [
                {
                    "operation_id": "change_user_status",
                    "method": "POST",
                    "path": "/api/auth/admin/users/{id}/status",
                    "request_example": {"status": "DISABLED"},
                },
                {
                    "operation_id": "current_user",
                    "method": "GET",
                    "path": "/api/auth/me",
                },
                {
                    "operation_id": "refresh_session",
                    "method": "POST",
                    "path": "/api/auth/refresh",
                },
            ],
        },
        project_id="operation-request-lineage",
    )

    by_id = {row["operation_id"]: row for row in ir["operations"]}
    assert by_id["change_user_status"]["request_example"] == {
        "status": "DISABLED",
    }
    assert by_id["current_user"]["request_example"] == {}
    assert by_id["refresh_session"]["request_example"] == {}


def test_behavior_ir_classifies_source_declared_read_like_post_without_cleanup_pressure() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "operations": [
                {
                    "operation_id": "validate_discount",
                    "method": "POST",
                    "path": "/api/discounts/validate",
                    "summary": "Validate a discount and calculate eligibility without changing state.",
                    "description": "Returns eligibility and estimated amount; no redemption is recorded.",
                    "request_example": {"code": "SAVE10", "amount": 100},
                },
                {
                    "operation_id": "redeem_discount",
                    "method": "POST",
                    "path": "/api/discounts/redeem",
                    "summary": "Redeem and consume a discount for an order.",
                    "request_example": {"code": "SAVE10", "orderId": "order-1"},
                },
                {
                    "operation_id": "query_report",
                    "method": "POST",
                    "path": "/api/reports/query",
                    "read_write": "read",
                    "summary": "Query a report with a complex filter body.",
                    "request_example": {"from": "2026-01-01", "to": "2026-01-31"},
                },
            ],
        },
        project_id="read-like-post-semantics",
    )

    by_id = {row["operation_id"]: row for row in ir["operations"]}

    assert by_id["validate_discount"]["read_write"] == "read"
    assert by_id["validate_discount"]["side_effect_class"] == "read"
    assert _cleanup_requirement(by_id["validate_discount"], ir["operations"], ir["relations"])["required"] is False

    assert by_id["redeem_discount"]["read_write"] == "write"
    assert by_id["redeem_discount"]["side_effect_class"] == "write"
    assert _cleanup_requirement(by_id["redeem_discount"], ir["operations"], ir["relations"])["required"] is True

    assert by_id["query_report"]["read_write"] == "read"
    assert by_id["query_report"]["side_effect_class"] == "read"
    assert _cleanup_requirement(by_id["query_report"], ir["operations"], ir["relations"])["required"] is False


def test_runtime_binding_does_not_inherit_unrelated_root_api_body() -> None:
    example = _request_example(
        {
            "method": "GET",
            "path": "/api/products",
            "request_example": {},
        },
        sibling_ops=[
            {
                "method": "POST",
                "path": "/api/orders",
                "request_example": {"addressId": "<address_id>"},
            }
        ],
    )

    assert example == {}


def test_runtime_binding_never_inherits_a_sibling_operation_body() -> None:
    example = _request_example(
        {
            "method": "POST",
            "path": "/api/orders/confirm",
            "request_example": {},
        },
        sibling_ops=[
            {
                "method": "POST",
                "path": "/api/orders/create",
                "request_example": {"addressId": "<address_id>"},
            }
        ],
    )

    assert example == {}


def test_behavior_ir_merges_structural_entity_aliases_and_binds_operation_path() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "business_objects": [{"name": "order", "kind": "business_object"}],
            "data_tables": [{"name": "orders", "kind": "resource"}],
        },
        project_id="merged-entity-test",
        api_operations=[{
            "operation_id": "create_order",
            "method": "POST",
            "path": "/api/orders",
        }],
    )

    assert len(ir["entities"]) == 1
    entity = ir["entities"][0]
    assert set(entity["source_entity_names"]) == {"order", "orders"}
    # Physical storage name from data_tables must stamp entity.table so cleanup
    # plans target orders, not the logical business-object name order.
    assert entity["table"] == "orders"
    operation = ir["operations"][0]
    relation = next(
        row for row in ir["relations"]
        if row.get("relation_type") == "produces"
    )
    assert relation["operation_ref"] == operation["id"]
    assert relation["to_ref"] == entity["id"]


def test_operation_entity_binding_prefers_most_specific_path_entity() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "business_objects": [{"name": "cart"}],
            "data_tables": [{"name": "cart_items"}],
        },
        project_id="specific-entity-test",
        api_operations=[{
            "operation_id": "create_cart_item",
            "method": "POST",
            "path": "/api/cart/items",
        }],
    )

    entities = {row["name"]: row for row in ir["entities"]}
    relation = next(
        row for row in ir["relations"]
        if row.get("relation_type") == "produces"
    )
    assert relation["to_ref"] == entities["cart_items"]["id"]


def test_action_write_uses_source_declared_parent_resource_observer() -> None:
    write = {
        "id": "approve-resource",
        "method": "POST",
        "path": "/api/resources/{id}/approve",
    }
    observer = {
        "id": "read-resource",
        "method": "GET",
        "path": "/api/resources/{id}",
    }

    assert declared_effect_observers(
        write,
        behavior_ir={"operations": [write, observer]},
    ) == [{
        "operation_ref": "read-resource",
        "method": "GET",
        "path": "/api/resources/{id}",
    }]


def test_write_effect_observer_can_bind_read_placeholder_from_request_body() -> None:
    write = {
        "id": "adjust-inventory",
        "method": "POST",
        "path": "/api/inventory/admin/adjust",
        "request_schema": {
            "content": {
                "application/json": {
                    "example": {
                        "sku": "SKU-PHONE-001",
                        "delta": 10,
                        "reason": "stock correction",
                    },
                },
            },
        },
    }
    observer = {
        "id": "read-inventory-by-sku",
        "method": "GET",
        "path": "/api/inventory/{sku}",
    }

    assert declared_effect_observers(
        write,
        behavior_ir={"operations": [write, observer]},
    ) == [{
        "operation_ref": "read-inventory-by-sku",
        "method": "GET",
        "path": "/api/inventory/{sku}",
    }]


def test_collection_create_observer_can_bind_identity_from_write_response() -> None:
    write = {
        "id": "create-refund",
        "method": "POST",
        "path": "/api/refunds",
        "request_example": {"orderId": "order-1", "amount": 100},
        "response_schema": {
            "201": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/Refund"},
                    },
                },
            },
        },
    }
    observer = {
        "id": "read-refund",
        "method": "GET",
        "path": "/api/refunds/{id}",
        "response_schema": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/Refund"},
                    },
                },
            },
        },
    }

    assert declared_effect_observers(
        write,
        behavior_ir={"operations": [write, observer]},
    ) == [{
        "operation_ref": "read-refund",
        "method": "GET",
        "path": "/api/refunds/{id}",
    }]


def test_invariant_obligation_uses_explicit_relation_operation() -> None:
    ir = empty_behavior_ir(project_id="invariant-relation-test")
    ir.update({
        "operations": [
            {"id": "op-unrelated", "method": "GET", "path": "/resources"},
            {"id": "op-conserving", "method": "POST", "path": "/transfers", "read_write": "write"},
        ],
        "invariants": [{
            "id": "invariant-conservation",
            "expression": {"kind": "conservation", "operator": "must_hold"},
            "confidence": 0.9,
        }],
        "relations": [
            _relation(
                "relation-conserves",
                "conserves",
                "op-conserving",
                "invariant-conservation",
                operation_ref="op-conserving",
            )
        ],
    })

    obligation = next(
        item
        for item in compile_obligations_from_behavior_ir(ir)["obligations"]
        if item["risk_family"] == "conservation"
    )

    assert obligation["property"]["operation_ref"] == "op-conserving"
    assert obligation["relation_refs"] == ["relation-conserves"]


def test_validation_invariant_does_not_compile_body_mutation_against_read_operation() -> None:
    ir = empty_behavior_ir(project_id="validation-read-operation-test")
    ir.update({
        "operations": [
            {
                "id": "op-read-inventory",
                "method": "GET",
                "path": "/inventory/{sku}",
                "read_write": "read",
            },
        ],
        "invariants": [{
            "id": "invariant-positive-qty",
            "expression": {
                "kind": "validation",
                "operator": "must_hold",
                "raw": "qty must be a positive integer",
            },
            "confidence": 0.9,
        }],
        "relations": [
            _relation(
                "relation-token-overlap",
                "observes",
                "op-read-inventory",
                "invariant-positive-qty",
                operation_ref="op-read-inventory",
            ),
        ],
    })

    compiled = compile_obligations_from_behavior_ir(ir)

    assert not any(
        item["risk_family"] == "validation"
        and item["property"].get("operation_ref") == "op-read-inventory"
        for item in compiled["obligations"]
    )
    assert any(
        gap["subject_ref"] == "invariant-positive-qty"
        and gap["status"] == "unsupported"
        for gap in compiled["coverage_gaps"]
    )


def test_permission_boundary_invariant_builds_visibility_actor_pair() -> None:
    ir = empty_behavior_ir(project_id="permission-boundary-pair-test")
    ir.update({
        "operations": [
            {
                "id": "op-read-addresses",
                "method": "GET",
                "path": "/api/users/addresses",
                "read_write": "read",
            },
        ],
        "actors": [
            {
                "id": "actor-owner",
                "role": "owner",
                "account_ref": "owner@example.test",
                "credential_secret_ref": "secret_ref:test_accounts:owner",
                "account_status": "active",
                "runtime_bound": True,
            },
            {
                "id": "actor-outsider",
                "role": "outsider",
                "account_ref": "outsider@example.test",
                "credential_secret_ref": "secret_ref:test_accounts:outsider",
                "account_status": "active",
                "runtime_bound": True,
            },
        ],
        "invariants": [{
            "id": "invariant-permission-boundary",
            "expression": {
                "kind": "permission_boundary",
                "operator": "must_hold",
                "raw": "Only the owning user may query addresses",
            },
            "confidence": 0.9,
        }],
        "relations": [
            _relation(
                "relation-observes-boundary",
                "observes",
                "op-read-addresses",
                "invariant-permission-boundary",
                operation_ref="op-read-addresses",
            ),
            _relation(
                "relation-permit-owner",
                "permits",
                "actor-owner",
                "op-read-addresses",
                operation_ref="op-read-addresses",
                actor_ref="actor-owner",
            ),
            _relation(
                "relation-deny-outsider",
                "denies",
                "actor-outsider",
                "op-read-addresses",
                operation_ref="op-read-addresses",
                actor_ref="actor-outsider",
            ),
        ],
    })

    compiled = compile_obligations_from_behavior_ir(ir)

    assert not any(
        item["risk_family"] == "validation"
        and item["property"].get("invariant_ref") == "invariant-permission-boundary"
        for item in compiled["obligations"]
    )
    visibility = next(
        item
        for item in compiled["obligations"]
        if item["risk_family"] == "visibility"
        and item["property"].get("invariant_ref") == "invariant-permission-boundary"
    )
    assert visibility["required_actors"] == ["actor-owner", "actor-outsider"]
    assert visibility["property"]["control_actor_ref"] == "actor-owner"
    assert visibility["property"]["treatment_actor_ref"] == "actor-outsider"
    assert {
        "http_response",
        "actor_identity",
        "authorization_comparison",
        "typed_assertion",
        "source_invariant",
    } <= set(visibility["required_observers"])

    experiment = compile_experiment_for_obligation(
        visibility,
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment
    assert experiment["control_plan"][0]["actor_ref"] == "actor-owner"
    assert experiment["treatment_plan"][0]["actor_ref"] == "actor-outsider"
    assert experiment["assertions"][0]["kind"] == "visibility"


def test_permission_boundary_without_denied_actor_becomes_actor_pair_gap() -> None:
    ir = empty_behavior_ir(project_id="permission-boundary-gap-test")
    ir.update({
        "operations": [
            {
                "id": "op-read-addresses",
                "method": "GET",
                "path": "/api/users/addresses",
                "read_write": "read",
            },
        ],
        "actors": [
            {
                "id": "actor-owner",
                "role": "owner",
                "account_ref": "owner@example.test",
                "credential_secret_ref": "secret_ref:test_accounts:owner",
                "account_status": "active",
                "runtime_bound": True,
            },
        ],
        "invariants": [{
            "id": "invariant-permission-boundary",
            "expression": {
                "kind": "permission_boundary",
                "operator": "must_hold",
                "raw": "Only the owning user may query addresses",
            },
            "confidence": 0.9,
        }],
        "relations": [
            _relation(
                "relation-observes-boundary",
                "observes",
                "op-read-addresses",
                "invariant-permission-boundary",
                operation_ref="op-read-addresses",
            ),
            _relation(
                "relation-permit-owner",
                "permits",
                "actor-owner",
                "op-read-addresses",
                operation_ref="op-read-addresses",
                actor_ref="actor-owner",
            ),
        ],
    })

    compiled = compile_obligations_from_behavior_ir(ir)

    assert not any(
        item["risk_family"] == "validation"
        and item["property"].get("invariant_ref") == "invariant-permission-boundary"
        for item in compiled["obligations"]
    )
    gap = next(
        gap
        for gap in compiled["coverage_gaps"]
        if gap.get("code") == "BLOCKED_MISSING_ACTOR_PAIR"
    )
    assert gap["risk_family"] == "visibility"
    assert gap["operation_ref"] == "op-read-addresses"


def test_entity_validation_uses_explicit_relation_operation() -> None:
    ir = empty_behavior_ir(project_id="entity-relation-test")
    ir.update({
        "entities": [{"id": "entity-resource", "name": "resource"}],
        "actors": [{
            "id": "actor-writer",
            "role": "writer",
            "account_status": "active",
            "credential_secret_ref": "secret_ref:test_accounts:writer",
            "runtime_bound": True,
        }],
        "operations": [
            {"id": "op-unrelated-write", "method": "POST", "path": "/other", "read_write": "write"},
            {"id": "op-resource-write", "method": "POST", "path": "/resources", "read_write": "write"},
        ],
        "relations": [
            _relation(
                "relation-produces-resource",
                "produces",
                "op-resource-write",
                "entity-resource",
                operation_ref="op-resource-write",
            ),
            _relation(
                "relation-permits-writer",
                "permits",
                "actor-writer",
                "op-resource-write",
                operation_ref="op-resource-write",
                actor_ref="actor-writer",
            ),
        ],
    })

    obligation = next(
        item
        for item in compile_obligations_from_behavior_ir(ir)["obligations"]
        if item["risk_family"] == "validation"
        and item["property"].get("entity_ref") == "entity-resource"
    )

    assert obligation["property"]["operation_ref"] == "op-resource-write"
    assert obligation["property"]["actor_ref"] == "actor-writer"
    assert obligation["required_actors"] == ["actor-writer"]
    assert obligation["relation_refs"] == [
        "relation-permits-writer",
        "relation-produces-resource",
    ]


def test_validation_experiment_resolves_source_permitted_actor_when_obligation_omits_one() -> None:
    operation = {
        "id": "op-resource-write",
        "method": "POST",
        "path": "/resources",
        "read_write": "write",
        "request_example": {"quantity": 1},
        "request_schema": {
            "type": "object",
            "required": ["quantity"],
            "properties": {"quantity": {"type": "integer"}},
        },
    }
    ir = {
        "operations": [
            operation,
            {"id": "op-resource-list", "method": "GET", "path": "/resources", "read_write": "read"},
            {"id": "op-resource-delete", "method": "DELETE", "path": "/resources/{id}", "read_write": "write"},
        ],
        "actors": [{
            "id": "actor-writer",
            "role": "writer",
            "account_ref": "writer_a",
            "credential_secret_ref": "secret_ref:test_accounts:writer_a",
            "runtime_bound": True,
        }],
        "relations": [{
            "id": "relation-permit-writer",
            "relation_type": "permits",
            "from_ref": "actor-writer",
            "to_ref": "op-resource-write",
            "operation_ref": "op-resource-write",
            "actor_ref": "actor-writer",
            "status": "accepted",
            "source_refs": [],
        }],
        "conflicts": [],
    }
    obligation = {
        "obligation_id": "obl-validation-source-actor",
        "risk_family": "validation",
        "property": {
            "template": "schema_constraint",
            "operation_ref": "op-resource-write",
            "source_intent": "quantity must be non-negative",
        },
        "required_actors": [],
        "required_operations": ["op-resource-write"],
        "required_fixtures": [],
        "required_observers": ["http_response"],
        "cleanup_requirement": {
            "required": True,
            "mode": "reverse_order",
            "operation_ref": "op-resource-delete",
        },
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    assert experiment["treatment_plan"][0]["actor_ref"] == "actor-writer"


def test_exact_identity_delete_is_valid_create_compensation_without_relation() -> None:
    ir = empty_behavior_ir(project_id="cleanup-relation-test")
    ir.update({
        "operations": [
            {"id": "create-resource", "method": "POST", "path": "/resources", "read_write": "write"},
            {"id": "delete-resource", "method": "DELETE", "path": "/resources/{id}", "read_write": "write"},
        ],
        "actors": [
            {
                "id": "actor-control",
                "role": "control",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:test_accounts:control",
                "allowed_resources": ["resources"],
                "allowed_actions": ["create"],
            },
            {
                "id": "actor-treatment",
                "role": "treatment",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:test_accounts:treatment",
                "allowed_resources": ["other"],
                "allowed_actions": ["read"],
            },
        ],
        "relations": [
            _relation("relation-permit", "permits", "actor-control", "create-resource", operation_ref="create-resource", actor_ref="actor-control"),
            _relation("relation-deny", "denies", "actor-treatment", "create-resource", operation_ref="create-resource", actor_ref="actor-treatment"),
        ],
    })

    obligation = next(
        item
        for item in compile_obligations_from_behavior_ir(ir)["obligations"]
        if item["risk_family"] == "authorization"
        and item["property"].get("operation_ref") == "create-resource"
    )

    assert obligation["cleanup_requirement"]["operation_ref"] == "delete-resource"


def test_authorization_pair_comes_from_relations_not_actor_array_permissions() -> None:
    ir = empty_behavior_ir(project_id="authorization-relation-test")
    ir.update({
        "operations": [{"id": "op-create", "method": "POST", "path": "/resources", "read_write": "write"}],
        "actors": [
            {"id": "actor-order-trap", "role": "trap", "account_status": "active", "allowed_resources": ["resources"], "allowed_actions": ["create"]},
            {
                "id": "actor-control",
                "role": "control",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:test_accounts:control",
                "allowed_resources": [],
                "allowed_actions": [],
            },
            {
                "id": "actor-treatment",
                "role": "treatment",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:test_accounts:treatment",
                "allowed_resources": [],
                "allowed_actions": [],
            },
        ],
        "relations": [
            _relation("relation-control", "permits", "actor-control", "op-create", operation_ref="op-create", actor_ref="actor-control"),
            _relation("relation-treatment", "denies", "actor-treatment", "op-create", operation_ref="op-create", actor_ref="actor-treatment"),
        ],
    })

    obligation = next(
        item
        for item in compile_obligations_from_behavior_ir(ir)["obligations"]
        if item["risk_family"] == "authorization"
    )

    assert obligation["property"]["control_actor_ref"] == "actor-control"
    assert obligation["property"]["treatment_actor_ref"] == "actor-treatment"


def test_isolation_pair_comes_from_explicit_ownership_relations() -> None:
    ir = empty_behavior_ir(project_id="isolation-relation-test")
    ir.update({
        "operations": [{"id": "op-read-owned", "method": "GET", "path": "/resources/{id}", "read_write": "read"}],
        "actors": [
            {"id": "actor-order-trap", "role": "member", "account_ref": "a", "account_status": "active"},
            {"id": "actor-owner", "role": "member", "account_ref": "b", "account_status": "active"},
            {"id": "actor-viewer", "role": "member", "account_ref": "c", "account_status": "active"},
        ],
        "relations": [
            _relation("relation-owner", "owns", "actor-owner", "op-read-owned", operation_ref="op-read-owned", actor_ref="actor-owner"),
            _relation("relation-viewer", "owns", "actor-viewer", "op-read-owned", operation_ref="op-read-owned", actor_ref="actor-viewer"),
        ],
    })

    obligation = next(
        item
        for item in compile_obligations_from_behavior_ir(ir)["obligations"]
        if item["risk_family"] == "isolation"
    )

    assert set(obligation["required_actors"]) == {"actor-owner", "actor-viewer"}
    assert "actor-order-trap" not in obligation["required_actors"]


def test_obligation_compiler_generic_templates() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        _sample_asset(),
        project_id="proj-a",
        runtime_actors=[
            {"role": "owner_role", "account_ref": "owner_a", "secret_ref": "secret_ref:test_accounts:owner_a"},
            {"role": "owner_role", "account_ref": "owner_b", "secret_ref": "secret_ref:test_accounts:owner_b"},
            {"role": "viewer_role", "account_ref": "viewer_a", "secret_ref": "secret_ref:test_accounts:viewer_a"},
        ],
    )
    compiled = compile_obligations_from_behavior_ir(ir)
    assert compiled["obligation_count"] > 0
    families = {item["risk_family"] for item in compiled["obligations"]}
    assert "authorization" in families
    assert "isolation" in families
    # Obligations reference IR ids, not free-form industry endpoints as logic keys
    for obl in compiled["obligations"]:
        assert obl["obligation_id"].startswith("obl_")
        assert obl["compile_status"] == "PENDING"


def test_obligation_compiler_uses_unique_documented_create_compensation() -> None:
    ir = empty_behavior_ir(project_id="compensation-test")
    ir.update({
        "model_id": "bir-generic-create-delete",
        "operations": [
            {"id": "create_resource", "method": "POST", "path": "/api/resources", "read_write": "write"},
            {"id": "delete_resource", "method": "DELETE", "path": "/api/resources/:id", "read_write": "write"},
        ],
        "actors": [
            {
                "id": "resource_operator",
                "role": "operator",
                "credential_secret_ref": "secret_ref:test_accounts:operator",
                "account_status": "active",
                "allowed_resources": ["resources"],
                "allowed_actions": ["create", "delete"],
            },
            {
                "id": "resource_viewer",
                "role": "viewer",
                "credential_secret_ref": "secret_ref:test_accounts:viewer",
                "account_status": "active",
                "allowed_resources": ["other"],
                "allowed_actions": ["read"],
            },
        ],
        "relations": [
            _relation("permit-create", "permits", "resource_operator", "create_resource", operation_ref="create_resource", actor_ref="resource_operator"),
            _relation("deny-create", "denies", "resource_viewer", "create_resource", operation_ref="create_resource", actor_ref="resource_viewer"),
            _relation("compensate-create", "compensates", "delete_resource", "create_resource", operation_ref="delete_resource"),
        ],
    })

    compiled = compile_obligations_from_behavior_ir(ir)
    auth_obligation = next(
        item for item in compiled["obligations"]
        if item.get("property", {}).get("operation_ref") == "create_resource"
        and item["risk_family"] == "authorization"
    )
    write_effect_obligations = [
        item for item in compiled["obligations"]
        if item.get("property", {}).get("operation_ref") == "create_resource"
        and item["risk_family"] in {"idempotency", "concurrency"}
    ]

    assert auth_obligation["cleanup_requirement"]["operation_ref"] == "delete_resource"
    assert auth_obligation["required_actors"] == ["resource_operator", "resource_viewer"]
    assert write_effect_obligations == []


def test_behavior_ir_does_not_infer_action_compensation_from_path_name() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "interfaces": [
                {
                    "interface_id": "api:POST:/orders",
                    "operation_id": "create_order",
                    "method": "POST",
                    "path": "/orders",
                    "source_id": "api-doc",
                },
                {
                    "interface_id": "api:POST:/orders/:id/cancel",
                    "operation_id": "cancel_order",
                    "method": "POST",
                    "path": "/orders/:id/cancel",
                    "source_id": "api-doc",
                },
            ],
            "rules": [
                {
                    "rule_id": "rule-order-idempotency",
                    "kind": "idempotency",
                    "statement": "Creating the same order request must not create duplicate business effects.",
                    "operation_ref": "create_order",
                }
            ],
            "relationships": [
                {
                    "edge_id": "edge-order-idempotency",
                    "from": "rule-order-idempotency",
                    "to": "api:POST:/orders",
                    "relation": "rule_to_interface",
                    "status": "accepted",
                }
            ],
        },
        project_id="action-compensation-test",
        runtime_actors=[
            {
                "role": "operator",
                "account_ref": "operator_a",
                "secret_ref": "secret_ref:test_accounts:operator_a",
            }
        ],
    )
    operations = {operation["operation_id"]: operation for operation in ir["operations"]}

    assert not any(
        relation["relation_type"] == "compensates"
        and relation["from_ref"] == operations["cancel_order"]["id"]
        and relation["to_ref"] == operations["create_order"]["id"]
        and relation["operation_ref"] == operations["cancel_order"]["id"]
        for relation in ir["relations"]
    )
    obligations = compile_obligations_from_behavior_ir(ir)["obligations"]
    idempotency = next(
        obligation
        for obligation in obligations
        if obligation["risk_family"] == "idempotency"
    )
    assert "operation_ref" not in idempotency["cleanup_requirement"]


def test_obligation_compiler_requires_source_invariant_for_write_effect_family() -> None:
    ir = empty_behavior_ir(project_id="grounded-write-effect-test")
    ir.update({
        "model_id": "bir-grounded-write-effect",
        "operations": [
            {"id": "create_resource", "method": "POST", "path": "/api/resources", "read_write": "write"},
            {"id": "delete_resource", "method": "DELETE", "path": "/api/resources/:id", "read_write": "write"},
        ],
        "invariants": [{
            "id": "inv_create_once",
            "expression": {"kind": "idempotency", "operator": "must_hold"},
            "source_refs": [{"source_id": "prd", "kind": "business_rule"}],
            "confidence": 0.9,
            "status": "accepted",
        }],
        "relations": [
            _relation(
                "observe-create-once",
                "observes",
                "create_resource",
                "inv_create_once",
                operation_ref="create_resource",
            ),
            _relation(
                "compensate-create",
                "compensates",
                "delete_resource",
                "create_resource",
                operation_ref="delete_resource",
            ),
        ],
    })

    obligations = compile_obligations_from_behavior_ir(ir)["obligations"]
    grounded = [item for item in obligations if item["risk_family"] == "idempotency"]

    assert len(grounded) == 1
    assert grounded[0]["property"]["operation_ref"] == "create_resource"
    assert grounded[0]["required_observers"] == ["business_effect", "http_response"]
    assert grounded[0]["cleanup_requirement"]["operation_ref"] == "delete_resource"


def test_obligation_compiler_does_not_guess_ambiguous_compensation() -> None:
    ir = empty_behavior_ir(project_id="ambiguous-compensation-test")
    ir.update({
        "model_id": "bir-ambiguous-create-delete",
        "operations": [
            {"id": "create_resource", "method": "POST", "path": "/api/resources", "read_write": "write"},
            {"id": "delete_resource_by_id", "method": "DELETE", "path": "/api/resources/:id", "read_write": "write"},
            {"id": "delete_resource_by_key", "method": "DELETE", "path": "/api/resources/:key", "read_write": "write"},
        ],
        "actors": [
            {
                "id": "resource_operator",
                "role": "operator",
                "credential_secret_ref": "secret_ref:test_accounts:operator",
                "account_status": "active",
                "allowed_resources": ["resources"],
                "allowed_actions": ["create", "delete"],
            },
            {
                "id": "resource_viewer",
                "role": "viewer",
                "credential_secret_ref": "secret_ref:test_accounts:viewer",
                "account_status": "active",
                "allowed_resources": ["other"],
                "allowed_actions": ["read"],
            },
        ],
        "relations": [
            _relation("permit-create", "permits", "resource_operator", "create_resource", operation_ref="create_resource", actor_ref="resource_operator"),
            _relation("deny-create", "denies", "resource_viewer", "create_resource", operation_ref="create_resource", actor_ref="resource_viewer"),
            _relation("compensate-create-id", "compensates", "delete_resource_by_id", "create_resource", operation_ref="delete_resource_by_id"),
            _relation("compensate-create-key", "compensates", "delete_resource_by_key", "create_resource", operation_ref="delete_resource_by_key"),
        ],
    })

    compiled = compile_obligations_from_behavior_ir(ir)
    create_obligation = next(
        item for item in compiled["obligations"]
        if item.get("property", {}).get("operation_ref") == "create_resource"
        and item["risk_family"] == "authorization"
    )

    assert "operation_ref" not in create_obligation["cleanup_requirement"]


def test_authorization_obligation_uses_source_permissions_not_actor_order() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        _sample_asset(),
        project_id="proj-a",
        runtime_actors=[
            {"role": "owner_role", "account_ref": "owner_a", "secret_ref": "secret_ref:test_accounts:owner_a"},
            {"role": "viewer_role", "account_ref": "viewer_a", "secret_ref": "secret_ref:test_accounts:viewer_a"},
        ],
    )
    compiled = compile_obligations_from_behavior_ir(ir)
    actors = {item["id"]: item for item in ir["actors"]}
    ops = {item["id"]: item for item in ir["operations"]}
    update_op = next(item for item in ir["operations"] if item.get("operation_id") == "update_resource")
    auth_obl = next(
        item for item in compiled["obligations"]
        if item["risk_family"] == "authorization"
        and item["property"].get("operation_ref") == update_op["id"]
    )
    control = actors[auth_obl["property"]["control_actor_ref"]]
    treatment = actors[auth_obl["property"]["treatment_actor_ref"]]
    assert control["role"] == "owner_role"
    assert treatment["role"] == "viewer_role"
    assert ops[auth_obl["property"]["operation_ref"]]["operation_id"] == "update_resource"


def test_experiment_compiler_blocks_missing_binding_without_declared_read_resolver() -> None:
    asset = _sample_asset()
    asset["operations"] = [
        operation
        for operation in asset["operations"]
        if operation["operation_id"] != "list_resources"
    ]
    ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="proj-a",
        runtime_actors=[
            {"role": "viewer_role", "account_ref": "viewer_a", "secret_ref": "secret_ref:test_accounts:viewer_a"},
            {"role": "owner_role", "account_ref": "owner_a", "secret_ref": "secret_ref:test_accounts:owner_a"},
        ],
    )
    compiled = compile_obligations_from_behavior_ir(ir)
    write_op = next(op for op in ir["operations"] if "{id}" in str(op.get("path")))
    actors = [actor for actor in ir["actors"] if actor.get("account_ref")]
    write_obl = {
        "obligation_id": "obl_test_binding",
        "risk_family": "idempotency",
        "subject_refs": [write_op["id"]],
        "property": {"template": "idempotent_effect_cardinality", "operation_ref": write_op["id"]},
        "required_actors": [actors[0]["id"]] if actors else [],
        "required_operations": [write_op["id"]],
        "required_fixtures": [],
        "required_observers": ["http_response", "business_effect"],
        "cleanup_requirement": {"required": True, "mode": "reverse_order"},
        "source_refs": [],
        "confidence": 0.5,
        "compile_status": "PENDING",
    }
    experiment = compile_experiment_for_obligation(
        write_obl,
        behavior_ir=ir,
        environment_type="test",
    )
    receipt = experiment["compile_receipt"]
    # With generated test values as fallback, unresolvable bindings no longer
    # block compilation. The next gate (observer availability) may still block.
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] in {"BLOCKED_MISSING_BINDING", "BLOCKED_MISSING_OBSERVER"}


def test_experiment_compiler_uses_unique_source_declared_action_compensator() -> None:
    request_example = {"resourceId": "<resource_id>", "quantity": 1}
    request_schema = {
        "type": "object",
        "required": ["resourceId", "quantity"],
        "properties": {
            "resourceId": {"type": "string"},
            "quantity": {"type": "integer"},
        },
    }
    operations = [
        {
            "id": "reserve_capacity",
            "method": "POST",
            "path": "/api/capacity/reserve",
            "read_write": "write",
            "summary": "Reserve capacity",
            "request_example": request_example,
            "request_schema": request_schema,
            "source_refs": [{"source_id": "api", "locator": "POST /api/capacity/reserve"}],
        },
        {
            "id": "release_capacity",
            "method": "POST",
            "path": "/api/capacity/release",
            "read_write": "write",
            "summary": "Release reserved capacity",
            "request_example": request_example,
            "request_schema": request_schema,
            "source_refs": [{"source_id": "api", "locator": "POST /api/capacity/release"}],
        },
        {
            "id": "read_capacity",
            "method": "GET",
            "path": "/api/capacity/{resourceId}",
            "read_write": "read",
            "source_refs": [{"source_id": "api", "locator": "GET /api/capacity/{resourceId}"}],
        },
        {
            "id": "list_resources",
            "method": "GET",
            "path": "/api/resources",
            "read_write": "read",
            "source_refs": [{"source_id": "api", "locator": "GET /api/resources"}],
            "entity_refs": ["entity-resource"],
        },
    ]
    obligation = {
        "obligation_id": "obl_source_compensation",
        "risk_family": "validation",
        "property": {
            "template": "schema_constraint",
            "operation_ref": "reserve_capacity",
            "actor_ref": "operator",
            "source_intent": "quantity must be non-negative",
        },
        "required_actors": ["operator"],
        "required_operations": ["reserve_capacity"],
        "required_fixtures": [],
        "required_observers": ["http_response"],
        "cleanup_requirement": {"required": True, "mode": "reverse_order"},
        "source_refs": [{"source_id": "rule", "locator": "capacity reservation"}],
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir={
            "operations": operations,
            "actors": [{"id": "operator", "role": "public"}],
            "entities": [{"id": "entity-resource", "name": "resource"}],
            "body_reference_relations": [{
                "operation_ref": "reserve_capacity",
                "body_path": "resourceId",
                "target_entity_ref": "entity-resource",
                "status": "RESOLVED",
                "source_refs": [{
                    "kind": "database_foreign_key",
                    "locator": "reservation.resource_id -> resources.id",
                }],
            }],
            "relations": [{
                "id": "rel-reserve-release",
                "kind": "compensates",
                "source": "reserve_capacity",
                "target": "release_capacity",
                "source_refs": [{"source_id": "api"}],
            }],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    assert experiment["cleanup_plan"] == [
        {
            "action": "source_declared_compensation",
            "mode": "compensating_transition",
            "operation_ref": "release_capacity",
            "compensates_operation_ref": "reserve_capacity",
            "path": "/api/capacity/release",
            "method": "POST",
            "body_from_original_request": True,
            "runtime_response_binding_required": False,
            "source_step_id": "treatment_1",
        },
        {
            "action": "source_declared_compensation",
            "mode": "compensating_transition",
            "operation_ref": "release_capacity",
            "compensates_operation_ref": "reserve_capacity",
            "path": "/api/capacity/release",
            "method": "POST",
            "body_from_original_request": True,
            "runtime_response_binding_required": False,
            "source_step_id": "control_1",
        },
    ]


def test_recreate_cleanup_reuses_compensator_primary_request_body() -> None:
    """release primary → reserve cleanup must reuse bound primary bodies."""
    request_example = {"sku": "SKU-1", "qty": 1, "orderId": "<order_id>"}
    request_schema = {
        "type": "object",
        "required": ["sku", "qty", "orderId"],
        "properties": {
            "sku": {"type": "string"},
            "qty": {"type": "integer"},
            "orderId": {"type": "string"},
        },
    }
    operations = [
        {
            "id": "reserve_stock",
            "method": "POST",
            "path": "/api/inventory/reserve",
            "read_write": "write",
            "request_example": request_example,
            "request_schema": request_schema,
            "source_refs": [{"source_id": "api", "locator": "POST /api/inventory/reserve"}],
        },
        {
            "id": "release_stock",
            "method": "POST",
            "path": "/api/inventory/release",
            "read_write": "write",
            "request_example": request_example,
            "request_schema": request_schema,
            "source_refs": [{"source_id": "api", "locator": "POST /api/inventory/release"}],
        },
        {
            "id": "read_stock",
            "method": "GET",
            "path": "/api/inventory/{sku}",
            "read_write": "read",
            "source_refs": [{"source_id": "api", "locator": "GET /api/inventory/{sku}"}],
        },
        {
            "id": "list_orders",
            "method": "GET",
            "path": "/api/orders",
            "read_write": "read",
            "source_refs": [{"source_id": "api", "locator": "GET /api/orders"}],
            "entity_refs": ["entity-order"],
        },
        {
            "id": "create_order",
            "method": "POST",
            "path": "/api/orders",
            "read_write": "write",
            "request_example": {"sku": "SKU-1", "qty": 1},
            "source_refs": [{"source_id": "api", "locator": "POST /api/orders"}],
        },
    ]
    obligation = {
        "obligation_id": "obl_recreate_release",
        "risk_family": "validation",
        "property": {
            "template": "schema_constraint",
            "operation_ref": "release_stock",
            "actor_ref": "operator",
            "source_intent": "qty must be non-negative",
        },
        "required_actors": ["operator"],
        "required_operations": ["release_stock"],
        "required_fixtures": [],
        "required_observers": ["http_response"],
        "cleanup_requirement": {
            "required": True,
            "mode": "recreate_compensated_resource",
            "operation_ref": "reserve_stock",
        },
        "source_refs": [{"source_id": "rule", "locator": "inventory release"}],
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir={
            "operations": operations,
            "actors": [{"id": "operator", "role": "public"}],
            "entities": [{"id": "entity-order", "name": "order"}],
            "body_reference_relations": [{
                "operation_ref": "release_stock",
                "body_path": "orderId",
                "target_entity_ref": "entity-order",
                "status": "RESOLVED",
                "source_refs": [{
                    "kind": "database_foreign_key",
                    "locator": "inventory.order_id -> orders.id",
                }],
            }],
            "relations": [{
                "id": "rel-release-reserve",
                "kind": "compensates",
                "source": "release_stock",
                "target": "reserve_stock",
                "source_refs": [{"source_id": "api"}],
            }],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    assert experiment["cleanup_plan"] == [
        {
            "action": "source_declared_compensation",
            "mode": "recreate_compensated_resource",
            "operation_ref": "reserve_stock",
            "compensates_operation_ref": "release_stock",
            "path": "/api/inventory/reserve",
            "method": "POST",
            "body_from_original_request": True,
            "runtime_response_binding_required": False,
            "source_step_id": "treatment_1",
        },
        {
            "action": "source_declared_compensation",
            "mode": "recreate_compensated_resource",
            "operation_ref": "reserve_stock",
            "compensates_operation_ref": "release_stock",
            "path": "/api/inventory/reserve",
            "method": "POST",
            "body_from_original_request": True,
            "runtime_response_binding_required": False,
            "source_step_id": "control_1",
        },
    ]


def test_relation_bound_compensation_reuses_original_request_body() -> None:
    """cleanup_requirement.operation_ref must not emit empty-body reverse_order."""
    request_example = {"resourceId": "resource-1", "units": 1}
    request_schema = {
        "type": "object",
        "required": ["resourceId", "units"],
        "properties": {
            "resourceId": {"type": "string"},
            "units": {"type": "integer"},
        },
    }
    operations = [
        {
            "id": "reserve_capacity",
            "method": "POST",
            "path": "/api/capacity/reserve",
            "read_write": "write",
            "request_example": request_example,
            "request_schema": request_schema,
            "source_refs": [{"source_id": "api", "locator": "POST /api/capacity/reserve"}],
        },
        {
            "id": "release_capacity",
            "method": "POST",
            "path": "/api/capacity/release",
            "read_write": "write",
            "request_example": request_example,
            "request_schema": request_schema,
            "source_refs": [{"source_id": "api", "locator": "POST /api/capacity/release"}],
        },
        {
            "id": "read_capacity",
            "method": "GET",
            "path": "/api/capacity/{resourceId}",
            "read_write": "read",
            "source_refs": [{"source_id": "api", "locator": "GET /api/capacity/{resourceId}"}],
        },
    ]
    obligation = {
        "obligation_id": "obl_relation_compensation",
        "risk_family": "concurrency",
        "property": {
            "template": "concurrent_final_invariant",
            "operation_ref": "reserve_capacity",
            "actor_ref": "operator",
            "insufficient_signal": "dual_2xx_alone",
        },
        "required_actors": ["operator"],
        "required_operations": ["reserve_capacity"],
        "required_fixtures": [],
        "required_observers": ["final_state", "barrier_timeline"],
        "cleanup_requirement": {
            "required": True,
            "mode": "reverse_order",
            "operation_ref": "release_capacity",
        },
        "source_refs": [{"source_id": "rule", "locator": "capacity reservation"}],
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir={
            "operations": operations,
            "actors": [{"id": "operator", "role": "public"}],
            "relations": [{
                "id": "rel-reserve-release",
                "kind": "compensates",
                "source": "reserve_capacity",
                "target": "release_capacity",
                "source_refs": [{"source_id": "api"}],
            }],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    assert experiment["cleanup_plan"] == [
        {
            "action": "source_declared_compensation",
            "mode": "compensating_transition",
            "operation_ref": "release_capacity",
            "compensates_operation_ref": "reserve_capacity",
            "path": "/api/capacity/release",
            "method": "POST",
            "body_from_original_request": True,
            "runtime_response_binding_required": False,
            "source_step_id": "treatment_1",
        },
        {
            "action": "source_declared_compensation",
            "mode": "compensating_transition",
            "operation_ref": "release_capacity",
            "compensates_operation_ref": "reserve_capacity",
            "path": "/api/capacity/release",
            "method": "POST",
            "body_from_original_request": True,
            "runtime_response_binding_required": False,
            "source_step_id": "control_1",
        },
    ]


def test_write_without_concrete_compensation_is_blocked_before_execution() -> None:
    operation = {
        "id": "consume_capacity",
        "method": "POST",
        "path": "/api/capacity/consume",
        "read_write": "write",
        "request_example": {"resourceId": "resource-1", "units": 1},
        "request_schema": {
            "type": "object",
            "required": ["resourceId", "units"],
            "properties": {
                "resourceId": {"type": "string"},
                "units": {"type": "integer"},
            },
        },
    }
    obligation = {
        "obligation_id": "obl_non_reversible_write",
        "risk_family": "validation",
        "property": {
            "operation_ref": "consume_capacity",
            "actor_ref": "operator",
        },
        "required_actors": ["operator"],
        "required_operations": ["consume_capacity"],
        "required_fixtures": [],
        "required_observers": ["http_response"],
        "cleanup_requirement": {"required": True},
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir={
            "operations": [
                operation,
                {
                    "id": "read_capacity",
                    "method": "GET",
                    "path": "/api/capacity/{resourceId}",
                    "read_write": "read",
                },
            ],
            "actors": [{"id": "operator", "role": "public"}],
            "relations": [],
            "conflicts": [],
        },
        environment_type="test",
    )

    # Production targets stay fail-closed before any cleanup tier resolves
    # (the adapter gate blocks earlier than the residue decision).
    assert experiment["compile_receipt"]["status"] == "BLOCKED"


def test_missing_compensator_never_downgrades_required_write_cleanup() -> None:
    requirement = _cleanup_requirement(
        {
            "id": "write_resource",
            "method": "POST",
            "path": "/resources",
            "read_write": "write",
        },
        [],
        [],
    )

    assert requirement == {"required": True, "mode": "reverse_order"}


def test_cleanup_requirement_binds_recreate_when_primary_is_compensator() -> None:
    operations = [
        {
            "id": "create_item",
            "method": "POST",
            "path": "/api/cart/items",
            "read_write": "write",
        },
        {
            "id": "delete_item",
            "method": "DELETE",
            "path": "/api/cart/items/{id}",
            "read_write": "write",
        },
        {
            "id": "patch_product",
            "method": "PATCH",
            "path": "/api/products/admin/{sku}",
            "read_write": "write",
        },
        {
            "id": "create_product",
            "method": "POST",
            "path": "/api/products/admin",
            "read_write": "write",
        },
        {
            "id": "delete_product",
            "method": "DELETE",
            "path": "/api/products/admin/{sku}",
            "read_write": "write",
        },
    ]
    relations = [
        {
            "relation_type": "compensates",
            "from_ref": "delete_item",
            "to_ref": "create_item",
            "operation_ref": "delete_item",
        },
        {
            "relation_type": "compensates",
            "from_ref": "delete_product",
            "to_ref": "create_product",
            "operation_ref": "delete_product",
        },
        {
            "relation_type": "compensates",
            "from_ref": "delete_product",
            "to_ref": "patch_product",
            "operation_ref": "delete_product",
        },
    ]

    delete_item = _cleanup_requirement(operations[1], operations, relations)
    assert delete_item["operation_ref"] == "create_item"
    assert delete_item["mode"] == "recreate_compensated_resource"

    delete_product = _cleanup_requirement(operations[4], operations, relations)
    assert delete_product["operation_ref"] == "create_product"
    assert delete_product["mode"] == "recreate_compensated_resource"

    consume = _cleanup_requirement(
        {
            "id": "consume",
            "method": "POST",
            "path": "/api/inventory/consume",
            "read_write": "write",
        },
        operations,
        relations,
    )
    assert consume["required"] is True
    assert "operation_ref" not in consume


def test_action_shape_and_cleanup_name_do_not_invent_compensation_relation() -> None:
    request_example = {"resourceId": "resource-1", "units": 1}
    source = {
        "id": "consume_capacity",
        "method": "POST",
        "path": "/api/capacity/consume",
        "summary": "Consume capacity",
        "request_example": request_example,
        "source_refs": [{"source_id": "api", "locator": "POST /api/capacity/consume"}],
    }
    candidate = {
        "id": "release_capacity",
        "method": "POST",
        "path": "/api/capacity/release",
        "summary": "Release reserved capacity",
        "request_example": request_example,
        "source_refs": [{"source_id": "api", "locator": "POST /api/capacity/release"}],
    }

    assert declared_action_compensators(
        source,
        behavior_ir={
            "operations": [
                source,
                candidate,
                {
                    "id": "read_capacity",
                    "method": "GET",
                    "path": "/api/capacity/{resourceId}",
                },
            ]
        },
    ) == []


def test_colon_path_params_compile_with_source_declared_runtime_resolver() -> None:
    asset = _sample_asset()
    asset["operations"] = [
        {
            "operation_id": "read_resources",
            "method": "GET",
            "path": "/resources",
            "side_effect_class": "read",
        },
        {
            "operation_id": "read_resource_by_id",
            "method": "GET",
            "path": "/resources/:id",
            "side_effect_class": "read",
        },
    ]
    ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="proj-a",
        runtime_actors=[
            {"role": "viewer_role", "account_ref": "viewer_a", "secret_ref": "secret_ref:test_accounts:viewer_a", "tenant_ref": "tenant-a"},
            {"role": "viewer_role", "account_ref": "viewer_b", "secret_ref": "secret_ref:test_accounts:viewer_b", "tenant_ref": "tenant-b"},
        ],
    )
    target_op = next(op for op in ir["operations"] if str(op.get("path")) == "/resources/:id")
    runtime_actor_ids = [
        actor["id"]
        for actor in ir["actors"]
        if actor.get("account_ref")
    ]
    # Tenant isolation requires same-role actors with DISTINCT tenant coordinates.
    # Patch the generated actors to carry distinct tenant_ref values.
    for idx, actor in enumerate(ir["actors"]):
        if actor.get("account_ref"):
            actor["tenant_ref"] = f"tenant-{idx}"
            actor.pop("tenant_scope", None)
    obligation = {
        "obligation_id": "obl_colon_path_binding",
        "risk_family": "isolation",
        "subject_refs": [target_op["id"]],
        "property": {"template": "tenant_isolation", "operation_ref": target_op["id"]},
        "required_actors": runtime_actor_ids[:2],
        "required_operations": [target_op["id"]],
        "required_fixtures": [],
        "required_observers": ["http_response"],
        "cleanup_requirement": {"required": False},
        "source_refs": [],
        "confidence": 0.9,
        "compile_status": "PENDING",
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )
    receipt = experiment["compile_receipt"]

    assert extract_placeholders("/resources/:id") == ["id"]
    assert receipt["status"] == "COMPILED"
    runtime_binding = next(
        item for item in experiment["binding_plan"]
        if item.get("target") == "id"
    )
    assert runtime_binding["status"] == "runtime_resolvable"
    assert runtime_binding["source_priority"] == "same_actor_list_read"
    assert runtime_binding["resolver_operations"] == [{
        "operation_ref": next(
            operation["id"]
            for operation in ir["operations"]
            if operation.get("operation_id") == "read_resources"
        ),
        "method": "GET",
        "path": "/resources",
    }]
    plan = plan_obligation_round(
        [obligation],
        experiments_by_obligation={obligation["obligation_id"]: experiment},
        budget=5,
    )
    assert plan["selected_count"] == 1


def test_runtime_binding_plan_includes_governed_fixture_setup_from_declared_operations() -> None:
    asset = _sample_asset()
    asset["permission_matrix"] = [
        {"role": "viewer_role", "resource": "resource", "actions": ["read"], "scope": "own"},
        {"role": "owner_role", "resource": "resource", "actions": ["read", "write"], "scope": "own"},
    ]
    asset["operations"] = [
        {
            "operation_id": "list_resources",
            "method": "GET",
            "path": "/api/resources",
            "side_effect_class": "read",
        },
        {
            "operation_id": "read_resource_projection",
            "method": "GET",
            "path": "/api/projections/resource/:resourceId",
            "side_effect_class": "read",
        },
        {
            "operation_id": "list_owners",
            "method": "GET",
            "path": "/api/owners",
            "side_effect_class": "read",
        },
        {
            "operation_id": "create_resource",
            "method": "POST",
            "path": "/api/resources",
            "side_effect_class": "write",
            "request_schema": {
                "content": {
                    "application/json": {
                        "example": {"ownerId": "<owner_id>", "name": "source-name"},
                    },
                },
            },
        },
        {
                "operation_id": "delete_resource",
                "method": "DELETE",
                "path": "/api/resources/:id",
                "side_effect_class": "write",
            },
    ]
    ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="proj-a",
        runtime_actors=[
            {"role": "owner_role", "account_ref": "owner_a", "secret_ref": "secret_ref:test_accounts:owner_a"},
            {"role": "viewer_role", "account_ref": "viewer_a", "secret_ref": "secret_ref:test_accounts:viewer_a"},
        ],
    )
    target = next(op for op in ir["operations"] if op.get("operation_id") == "read_resource_projection")
    runtime_actors_by_role = {
        actor["role"]: actor
        for actor in ir["actors"]
        if actor.get("account_ref")
    }
    control_actor = runtime_actors_by_role["owner_role"]
    treatment_actor = runtime_actors_by_role["viewer_role"]
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl_fixture_backed_binding",
            "risk_family": "authorization",
            "property": {
                "template": "authorization_control_treatment",
                "operation_ref": target["id"],
                "control_actor_ref": control_actor["id"],
                "treatment_actor_ref": treatment_actor["id"],
            },
            "required_actors": [control_actor["id"], treatment_actor["id"]],
            "required_operations": [target["id"]],
            "required_fixtures": [],
            "required_observers": ["http_response"],
            "cleanup_requirement": {"required": False},
            "source_refs": [],
        },
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED"
    binding = next(item for item in experiment["binding_plan"] if item.get("target") == "resourceId")
    fixture_setup = binding["fixture_setup"]
    assert fixture_setup["method"] == "POST"
    assert fixture_setup["path"] == "/api/resources"
    fixture_actors = {actor["id"]: actor for actor in ir["actors"]}
    assert fixture_setup["actor_refs"]
    assert all(
        "write" in fixture_actors[actor_ref]["allowed_actions"]
        for actor_ref in fixture_setup["actor_refs"]
    )
    assert fixture_setup["body_template"] == {"ownerId": "<owner_id>", "name": "source-name"}
    assert fixture_setup["body_bindings"] == [{
        "target": "ownerId",
        "template_token": "owner_id",
        "resolver_operations": [{
            "operation_ref": next(
                op["id"] for op in ir["operations"] if op.get("operation_id") == "list_owners"
            ),
            "method": "GET",
            "path": "/api/owners",
        }],
    }]
    assert fixture_setup["cleanup_operations"] == [{
        "operation_ref": next(
            op["id"] for op in ir["operations"] if op.get("operation_id") == "delete_resource"
        ),
        "method": "DELETE",
        "path": "/api/resources/{id}",
    }]


def test_runtime_binding_plan_deduplicates_merged_cleanup_operations() -> None:
    target = {"id": "read_projection", "method": "GET", "path": "/api/projections/resource/{resourceId}"}
    create = {
        "id": "create_resource",
        "method": "POST",
        "path": "/api/resources",
        "request_schema": {
            "content": {"application/json": {"example": {"name": "source-name"}}},
        },
    }
    cleanup = {"id": "delete_resource", "method": "DELETE", "path": "/api/resources/{id}"}
    plan = build_binding_plan(
        operation=target,
        obligation={"required_fixtures": []},
        behavior_ir={
            "operations": [
                {"id": "list_resources", "method": "GET", "path": "/api/resources"},
                target,
                create,
                cleanup,
                dict(cleanup),
            ],
            "actors": [{
                "id": "fixture_actor",
                "allowed_actions": ["write"],
                "allowed_resources": ["resource"],
                "credential_secret_ref": "secret_ref:test_accounts:fixture_actor",
            }],
            "relations": [{
                "id": "permit-create",
                "relation_type": "permits",
                "operation_ref": "create_resource",
                "actor_ref": "fixture_actor",
                "from_ref": "fixture_actor",
                "to_ref": "create_resource",
                "source_refs": [{"source_id": "api"}],
            }],
        },
    )

    fixture_setup = next(item for item in plan if item["target"] == "resourceId")["fixture_setup"]
    assert fixture_setup["cleanup_operations"] == [{
        "operation_ref": "delete_resource",
        "method": "DELETE",
        "path": "/api/resources/{id}",
    }]


def test_fixture_setup_requires_a_source_declared_request_example() -> None:
    from ai_test_asset_center.runtime_binding_graph import (
        _declared_fixture_setup,
    )

    target = {
        "id": "read_projection",
        "method": "GET",
        "path": "/api/projections/resource/{resourceId}",
    }
    setup = _declared_fixture_setup(
        target,
        target="resourceId",
        behavior_ir={
            "operations": [
                target,
                {
                    "id": "create_resource",
                    "method": "POST",
                    "path": "/api/resources",
                    "request_schema": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string"},
                        },
                    },
                },
                {
                    "id": "delete_resource",
                    "method": "DELETE",
                    "path": "/api/resources/{id}",
                },
            ],
            "actors": [{
                "id": "fixture_actor",
                "allowed_actions": ["write"],
                "allowed_resources": ["resource"],
                "credential_secret_ref": (
                    "secret_ref:test_accounts:fixture_actor"
                ),
            }],
        },
    )

    assert setup == {}


def test_experiment_compiler_blocks_production_environment() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        _sample_asset(),
        project_id="proj-a",
        runtime_actors=[
            {"role": "owner_role", "account_ref": "owner_a", "secret_ref": "secret_ref:test_accounts:owner_a"},
            {"role": "viewer_role", "account_ref": "viewer_a", "secret_ref": "secret_ref:test_accounts:viewer_a"},
        ],
    )
    obl = compile_obligations_from_behavior_ir(ir)["obligations"][0]
    blocked = compile_experiment_for_obligation(obl, behavior_ir=ir, environment_type="production")
    assert blocked["compile_receipt"]["status"] == "BLOCKED"
    assert blocked["compile_receipt"]["reason_code"] == "BLOCKED_UNSUPPORTED_ADAPTER"


def test_binding_priority_no_low_override() -> None:
    plan = [{"target": "id", "status": "bound", "source_priority": "experiment_setup_response", "value_fingerprint": "aaa"}]
    updated = apply_binding(
        plan,
        target="id",
        value="low",
        source_priority="api_doc_example",
    )
    assert updated[0]["source_priority"] == "experiment_setup_response"
    assert "override_rejected" in updated[0]


def test_binding_priority_rejects_unknown_or_evaluator_owned_sources() -> None:
    from ai_test_asset_center.runtime_binding_graph import BINDING_PRIORITY

    assert "schema_generated" not in BINDING_PRIORITY
    assert "evaluator_frozen_fixture" not in BINDING_PRIORITY
    with pytest.raises(ValueError, match="binding_source_priority_invalid"):
        apply_binding(
            [],
            target="id",
            value="invented",
            source_priority="unknown_source",
        )


def test_assertion_concurrency_rejects_dual_2xx_alone() -> None:
    result = evaluate_assertion(
        {"kind": "concurrency_final_invariant", "assertion_id": "a1"},
        observations={"dual_2xx": True},
    )
    assert result["status"] == "INDETERMINATE"
    assert result["passed"] is None
    assert result["reason_code"] == "FINAL_INVARIANT_MISSING"


def test_assertion_isolation_requires_control() -> None:
    result = evaluate_assertion(
        {"kind": "isolation", "assertion_id": "a2"},
        observations={
            "owner_can_access": True,
            "viewer_can_access": False,
            "leak_detected": False,
            "control_succeeded": False,
        },
    )
    assert result["status"] == "INDETERMINATE"
    assert result["passed"] is None
    assert result["reason_code"] == "AUTHORIZED_CONTROL_NOT_PROVEN"


def test_contract_oracle_harness_error_not_defect() -> None:
    verdict = evaluate_contract_oracle(
        experiment={"experiment_id": "exp-harness", "obligation_id": "obl-harness", "campaign_id": "campaign-harness", "execution_id": "execution-harness", "source_refs": [{"kind": "api_contract", "source_id": "source", "locator": "GET /resource"}], "control_plan": [{"x": 1}], "treatment_plan": [{"y": 1}], "observers": [{"observer_id": "http_response"}], "assertions": [{"kind": "http_status", "expected": 403}]},
        evidence={"harness_error": True, "control_succeeded": True, "treatment_observation": {}, "http_response": True},
    )
    assert verdict["verdict"] == "harness_failure"
    assert verdict["status"] == "HARNESS_FAILED"
    assert verdict["customer_deliverable"] is False


def test_demote_heuristic_concurrency_finding() -> None:
    finding = {
        "id": "f1",
        "oracle": {"oracle_name": "ConcurrencyOracle"},
        "evidence": {"dual_2xx": True},
        "gate_passed": True,
        "customer_delivery_status": "defect",
    }
    demoted = demote_heuristic_business_oracle_finding(finding)
    assert demoted["customer_delivery_status"] == "clue"
    assert demoted["gate_passed"] is False


def test_source_grounded_legacy_permission_oracle_stays_diagnostic() -> None:
    finding = {
        "id": "f_perm",
        "oracle": {"oracle_name": "PermissionOracle", "violated_rule": "unauthorized_access"},
        "source_refs": [{"kind": "permission_matrix", "source_id": "roles", "locator": "buyer->reports"}],
        "evidence": {
            "request": "GET /api/reports",
            "actor_token_present": True,
        },
        "before_after_snapshot": {
            "after": {"method": "GET", "path": "/api/reports", "status_code": 200, "body": {"items": [{"id": "r1"}]}}
        },
        "gate_passed": True,
        "customer_delivery_status": "defect",
    }
    demoted = demote_heuristic_business_oracle_finding(finding)
    assert demoted["customer_delivery_status"] == "clue"
    assert demoted["gate_passed"] is False
    assert demoted["oracle_demotion_reason"] == (
        "heuristic_business_oracle_without_contract"
    )


def test_ungrounded_permission_signal_still_demoted() -> None:
    finding = {
        "id": "f_perm_clue",
        "oracle": {"oracle_name": "PermissionOracle", "violated_rule": "unauthorized_access"},
        "source_refs": [{"kind": "runtime_actor", "source_id": "runtime", "locator": "buyer"}],
        "evidence": {
            "request": "GET /api/reports",
            "actor_token_present": True,
        },
        "before_after_snapshot": {
            "after": {"method": "GET", "path": "/api/reports", "status_code": 200, "body": {"items": [{"id": "r1"}]}}
        },
        "gate_passed": True,
        "customer_delivery_status": "defect",
    }
    demoted = demote_heuristic_business_oracle_finding(finding)
    assert demoted["customer_delivery_status"] == "clue"
    assert demoted["gate_passed"] is False


def test_idempotency_oracle_marks_heuristic_as_clue_tier() -> None:
    from ai_test_asset_center.oracle_engine import IdempotencyOracle

    oracle = IdempotencyOracle()
    result = oracle.evaluate(
        {"title": "dup submit"},
        {
            "steps": [
                {"method": "POST", "path": "/api/resource", "response": {"status_code": 200}},
                {"method": "POST", "path": "/api/resource", "response": {"status_code": 200}},
            ]
        },
    )
    assert result.passed is False
    assert result.customer_deliverable is False
    assert result.oracle_tier == "internal_clue"


def test_obligation_execution_projection_counts() -> None:
    from ai_test_asset_center.discovery_quality_projection import build_obligation_execution_projection

    proj = build_obligation_execution_projection(
        {
            "test_obligations": {"count": 10},
            "experiment_compile": {"compiled_count": 7, "blocked_count": 3, "block_reason_counts": {"BLOCKED_MISSING_BINDING": 3}},
            "obligation_plan": {"selected_count": 5},
            "phases": {"oracle": {"traces_with_http": 4}},
            "findings": [],
        }
    )
    assert proj["obligation_total"] == 10
    assert proj["obligation_compiled"] == 7
    assert proj["obligation_blocked"] == 3
    assert proj["obligation_executed"] == 4
    assert proj["block_reason_counts"]["BLOCKED_MISSING_BINDING"] == 3


def test_adaptive_planner_prefers_compiled() -> None:
    ir = build_behavior_ir_from_knowledge_asset(_sample_asset(), project_id="proj-a")
    obl_pack = compile_obligations_from_behavior_ir(ir)
    exp_pack = compile_experiments(
        obl_pack["obligations"],
        behavior_ir=ir,
        environment_type="test",
    )
    by_oid = {item["obligation_id"]: item for item in exp_pack["experiments"]}
    plan = plan_obligation_round(obl_pack["obligations"], experiments_by_obligation=by_oid, budget=5)
    assert plan["selected_count"] <= 5
    assert all(item.get("obligation_id") for item in plan["selected"])


def test_adaptive_planner_reports_final_family_counts_and_reserves_breadth() -> None:
    obligations = [
        {
            "obligation_id": f"obl_auth_{index}",
            "risk_family": "authorization",
            "subject_refs": [f"subject_{index}"],
            "confidence": 0.95,
            "compile_status": "COMPILED",
        }
        for index in range(5)
    ] + [
        {
            "obligation_id": "obl_state",
            "risk_family": "state",
            "subject_refs": ["state_subject"],
            "confidence": 0.6,
            "compile_status": "COMPILED",
        },
        {
            "obligation_id": "obl_concurrency",
            "risk_family": "concurrency",
            "subject_refs": ["concurrency_subject"],
            "confidence": 0.6,
            "compile_status": "COMPILED",
        },
    ]

    plan = plan_obligation_round(obligations, budget=4)

    selected_families = [item["risk_family"] for item in plan["selected"]]
    assert {"authorization", "state", "concurrency"}.issubset(selected_families)
    assert plan["family_coverage"] == {
        family: selected_families.count(family)
        for family in sorted(set(selected_families))
    }


def test_permit_only_read_emits_gap_and_permitted_invocation_obligation() -> None:
    ir = empty_behavior_ir(project_id="permit-only-read")
    ir.update({
        "operations": [{
            "id": "op-report-sales",
            "method": "GET",
            "path": "/api/reports/sales",
            "read_write": "read",
            "source_refs": [{"source_id": "api", "locator": "GET /api/reports/sales"}],
        }],
        "actors": [{
            "id": "actor-analyst",
            "role": "analyst",
            "account_status": "active",
            "credential_secret_ref": "secret_ref:test_accounts:analyst",
        }],
        "relations": [
            _relation(
                "relation-permit",
                "permits",
                "actor-analyst",
                "op-report-sales",
                operation_ref="op-report-sales",
                actor_ref="actor-analyst",
            ),
        ],
    })

    compiled = compile_obligations_from_behavior_ir(ir)
    obligations = compiled["obligations"]
    gaps = compiled["coverage_gaps"]

    assert any(
        item.get("code") == "BLOCKED_MISSING_ACTOR_PAIR"
        and item.get("operation_ref") == "op-report-sales"
        for item in gaps
    )
    permitted = next(
        item
        for item in obligations
        if item["property"].get("template") == "permitted_operation_invocation"
    )
    assert permitted["risk_family"] == "authorization"
    assert permitted["required_actors"] == ["actor-analyst"]
    assert permitted["property"].get("operation_path_prefix") == "/api/reports"
    assert permitted["relation_refs"] == ["relation-permit"]

    experiment = compile_experiment_for_obligation(
        permitted,
        behavior_ir=ir,
        environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["control_plan"] == []
    assert len(experiment["treatment_plan"]) == 1
    assert experiment["treatment_plan"][0]["actor_ref"] == "actor-analyst"


def test_permit_only_reversible_write_emits_permitted_invocation() -> None:
    ir = empty_behavior_ir(project_id="permit-only-write")
    ir.update({
        "operations": [
            {
                "id": "op-cart-add",
                "method": "POST",
                "path": "/api/cart/items",
                "read_write": "write",
                "source_refs": [{"source_id": "api", "locator": "POST /api/cart/items"}],
                "request_example": {"sku": "A1", "qty": 1},
            },
            {
                "id": "op-cart-delete",
                "method": "DELETE",
                "path": "/api/cart/items/{id}",
                "read_write": "write",
                "source_refs": [{"source_id": "api", "locator": "DELETE /api/cart/items/{id}"}],
            },
        ],
        "actors": [{
            "id": "actor-buyer",
            "role": "buyer",
            "account_status": "active",
            "credential_secret_ref": "secret_ref:test_accounts:buyer",
        }],
        "relations": [
            _relation(
                "relation-permit",
                "permits",
                "actor-buyer",
                "op-cart-add",
                operation_ref="op-cart-add",
                actor_ref="actor-buyer",
            ),
            _relation(
                "relation-compensate",
                "compensates",
                "op-cart-delete",
                "op-cart-add",
                operation_ref="op-cart-delete",
            ),
        ],
    })

    compiled = compile_obligations_from_behavior_ir(ir)
    assert any(
        item.get("code") == "BLOCKED_MISSING_ACTOR_PAIR"
        and item.get("operation_ref") == "op-cart-add"
        for item in compiled["coverage_gaps"]
    )
    permitted = next(
        item
        for item in compiled["obligations"]
        if item["property"].get("template") == "permitted_operation_invocation"
    )
    assert permitted["cleanup_requirement"].get("operation_ref") == "op-cart-delete"
    assert permitted["property"].get("operation_path_prefix") == "/api/cart"

    experiment = compile_experiment_for_obligation(
        permitted,
        behavior_ir=ir,
        environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "BLOCKED"
    assert experiment["compile_receipt"]["reason_code"] == "BLOCKED_MISSING_OBSERVER"


def test_permit_only_write_without_cleanup_stays_gap_only() -> None:
    ir = empty_behavior_ir(project_id="permit-only-irreversible")
    ir.update({
        "operations": [{
            "id": "op-pay",
            "method": "POST",
            "path": "/api/payments/pay",
            "read_write": "write",
            "source_refs": [{"source_id": "api", "locator": "POST /api/payments/pay"}],
        }],
        "actors": [{
            "id": "actor-buyer",
            "role": "buyer",
            "account_status": "active",
            "credential_secret_ref": "secret_ref:test_accounts:buyer",
        }],
        "relations": [
            _relation(
                "relation-permit",
                "permits",
                "actor-buyer",
                "op-pay",
                operation_ref="op-pay",
                actor_ref="actor-buyer",
            ),
        ],
    })

    compiled = compile_obligations_from_behavior_ir(ir)
    assert not any(
        item["property"].get("template") == "permitted_operation_invocation"
        for item in compiled["obligations"]
    )
    assert any(
        item.get("code") == "BLOCKED_MISSING_ACTOR_PAIR"
        for item in compiled["coverage_gaps"]
    )


def test_adaptive_planner_resolves_empty_prefix_from_behavior_ir() -> None:
    obligations = [{
        "obligation_id": "obl_pay",
        "risk_family": "conservation",
        "subject_refs": ["pay"],
        "confidence": 0.8,
        "compile_status": "COMPILED",
        "property": {},
        "required_operations": ["op-pay"],
    }]
    behavior_ir = {
        "operations": [{
            "id": "op-pay",
            "method": "POST",
            "path": "/api/payments/pay",
        }],
    }
    plan = plan_obligation_round(
        obligations,
        behavior_ir=behavior_ir,
        budget=1,
    )
    assert plan["selected"][0]["path_prefix"] == "/api/payments"
    assert plan["selected"][0]["operation_key"] == "POST /api/payments/pay"
    assert plan["operation_coverage"].get("POST /api/payments/pay") == 1


def test_adaptive_planner_soft_caps_operations_within_budget() -> None:
    obligations = [
        *[
            {
                "obligation_id": f"obl_status_{index}",
                "risk_family": "authorization",
                "subject_refs": [f"status_{index}"],
                "confidence": 0.95,
                "compile_status": "COMPILED",
                "property": {"operation_path_prefix": "/api/auth"},
                "required_operations": ["op_auth_status"],
            }
            for index in range(8)
        ],
        *[
            {
                "obligation_id": f"obl_login_{index}",
                "risk_family": "authorization",
                "subject_refs": [f"login_{index}"],
                "confidence": 0.7,
                "compile_status": "COMPILED",
                "property": {"operation_path_prefix": "/api/auth"},
                "required_operations": ["op_auth_login"],
            }
            for index in range(4)
        ],
    ]
    behavior_ir = {
        "operations": [
            {"id": "op_auth_status", "method": "POST", "path": "/api/auth/admin/users/{id}/status"},
            {"id": "op_auth_login", "method": "POST", "path": "/api/auth/login"},
        ],
    }
    plan = plan_obligation_round(
        obligations,
        behavior_ir=behavior_ir,
        budget=4,
    )
    assert plan["operation_coverage"].get("POST /api/auth/login", 0) >= 1
    assert plan["operation_coverage"].get(
        "POST /api/auth/admin/users/{id}/status",
        0,
    ) <= 2
    assert plan["selected_count"] == 4


def test_adaptive_planner_soft_caps_path_prefixes_within_budget() -> None:
    obligations = [
        *[
            {
                "obligation_id": f"obl_cart_{index}",
                "risk_family": "authorization",
                "subject_refs": [f"cart_{index}"],
                "confidence": 0.9,
                "compile_status": "COMPILED",
                "property": {"operation_path_prefix": "/api/cart"},
                "required_operations": ["op_cart"],
            }
            for index in range(10)
        ],
        *[
            {
                "obligation_id": f"obl_report_{index}",
                "risk_family": "authorization",
                "subject_refs": [f"report_{index}"],
                "confidence": 0.8,
                "compile_status": "COMPILED",
                "property": {"operation_path_prefix": "/api/reports"},
                "required_operations": ["op_report"],
            }
            for index in range(4)
        ],
        *[
            {
                "obligation_id": f"obl_coupon_{index}",
                "risk_family": "authorization",
                "subject_refs": [f"coupon_{index}"],
                "confidence": 0.8,
                "compile_status": "COMPILED",
                "property": {"operation_path_prefix": "/api/coupons"},
                "required_operations": ["op_coupon"],
            }
            for index in range(4)
        ],
    ]

    plan = plan_obligation_round(obligations, budget=9)
    selected_prefixes = {
        item.get("path_prefix")
        for item in plan["selected"]
        if item.get("path_prefix")
    }
    assert "/api/reports" in selected_prefixes
    assert "/api/coupons" in selected_prefixes
    assert plan["path_prefix_coverage"].get("/api/cart", 0) <= 3
    assert plan["selected_count"] == 9


def test_adaptive_planner_soft_caps_abundant_families_within_budget() -> None:
    obligations = [
        *[
            {
                "obligation_id": f"obl_auth_{index}",
                "risk_family": "authorization",
                "subject_refs": [f"auth_{index}"],
                "confidence": 0.9,
                "compile_status": "COMPILED",
            }
            for index in range(12)
        ],
        *[
            {
                "obligation_id": f"obl_cons_{index}",
                "risk_family": "conservation",
                "subject_refs": [f"cons_{index}"],
                "confidence": 0.7,
                "compile_status": "COMPILED",
            }
            for index in range(4)
        ],
        *[
            {
                "obligation_id": f"obl_conc_{index}",
                "risk_family": "concurrency",
                "subject_refs": [f"conc_{index}"],
                "confidence": 0.7,
                "compile_status": "COMPILED",
            }
            for index in range(4)
        ],
    ]

    plan = plan_obligation_round(obligations, budget=9)

    assert plan["family_coverage"].get("conservation", 0) >= 3
    assert plan["family_coverage"].get("concurrency", 0) >= 3
    assert plan["family_coverage"].get("authorization", 0) <= 3
    assert plan["selected_count"] == 9


def test_adaptive_planner_risk_priority_comes_from_runtime_policy_data() -> None:
    common = {
        "subject_refs": ["same_subject"],
        "confidence": 0.8,
        "compile_status": "COMPILED",
    }
    obligations = [
        {**common, "obligation_id": "obl_a", "risk_family": "authorization"},
        {**common, "obligation_id": "obl_b", "risk_family": "source_declared_custom_risk"},
    ]

    plan = plan_obligation_round(
        obligations,
        budget=1,
        historical_yield={
            "risk:authorization": 0.2,
            "risk:source_declared_custom_risk": 1.0,
        },
    )

    assert plan["selected"][0]["obligation_id"] == "obl_b"


def test_no_industry_hardcoded_endpoint_drivers_in_new_modules() -> None:
    """Static guard: new modules must not hardcode benchmark/customer endpoint paths as drivers."""
    banned = re.compile(
        r"/api/orders|/api/cart|/api/coupons|/api/sku|benchmark_mall|/mall/",
        re.I,
    )
    for path in NEW_MODULES:
        text = path.read_text(encoding="utf-8")
        # Allow comments mentioning the prohibition, but not executable string literals driving logic.
        code_lines = [
            line for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#") and not line.strip().startswith('"""') and not line.strip().startswith("'''")
        ]
        joined = "\n".join(code_lines)
        assert not banned.search(joined), f"industry/benchmark hardcoding found in {path.name}"


def test_recreate_cleanup_without_buildable_body_degrades_to_residue_on_test() -> None:
    """A recreate compensator whose request body cannot be built from any
    source example must not be emitted as an empty-body compensation (target
    NaN/500, cleanup preflight block). On a declared non-production target the
    compiler degrades to accepted residue so the write is still tested and the
    leftover is surfaced for environment reset."""
    primary = {
        "id": "cancel_reservation",
        "method": "POST",
        "path": "/api/capacity/cancel",
        "read_write": "write",
    }
    recreate = {
        "id": "recreate_reservation",
        "method": "PUT",
        "path": "/api/capacity/reservations/{id}",
        "read_write": "write",
    }
    read_reservation = {
        "id": "read_reservation",
        "method": "GET",
        "path": "/api/capacity/reservations/{id}",
        "read_write": "read",
    }
    obligation = {
        "obligation_id": "obl_recreate_body_missing",
        "risk_family": "idempotency",
        "property": {
            "operation_ref": "cancel_reservation",
            "actor_ref": "operator",
        },
        "required_actors": ["operator"],
        "required_operations": ["cancel_reservation"],
        "required_fixtures": [],
        "required_observers": ["http_response"],
        "cleanup_requirement": {
            "required": True,
            "mode": "recreate_compensated_resource",
            "operation_ref": "recreate_reservation",
        },
    }
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir={
            "operations": [primary, recreate, read_reservation],
            "actors": [{"id": "operator", "role": "public"}],
            "relations": [],
            "conflicts": [],
        },
        environment_type="test",
    )
    plan = experiment.get("cleanup_plan") or []
    assert plan and plan[0]["action"] == "accepted_residue"
    assert plan[0]["mode"] == "accepted_residue_no_cleanup"
    assert plan[0]["compensates_operation_ref"] == "cancel_reservation"


def test_recreate_cleanup_without_buildable_body_stays_fail_closed_in_production() -> None:
    primary = {
        "id": "cancel_reservation",
        "method": "POST",
        "path": "/api/capacity/cancel",
        "read_write": "write",
    }
    recreate = {
        "id": "recreate_reservation",
        "method": "PUT",
        "path": "/api/capacity/reservations/{id}",
        "read_write": "write",
    }
    obligation = {
        "obligation_id": "obl_recreate_body_missing_prod",
        "risk_family": "validation",
        "property": {
            "operation_ref": "cancel_reservation",
            "actor_ref": "operator",
        },
        "required_actors": ["operator"],
        "required_operations": ["cancel_reservation"],
        "required_fixtures": [],
        "required_observers": ["http_response"],
        "cleanup_requirement": {
            "required": True,
            "mode": "recreate_compensated_resource",
            "operation_ref": "recreate_reservation",
        },
    }
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir={
            "operations": [primary, recreate],
            "actors": [{"id": "operator", "role": "public"}],
            "relations": [],
            "conflicts": [],
        },
        environment_type="production",
    )
    # Production targets stay fail-closed before any cleanup tier resolves
    # (the adapter gate blocks earlier than the residue decision).
    assert experiment["compile_receipt"]["status"] == "BLOCKED"
