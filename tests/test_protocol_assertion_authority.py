from __future__ import annotations


def test_generic_family_without_assertion_authority_is_blocked() -> None:
    from ai_test_asset_center.experiment_protocols import compile_family_protocol

    result = compile_family_protocol(
        risk_family="custom_missing_oracle",
        operation={
            "id": "op-custom",
            "method": "POST",
            "path": "/custom",
            "request_example": {"value": "declared"},
        },
        operation_ref="op-custom",
        control_actor_ref="",
        treatment_actor_ref="actor-1",
        property_spec={"template": "custom-template"},
        behavior_ir={"operations": [], "actors": []},
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "FIELD_LEVEL_RULE_NOT_EXECUTABLE"
    assert result["detail"] == (
        "protocol_assertion_kind_missing:custom_missing_oracle"
    )
    assert result["assertion_authority_gate"][
        "generic_http_status_fallback_allowed"
    ] is False


def test_registered_assertion_kind_is_projected_when_compiler_omits_it() -> None:
    from ai_test_asset_center.experiment_protocol_registry import (
        register_family_protocol,
    )
    from ai_test_asset_center.experiment_protocols import compile_family_protocol

    family = "custom_registered_oracle_projection"
    template = "custom_registered_oracle_template"

    def _compiler(envelope: dict) -> dict:
        return {
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [
                {
                    "step_id": "treatment_1",
                    "actor_ref": envelope["treatment_actor_ref"],
                    "operation_ref": envelope["operation_ref"],
                }
            ],
        }

    register_family_protocol(
        family,
        template,
        compiler=_compiler,
        observers=["http_response"],
        assertion_kind="http_status_class",
    )

    result = compile_family_protocol(
        risk_family=family,
        operation={"id": "op-registered", "method": "GET", "path": "/registered"},
        operation_ref="op-registered",
        control_actor_ref="",
        treatment_actor_ref="actor-1",
        property_spec={"template": template},
        behavior_ir={"operations": [], "actors": []},
    )

    assert result["status"] == "COMPILED"
    assert result["assertion"]["kind"] == "http_status_class"
    assert result["assertion_authority_gate"]["status"] == "PASS"
