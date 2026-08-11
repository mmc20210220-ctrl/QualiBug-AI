from __future__ import annotations


def test_compiled_step_cannot_reference_unknown_operation() -> None:
    from ai_test_asset_center.experiment_protocols import (
        _protocol_operation_contract_problem,
    )

    problem = _protocol_operation_contract_problem(
        result={
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [
                {"step_id": "t1", "operation_ref": "missing", "method": "POST"}
            ],
        },
        operation={"id": "op-main", "method": "POST", "path": "/api/main"},
        operation_ref="op-main",
        behavior_ir={"operations": []},
    )

    assert problem == "protocol_operation_unresolved:treatment:missing"


def test_protocol_default_post_cannot_override_missing_source_method() -> None:
    from ai_test_asset_center.experiment_protocols import (
        _protocol_operation_contract_problem,
    )

    problem = _protocol_operation_contract_problem(
        result={
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [
                {"step_id": "t1", "operation_ref": "op-1", "method": "POST"}
            ],
        },
        operation={"id": "op-main", "method": "POST", "path": "/api/main"},
        operation_ref="op-main",
        behavior_ir={
            "operations": [{"id": "op-1", "path": "/api/items"}]
        },
    )

    assert problem == "protocol_operation_method_missing:treatment:op-1"


def test_protocol_step_method_must_match_source_operation() -> None:
    from ai_test_asset_center.experiment_protocols import (
        _protocol_operation_contract_problem,
    )

    problem = _protocol_operation_contract_problem(
        result={
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [
                {"step_id": "t1", "operation_ref": "op-1", "method": "POST"}
            ],
        },
        operation={"id": "op-main", "method": "POST", "path": "/api/main"},
        operation_ref="op-main",
        behavior_ir={
            "operations": [
                {"id": "op-1", "method": "PATCH", "path": "/api/items/{id}"}
            ]
        },
    )

    assert problem == (
        "protocol_operation_method_drift:treatment:op-1:step=POST:source=PATCH"
    )
