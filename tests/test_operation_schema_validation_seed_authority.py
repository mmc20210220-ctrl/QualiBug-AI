from __future__ import annotations

from ai_test_asset_center import obligation_compiler
from ai_test_asset_center._validation_obligation_expander_core import (
    expand_validation_obligation,
)
from ai_test_asset_center.schema_validation_seed_authority import (
    append_operation_schema_validation_seeds,
)


def _source(source_id: str) -> dict:
    return {"source_id": source_id, "kind": "openapi", "locator": "openapi.yaml"}


def _operation(*, constrained: bool = True) -> dict:
    schema = {
        "type": "object",
        "properties": {
            "status": (
                {"type": "string", "enum": ["ACTIVE", "INACTIVE"]}
                if constrained
                else {}
            )
        },
    }
    if constrained:
        schema["required"] = ["status"]
    return {
        "id": "op-update-widget",
        "status": "accepted",
        "method": "PATCH",
        "path": "/widgets/{id}",
        "read_write": "write",
        "request_schema": {
            "content": {
                "application/json": {
                    "schema": schema,
                    "example": {"status": "ACTIVE"},
                }
            }
        },
        "source_refs": [_source("src-api")],
        "confidence": 0.95,
    }


def _actor(*, resolvable: bool = True) -> dict:
    return {
        "id": "actor-admin",
        "status": "accepted",
        "role": "admin",
        "account_status": "active",
        "credential_secret_ref": (
            "vault://qualibug/admin" if resolvable else "secret_ref:actor:admin"
        ),
        "source_refs": [_source("src-accounts")],
        "confidence": 0.9,
    }


def _permit() -> dict:
    return {
        "id": "rel-admin-permits-update",
        "status": "accepted",
        "relation_type": "permits",
        "operation_ref": "op-update-widget",
        "actor_ref": "actor-admin",
        "from_ref": "actor-admin",
        "to_ref": "op-update-widget",
        "source_refs": [_source("src-api")],
    }


def _compiled() -> dict:
    return {
        "schema_version": "qualibug.obligation-compile.v1",
        "obligation_count": 0,
        "by_family": {},
        "obligations": [],
        "coverage_gaps": [],
    }


def _ir(*, permits: bool = True, resolvable: bool = True, constrained: bool = True) -> dict:
    return {
        "schema_version": "qualibug.behavior-ir.v2",
        "operations": [_operation(constrained=constrained)],
        "actors": [_actor(resolvable=resolvable)],
        "relations": [_permit()] if permits else [],
        "entities": [],
    }


def test_operation_schema_seed_does_not_require_entity_relation() -> None:
    ir = _ir()
    result = append_operation_schema_validation_seeds(
        _compiled(), behavior_ir=ir, compiler_base=obligation_compiler
    )
    seeds = [
        row
        for row in result["obligations"]
        if row.get("property", {}).get("schema_validation_seed_authority")
        == "operation_request_contract"
    ]
    assert len(seeds) == 1
    seed = seeds[0]
    assert seed["required_actors"] == ["actor-admin"]
    assert seed["required_operations"] == ["op-update-widget"]
    assert seed["property"]["template"] == "single_dimension_mutation"
    variants = expand_validation_obligation(seed, operation=ir["operations"][0])
    assert {
        row["property"]["validation_constraint"] for row in variants
    } == {"required", "type:string", "enum"}
    assert all(
        row["property"]["validation_constraint_source"] == "request_schema"
        for row in variants
    )


def test_schema_seed_without_source_permit_stays_coverage_gap() -> None:
    result = append_operation_schema_validation_seeds(
        _compiled(), behavior_ir=_ir(permits=False), compiler_base=obligation_compiler
    )
    assert result["obligations"] == []
    assert result["schema_validation_seed_receipt"]["seed_count"] == 0
    assert result["schema_validation_seed_receipt"]["blocked_operation_count"] == 1
    assert any(
        row.get("code") == "BLOCKED_MISSING_IR_RELATION"
        and row.get("schema_validation_seed_blocked") is True
        for row in result["coverage_gaps"]
    )


def test_schema_seed_with_unresolved_actor_secret_stays_fail_closed() -> None:
    result = append_operation_schema_validation_seeds(
        _compiled(),
        behavior_ir=_ir(resolvable=False),
        compiler_base=obligation_compiler,
    )
    assert result["obligations"] == []
    assert result["schema_validation_seed_receipt"]["blocked_operation_count"] == 1
    assert any(
        row.get("code") == "BLOCKED_MISSING_ACTOR_BINDING"
        and row.get("actor_ref") == "actor-admin"
        and row.get("schema_validation_seed_blocked") is True
        for row in result["coverage_gaps"]
    )


def test_operation_without_expandable_schema_constraint_gets_no_seed_or_gap() -> None:
    ir = _ir(constrained=False)
    ir["operations"][0]["path"] = "/widgets"
    ir["operations"][0]["request_schema"] = {}
    result = append_operation_schema_validation_seeds(
        _compiled(),
        behavior_ir=ir,
        compiler_base=obligation_compiler,
    )
    assert result["obligations"] == []
    assert result["coverage_gaps"] == []
    assert result["schema_validation_seed_receipt"] == {
        "authority": "operation_request_contract",
        "seed_count": 0,
        "blocked_operation_count": 0,
        "source_order_selection_allowed": False,
        "implicit_public_actor_allowed": False,
    }
