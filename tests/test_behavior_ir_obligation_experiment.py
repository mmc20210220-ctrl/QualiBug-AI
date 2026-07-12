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
from ai_test_asset_center.contract_oracles import (
    demote_heuristic_business_oracle_finding,
    evaluate_contract_oracle,
)
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation, compile_experiments
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir
from ai_test_asset_center.runtime_binding_graph import apply_binding, build_binding_plan, extract_placeholders
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
    ROOT / "ai_test_asset_center" / "execution_adapter.py",
]


def _sample_asset() -> dict:
    return {
        "sources": [{"source_id": "src-api", "filename": "api.md", "source_type": "api"}],
        "permission_matrix": [
            {"role": "viewer_role", "resource": "/resources/{id}", "actions": ["read"], "scope": "own"},
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


def test_entity_validation_uses_explicit_relation_operation() -> None:
    ir = empty_behavior_ir(project_id="entity-relation-test")
    ir.update({
        "entities": [{"id": "entity-resource", "name": "resource"}],
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
            )
        ],
    })

    obligation = next(
        item
        for item in compile_obligations_from_behavior_ir(ir)["obligations"]
        if item["risk_family"] == "validation"
        and item["property"].get("entity_ref") == "entity-resource"
    )

    assert obligation["property"]["operation_ref"] == "op-resource-write"
    assert obligation["relation_refs"] == ["relation-produces-resource"]


def test_cleanup_binding_requires_explicit_compensates_relation() -> None:
    ir = empty_behavior_ir(project_id="cleanup-relation-test")
    ir.update({
        "operations": [
            {"id": "create-resource", "method": "POST", "path": "/resources", "read_write": "write"},
            {"id": "delete-resource", "method": "DELETE", "path": "/resources/{id}", "read_write": "write"},
        ],
        "actors": [
            {"id": "actor-control", "role": "control", "account_status": "active", "allowed_resources": ["resources"], "allowed_actions": ["create"]},
            {"id": "actor-treatment", "role": "treatment", "account_status": "active", "allowed_resources": ["other"], "allowed_actions": ["read"]},
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

    assert "operation_ref" not in obligation["cleanup_requirement"]


def test_authorization_pair_comes_from_relations_not_actor_array_permissions() -> None:
    ir = empty_behavior_ir(project_id="authorization-relation-test")
    ir.update({
        "operations": [{"id": "op-create", "method": "POST", "path": "/resources", "read_write": "write"}],
        "actors": [
            {"id": "actor-order-trap", "role": "trap", "account_status": "active", "allowed_resources": ["resources"], "allowed_actions": ["create"]},
            {"id": "actor-control", "role": "control", "account_status": "active", "allowed_resources": [], "allowed_actions": []},
            {"id": "actor-treatment", "role": "treatment", "account_status": "active", "allowed_resources": [], "allowed_actions": []},
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
    assert write_effect_obligations
    assert all(
        item["cleanup_requirement"].get("operation_ref") == "delete_resource"
        for item in write_effect_obligations
    )
    assert all(not item.get("required_actors") for item in write_effect_obligations)
    assert all("treatment_actor_ref" not in item["property"] for item in write_effect_obligations)


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
    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="proj-a")
    compiled = compile_obligations_from_behavior_ir(ir)
    write_op = next(op for op in ir["operations"] if "{id}" in str(op.get("path")))
    actors = ir["actors"]
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
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert compiled["obligation_count"] > 0


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
            {"role": "viewer_role", "account_ref": "viewer_a", "secret_ref": "secret_ref:test_accounts:viewer_a"},
            {"role": "owner_role", "account_ref": "owner_a", "secret_ref": "secret_ref:test_accounts:owner_a"},
        ],
    )
    target_op = next(op for op in ir["operations"] if str(op.get("path")) == "/resources/:id")
    obligation = {
        "obligation_id": "obl_colon_path_binding",
        "risk_family": "isolation",
        "subject_refs": [target_op["id"]],
        "property": {"template": "tenant_isolation", "operation_ref": target_op["id"]},
        "required_actors": [actor["id"] for actor in ir["actors"][:2]],
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
            "operation_id": "archive_resource",
            "method": "POST",
            "path": "/api/resources/:id/archive",
            "side_effect_class": "write",
        },
        {
            "operation_id": "archive_resource",
            "method": "POST",
            "path": "/api/resources/:id/archive",
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
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl_fixture_backed_binding",
            "risk_family": "authorization",
            "property": {
                "template": "authorization_control_treatment",
                "operation_ref": target["id"],
                "control_actor_ref": ir["actors"][0]["id"],
                "treatment_actor_ref": ir["actors"][1]["id"],
            },
            "required_actors": [ir["actors"][0]["id"], ir["actors"][1]["id"]],
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
            op["id"] for op in ir["operations"] if op.get("operation_id") == "archive_resource"
        ),
        "method": "POST",
        "path": "/api/resources/{id}/archive",
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
    cleanup = {"id": "archive_resource", "method": "POST", "path": "/api/resources/{id}/archive"}
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
        },
    )

    fixture_setup = next(item for item in plan if item["target"] == "resourceId")["fixture_setup"]
    assert fixture_setup["cleanup_operations"] == [{
        "operation_ref": "archive_resource",
        "method": "POST",
        "path": "/api/resources/{id}/archive",
    }]


def test_experiment_compiler_blocks_production_environment() -> None:
    ir = build_behavior_ir_from_knowledge_asset(_sample_asset(), project_id="proj-a")
    obl = compile_obligations_from_behavior_ir(ir)["obligations"][0]
    blocked = compile_experiment_for_obligation(obl, behavior_ir=ir, environment_type="production")
    assert blocked["compile_receipt"]["status"] == "BLOCKED"
    assert blocked["compile_receipt"]["reason_code"] == "BLOCKED_UNSUPPORTED_ADAPTER"


def test_binding_priority_no_low_override() -> None:
    plan = [{"target": "id", "status": "bound", "source_priority": "experiment_setup_response", "value_fingerprint": "aaa"}]
    updated = apply_binding(plan, target="id", value="low", source_priority="schema_generated")
    assert updated[0]["source_priority"] == "experiment_setup_response"
    assert "override_rejected" in updated[0]


def test_assertion_concurrency_rejects_dual_2xx_alone() -> None:
    result = evaluate_assertion(
        {"kind": "concurrency_final_invariant", "assertion_id": "a1"},
        observations={"dual_2xx": True},
    )
    assert result["passed"] is False
    assert result["error"] == "dual_2xx_insufficient"


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
    assert result["passed"] is False


def test_contract_oracle_harness_error_not_defect() -> None:
    verdict = evaluate_contract_oracle(
        experiment={"control_plan": [{"x": 1}], "treatment_plan": [{"y": 1}], "observers": [{"observer_id": "http_response"}], "assertions": [{"kind": "http_status", "expected": 403}]},
        evidence={"harness_error": True, "control_succeeded": True, "treatment_observation": {}, "http_response": True},
    )
    assert verdict["verdict"] == "harness_failure"
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


def test_source_grounded_permission_bypass_not_demoted() -> None:
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
    kept = demote_heuristic_business_oracle_finding(finding)
    assert kept["customer_delivery_status"] == "defect"
    assert kept["gate_passed"] is True
    assert "oracle_demotion_reason" not in kept


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
