from __future__ import annotations


def _graph() -> dict:
    return {
        "nodes": [
            {"node_id": "step-1", "operation_ref": "op-1"},
            {"node_id": "step-2", "operation_ref": "op-2"},
        ],
        "edges": [],
    }


def test_name_only_produced_binding_cannot_make_later_token_available() -> None:
    from ai_test_asset_center.flow_data_requirement import build_flow_data_requirement

    graph = _graph()
    experiment = {
        "binding_plan": [],
        "treatment_plan": [
            {
                "step_id": "step-1",
                "operation_ref": "op-1",
                "method": "POST",
                "produces_bindings": ["future_id"],
                "_execution_graph": graph,
            },
            {
                "step_id": "step-2",
                "operation_ref": "op-2",
                "method": "POST",
                "body": {"id": "{future_id}"},
                "_execution_graph": graph,
            },
        ],
    }
    behavior_ir = {
        "operations": [
            {"id": "op-1", "method": "POST", "path": "/api/source"},
            {"id": "op-2", "method": "POST", "path": "/api/consumer"},
        ]
    }

    result = build_flow_data_requirement(experiment, behavior_ir=behavior_ir)

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_FLOW_DATA_BINDING_INCOMPLETE"
    assert result["flow_data_execution_contract"]["status"] == "BLOCKED"
    assert any(
        row.get("kind") == "STEP_BINDING_EXECUTION_SOURCE_MISSING"
        for row in result["flow_data_execution_contract"]["issues"]
    )


def test_unknown_step_operation_blocks_before_requirement_freeze() -> None:
    from ai_test_asset_center.flow_data_requirement import build_flow_data_requirement

    result = build_flow_data_requirement(
        {
            "binding_plan": [],
            "treatment_plan": [
                {
                    "step_id": "step-missing",
                    "operation_ref": "op-missing",
                    "method": "POST",
                }
            ],
        },
        behavior_ir={"operations": []},
    )

    assert result["status"] == "BLOCKED"
    assert result["operation_contract_issues"][0]["kind"] == (
        "FLOW_STEP_OPERATION_UNRESOLVED"
    )


def test_step_method_drift_blocks_flow_data_freeze() -> None:
    from ai_test_asset_center.flow_data_requirement import build_flow_data_requirement

    result = build_flow_data_requirement(
        {
            "binding_plan": [],
            "control_plan": [
                {
                    "step_id": "step-1",
                    "operation_ref": "op-1",
                    "method": "POST",
                }
            ],
        },
        behavior_ir={
            "operations": [
                {"id": "op-1", "method": "GET", "path": "/api/items"}
            ]
        },
    )

    assert result["status"] == "BLOCKED"
    assert result["operation_contract_issues"][0]["kind"] == (
        "FLOW_STEP_METHOD_DRIFT"
    )
