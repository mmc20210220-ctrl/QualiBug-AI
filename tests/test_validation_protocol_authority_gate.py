from __future__ import annotations

from ai_test_asset_center import experiment_protocols as protocols


def _compiled_mutation(mutation: dict) -> dict:
    return {
        "status": "COMPILED",
        "control_plan": [{"step_id": "control_1"}],
        "treatment_plan": [
            {"step_id": "treatment_1", "mutation": mutation}
        ],
        "assertion": {"kind": "validation_rejection"},
    }


def test_request_example_inference_is_never_formal_validation_authority() -> None:
    problem = protocols._validation_authority_problem(
        result=_compiled_mutation({
            "json_path": "$.amount",
            "constraint": "semantic:negative_value",
            "source": "inferred_from_example",
        }),
        property_spec={"template": "single_dimension_mutation"},
    )
    assert problem == "request_example_inference_not_validation_authority"


def test_source_invariant_cannot_silently_cross_with_unrelated_schema_constraint() -> None:
    problem = protocols._validation_authority_problem(
        result=_compiled_mutation({
            "json_path": "$.password",
            "constraint": "semantic:below_min_length",
            "source": "request_schema",
        }),
        property_spec={
            "invariant_ref": "bir:order-state-rule",
            "source_rule_statement": "已取消订单不得再次支付",
        },
    )
    assert problem.startswith(
        "schema_field_semantic_inference_not_authoritative:"
    )


def test_explicit_validation_constraint_is_authoritative() -> None:
    problem = protocols._validation_authority_problem(
        result=_compiled_mutation({
            "json_path": "$.email",
            "constraint": "pattern",
            "source": "request_schema",
        }),
        property_spec={
            "field_tokens": ["email"],
            "validation_constraint": "pattern",
            "validation_constraint_source": "request_schema",
        },
    )
    assert problem == ""


def test_source_declared_sql_injection_semantics_remain_authoritative() -> None:
    problem = protocols._validation_authority_problem(
        result=_compiled_mutation({
            "json_path": "$.keyword",
            "constraint": "semantic:sql_injection_probe",
            "source": "request_schema",
        }),
        property_spec={
            "invariant_ref": "bir:query-safety",
            "source_rule_statement": "关键词查询必须参数化，不得拼接 SQL",
        },
    )
    assert problem == ""


def test_source_declared_numeric_boundary_can_drive_semantic_negative_probe() -> None:
    problem = protocols._validation_authority_problem(
        result=_compiled_mutation({
            "json_path": "$.quantity",
            "constraint": "semantic:negative_value",
            "source": "request_schema",
        }),
        property_spec={
            "invariant_ref": "bir:quantity-boundary",
            "source_rule_statement": "数量必须非负，不允许为负",
        },
    )
    assert problem == ""


def test_runtime_source_rule_mutation_requires_source_bound_property() -> None:
    mutation = {
        "class": "runtime_entity_state_violation",
        "json_path": "$.entityId",
    }
    assert protocols._validation_authority_problem(
        result=_compiled_mutation(mutation),
        property_spec={
            "invariant_ref": "bir:state-exposure",
            "source_rule_statement": "用户端不得展示停用实体",
        },
    ) == ""
    assert protocols._validation_authority_problem(
        result=_compiled_mutation(mutation),
        property_spec={},
    ).startswith("runtime_validation_mutation_lacks_source_rule")
