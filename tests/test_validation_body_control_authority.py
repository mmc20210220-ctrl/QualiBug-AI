from __future__ import annotations

from ai_test_asset_center import obligation_compiler
from ai_test_asset_center.schema_validation_seed_authority import append_operation_schema_validation_seeds
from ai_test_asset_center.validation_body_control_authority import declared_validation_body_control
from ai_test_asset_center.validation_obligation_expander import expand_validation_obligation
from ai_test_asset_center.experiment_protocols_privacy_base import compile_family_protocol


def _src(sid: str) -> dict:
    return {"source_id": sid, "kind": "openapi", "locator": "openapi.yaml"}


def _actor() -> dict:
    return {"id":"actor-admin","status":"accepted","role":"admin","account_status":"active","credential_secret_ref":"vault://qualibug/admin","source_refs":[_src("accounts")],"confidence":0.9}


def _permit(op_id: str) -> dict:
    return {"id":"rel-admin-permits","status":"accepted","relation_type":"permits","operation_ref":op_id,"actor_ref":"actor-admin","from_ref":"actor-admin","to_ref":op_id,"source_refs":[_src("api")]}


def _operation() -> dict:
    return {
        "id":"op-create-widget","status":"accepted","method":"POST","path":"/widgets","read_write":"write",
        "request_schema":{"content":{"application/json":{"schema":{
            "type":"object","required":["status","quantity"],"properties":{
                "status":{"type":"string","enum":["ACTIVE","INACTIVE"],"example":"ACTIVE"},
                "quantity":{"type":"integer","minimum":1,"default":1},
            }
        }}}},
        "source_refs":[_src("api")],"confidence":0.95,
    }


def _compiled() -> dict:
    return {"schema_version":"qualibug.obligation-compile.v1","obligation_count":0,"by_family":{},"obligations":[],"coverage_gaps":[]}


def _ir(operation: dict) -> dict:
    return {"schema_version":"qualibug.behavior-ir.v2","operations":[operation],"actors":[_actor()],"relations":[_permit(operation["id"])],"entities":[]}


def test_schema_field_concrete_values_form_validation_control_without_request_example() -> None:
    operation=_operation()
    control, receipt=declared_validation_body_control(operation)
    assert control=={"status":"ACTIVE","quantity":1}
    assert receipt["authority"]=="request_schema_concrete_values"
    assert receipt["type_fallback_allowed"] is False

    result=append_operation_schema_validation_seeds(_compiled(),behavior_ir=_ir(operation),compiler_base=obligation_compiler)
    assert result["schema_validation_seed_receipt"]["seed_count"]==1
    seed=result["obligations"][0]
    variants=expand_validation_obligation(seed,operation=operation)
    assert {row["property"]["validation_constraint"] for row in variants} >= {"required","type:string","enum","type:integer","minimum"}

    required_status=next(row for row in variants if row["property"].get("field_tokens")==["status"] and row["property"].get("validation_constraint")=="required")
    protocol=compile_family_protocol(
        risk_family="validation", operation=operation, operation_ref=operation["id"],
        control_actor_ref="actor-admin", treatment_actor_ref="actor-admin",
        property_spec=required_status["property"], behavior_ir=_ir(operation),
    )
    assert protocol["status"]=="COMPILED"
    assert protocol["control_plan"][0]["body"]=={"status":"ACTIVE","quantity":1}
    assert protocol["treatment_plan"][0]["body"]=={"quantity":1}
    mutation=protocol["treatment_plan"][0]["mutation"]
    assert mutation["control_value_authority"]=="request_schema_concrete_values"
    assert mutation["source_declared_control_value"] is True


def test_type_only_required_body_field_never_authorizes_synthetic_control() -> None:
    operation=_operation()
    operation["request_schema"]["content"]["application/json"]["schema"]["properties"]["status"]={"type":"string"}
    control, receipt=declared_validation_body_control(operation)
    assert control=={}
    assert receipt=={}
    result=append_operation_schema_validation_seeds(_compiled(),behavior_ir=_ir(operation),compiler_base=obligation_compiler)
    assert result["obligations"]==[]


def test_multi_value_enum_without_example_is_not_a_control_value() -> None:
    operation=_operation()
    schema=operation["request_schema"]["content"]["application/json"]["schema"]
    schema["properties"]["status"].pop("example")
    control, receipt=declared_validation_body_control(operation)
    assert control=={}
    assert receipt=={}


def test_singleton_enum_is_source_concrete_body_control() -> None:
    operation=_operation()
    schema=operation["request_schema"]["content"]["application/json"]["schema"]
    schema["properties"]["status"]={"type":"string","enum":["ACTIVE"]}
    control, receipt=declared_validation_body_control(operation)
    assert control=={"status":"ACTIVE","quantity":1}
    assert receipt["authority"]=="request_schema_concrete_values"
