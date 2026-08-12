from __future__ import annotations

from ai_test_asset_center import obligation_compiler
from ai_test_asset_center.validation_obligation_expander import expand_validation_obligation
from ai_test_asset_center.schema_validation_seed_authority import append_operation_schema_validation_seeds


def _source(source_id: str) -> dict:
    return {"source_id": source_id, "kind": "openapi", "locator": "openapi.yaml"}


def _actor() -> dict:
    return {
        "id": "actor-reader",
        "status": "accepted",
        "role": "reader",
        "account_status": "active",
        "credential_secret_ref": "vault://qualibug/reader",
        "source_refs": [_source("src-accounts")],
        "confidence": 0.9,
    }


def _permit(operation_ref: str) -> dict:
    return {
        "id": f"rel-reader-permits-{operation_ref}",
        "status": "accepted",
        "relation_type": "permits",
        "operation_ref": operation_ref,
        "actor_ref": "actor-reader",
        "from_ref": "actor-reader",
        "to_ref": operation_ref,
        "source_refs": [_source("src-api")],
    }


def _read_operation() -> dict:
    return {
        "id": "op-search-widget",
        "status": "accepted",
        "method": "GET",
        "path": "/widgets",
        "read_write": "read",
        "parameters": [
            {
                "name": "status",
                "in": "query",
                "required": True,
                "schema": {"type": "string", "enum": ["ACTIVE", "INACTIVE"]},
                "example": "ACTIVE",
            }
        ],
        "source_refs": [_source("src-api")],
        "confidence": 0.95,
    }


def _compiled() -> dict:
    return {
        "schema_version": "qualibug.obligation-compile.v1",
        "obligation_count": 0,
        "by_family": {},
        "obligations": [],
        "coverage_gaps": [],
    }


def test_read_parameter_contract_seeds_validation_without_entity_join() -> None:
    operation = _read_operation()
    ir = {
        "schema_version": "qualibug.behavior-ir.v2",
        "operations": [operation],
        "actors": [_actor()],
        "relations": [_permit(operation["id"])],
        "entities": [],
    }
    result = append_operation_schema_validation_seeds(
        _compiled(), behavior_ir=ir, compiler_base=obligation_compiler
    )
    seeds = [
        row for row in result["obligations"]
        if row.get("property", {}).get("schema_validation_seed_authority")
        == "operation_request_contract"
    ]
    assert len(seeds) == 1
    seed = seeds[0]
    assert seed["cleanup_requirement"] == {"required": False}
    assert seed["property"]["operation_effect"] == "read"
    assert seed["required_observers"] == ["http_response"]
    variants = expand_validation_obligation(seed, operation=operation)
    assert variants
    assert all("business_effect" not in row["required_observers"] for row in variants)
    parameter_variants = [
        row for row in variants
        if row.get("property", {}).get("field_tokens") == ["@query", "status"]
    ]
    assert {row["property"]["validation_constraint"] for row in parameter_variants} == {
        "required", "type:string", "enum"
    }
    assert result["schema_validation_seed_receipt"]["seed_count"] == 1


def test_read_parameter_contract_without_permit_stays_fail_closed() -> None:
    operation = _read_operation()
    ir = {
        "schema_version": "qualibug.behavior-ir.v2",
        "operations": [operation],
        "actors": [_actor()],
        "relations": [],
        "entities": [],
    }
    result = append_operation_schema_validation_seeds(
        _compiled(), behavior_ir=ir, compiler_base=obligation_compiler
    )
    assert result["obligations"] == []
    assert result["schema_validation_seed_receipt"]["blocked_operation_count"] == 1
    assert any(row.get("schema_validation_seed_blocked") is True for row in result["coverage_gaps"])


def test_type_only_query_parameter_does_not_create_heuristic_seed() -> None:
    operation = _read_operation()
    operation["parameters"][0].pop("example", None)
    operation["parameters"][0]["schema"] = {"type": "string"}
    ir = {
        "schema_version": "qualibug.behavior-ir.v2",
        "operations": [operation],
        "actors": [_actor()],
        "relations": [_permit(operation["id"])],
        "entities": [],
    }
    result = append_operation_schema_validation_seeds(
        _compiled(), behavior_ir=ir, compiler_base=obligation_compiler
    )
    assert result["obligations"] == []
    assert result["schema_validation_seed_receipt"]["seed_count"] == 0


def test_singleton_enum_is_a_source_declared_query_control_value() -> None:
    operation = _read_operation()
    operation["parameters"][0].pop("example", None)
    operation["parameters"][0]["schema"] = {"type": "string", "enum": ["ACTIVE"]}
    ir = {
        "schema_version": "qualibug.behavior-ir.v2",
        "operations": [operation],
        "actors": [_actor()],
        "relations": [_permit(operation["id"])],
        "entities": [],
    }
    result = append_operation_schema_validation_seeds(
        _compiled(), behavior_ir=ir, compiler_base=obligation_compiler
    )
    assert result["schema_validation_seed_receipt"]["seed_count"] == 1
    seed = result["obligations"][0]
    variants = expand_validation_obligation(seed, operation=operation)
    parameter_variants = [
        row for row in variants
        if row.get("property", {}).get("field_tokens") == ["@query", "status"]
    ]
    assert {row["property"]["validation_constraint"] for row in parameter_variants} == {
        "required", "type:string", "enum"
    }
