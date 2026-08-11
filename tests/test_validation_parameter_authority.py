from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.validation_parameter_authority import (
    install_validation_parameter_authority,
    strict_parameter_constraint_material,
    strict_parameter_entries,
)


def _query_operation(*, with_example: bool = True) -> dict:
    parameter = {
        "name": "q",
        "in": "query",
        "required": True,
        "schema": {"type": "string"},
    }
    if with_example:
        parameter["example"] = "needle"
    return {
        "id": "op_search",
        "method": "GET",
        "path": "/search",
        "parameters": [parameter],
    }


def test_parameter_entries_never_fabricate_control_from_type_only() -> None:
    operation = _query_operation(with_example=False)
    assert strict_parameter_entries(operation) == []


def test_parameter_required_string_false_is_not_truthy_required() -> None:
    operation = _query_operation()
    operation["parameters"][0]["required"] = "false"
    entries = strict_parameter_entries(operation)
    assert len(entries) == 1
    assert entries[0]["required"] is False
    assert entries[0]["example"] == "needle"
    assert entries[0]["control_value_authority"] == "parameter_example"


def test_required_query_mutation_uses_declared_control_value() -> None:
    control, treatment, mutation, reason = strict_parameter_constraint_material(
        _query_operation(),
        {
            "validation_constraint": "required",
            "validation_constraint_source": "request_schema",
            "field_tokens": ["@query", "q"],
            "parameter_location": "query",
        },
        location="query",
        tokens=("q",),
        constraint="required",
    )
    assert reason == ""
    assert control == {"q": "needle"}
    assert treatment == {}
    assert mutation["operator"] == "remove_required_parameter"
    assert mutation["source_declared_control_value"] is True
    assert mutation["control_value_authority"] == "parameter_example"


def test_parameter_mutation_fails_closed_without_control_value_authority() -> None:
    control, treatment, mutation, reason = strict_parameter_constraint_material(
        _query_operation(with_example=False),
        {
            "validation_constraint": "required",
            "validation_constraint_source": "request_schema",
            "field_tokens": ["@query", "q"],
            "parameter_location": "query",
        },
        location="query",
        tokens=("q",),
        constraint="required",
    )
    assert control == {}
    assert treatment == {}
    assert mutation == {}
    assert reason == "parameter_control_value_authority_missing"


def test_request_build_contract_accepts_only_declared_required_query_removal() -> None:
    install_validation_parameter_authority()
    from ai_test_asset_center.request_build_contract import (
        STATUS_BLOCKED,
        STATUS_READY,
        build_request_build_contract,
    )

    operation = _query_operation()
    mutation = {
        "json_path": "$['@query'].q",
        "field_tokens": ["@query", "q"],
        "parameter_location": "query",
        "constraint": "required",
        "source": "request_schema",
        "operator": "remove_required_parameter",
        "control_value_authority": "parameter_example",
        "source_declared_control_value": True,
    }
    experiment = {
        "experiment_id": "exp_required_query",
        "obligation_id": "obl_required_query",
        "control_plan": [
            {
                "step_id": "control_1",
                "operation_ref": "op_search",
                "query": {"q": "needle"},
            }
        ],
        "treatment_plan": [
            {
                "step_id": "treatment_1",
                "operation_ref": "op_search",
                "query": {},
                "mutation": mutation,
            }
        ],
    }
    behavior_ir = {"operations": [operation]}
    contract = build_request_build_contract(
        experiment,
        behavior_ir=behavior_ir,
        flow_execution_contract={},
    )
    assert contract["status"] == STATUS_READY
    treatment = next(
        row for row in contract["steps"] if row["step_id"] == "treatment_1"
    )
    query_component = next(
        row for row in treatment["components"] if row["component"] == "query"
    )
    required_row = query_component["required"][0]
    assert required_row["status"] == STATUS_READY
    assert required_row["intentional_absence"] is True
    assert required_row["authority"] == "declared_required_query_removal_mutation"

    undeclared = deepcopy(experiment)
    undeclared["treatment_plan"][0].pop("mutation")
    blocked = build_request_build_contract(
        undeclared,
        behavior_ir=behavior_ir,
        flow_execution_contract={},
    )
    assert blocked["status"] == STATUS_BLOCKED
