from __future__ import annotations

from ai_test_asset_center import obligation_compiler
from ai_test_asset_center.schema_validation_seed_authority import append_operation_schema_validation_seeds
from ai_test_asset_center.validation_obligation_expander import expand_validation_obligation


def _src() -> dict:
    return {"source_id": "api", "kind": "openapi", "locator": "openapi.yaml"}


def _operation(*, explicit_security: bool = True, public_text: bool = False) -> dict:
    operation = {
        "id": "op-public-search",
        "status": "accepted",
        "method": "GET",
        "path": "/search",
        "read_write": "read",
        "parameters": [{
            "name": "q",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "minLength": 2},
            "example": "ok",
        }],
        "source_refs": [_src()],
        "confidence": 0.95,
    }
    if explicit_security:
        operation["security"] = []
    if public_text:
        operation["description"] = "public access; no authentication required"
    return operation


def _compiled() -> dict:
    return {"schema_version":"qualibug.obligation-compile.v1","obligation_count":0,"by_family":{},"obligations":[],"coverage_gaps":[]}


def test_normalized_empty_security_without_public_text_stays_fail_closed() -> None:
    operation = _operation()
    ir = {"schema_version":"qualibug.behavior-ir.v2","operations":[operation],"actors":[],"relations":[],"entities":[]}
    result = append_operation_schema_validation_seeds(_compiled(),behavior_ir=ir,compiler_base=obligation_compiler)
    assert result["obligations"] == []
    assert result["schema_validation_seed_receipt"]["blocked_operation_count"] == 1


def test_operation_public_access_text_is_source_authority_for_anonymous_seed() -> None:
    operation = _operation(explicit_security=False, public_text=True)
    ir = {"schema_version":"qualibug.behavior-ir.v2","operations":[operation],"actors":[],"relations":[],"entities":[]}
    result = append_operation_schema_validation_seeds(_compiled(),behavior_ir=ir,compiler_base=obligation_compiler)
    seed = result["obligations"][0]
    assert seed["required_actors"] == ["anonymous"]
    assert seed["property"]["actor_execution_authority"] == "operation_public_access_contract"
    assert seed["cleanup_requirement"] == {"required": False}
    assert any(actor.get("id") == "anonymous" for actor in ir["actors"])
    variants = expand_validation_obligation(seed, operation=operation)
    assert {row["property"]["validation_constraint"] for row in variants} >= {"required", "type:string", "minLength"}
    assert result["coverage_gaps"] == []


def test_missing_security_without_public_declaration_stays_fail_closed() -> None:
    operation = _operation(explicit_security=False, public_text=False)
    ir = {"schema_version":"qualibug.behavior-ir.v2","operations":[operation],"actors":[],"relations":[],"entities":[]}
    result = append_operation_schema_validation_seeds(_compiled(),behavior_ir=ir,compiler_base=obligation_compiler)
    assert result["obligations"] == []
    assert result["schema_validation_seed_receipt"]["blocked_operation_count"] == 1
    assert any(row.get("schema_validation_seed_blocked") is True for row in result["coverage_gaps"])
