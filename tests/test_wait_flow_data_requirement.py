from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.experiment_compile_freezer import (
    _requirement_projection_input,
)
from ai_test_asset_center.flow_data_requirement import (
    BLOCKED_FLOW_DATA_BINDING_INCOMPLETE,
    build_flow_data_requirement,
)


IR = {
    "operations": [
        {
            "id": "op_result",
            "method": "GET",
            "read_write": "read",
            "path": "/jobs/result",
        }
    ]
}


def _experiment(*, with_binding: bool) -> dict:
    return {
        "binding_plan": (
            [
                {
                    "target": "job_id",
                    "status": "runtime_resolvable",
                    "target_path": "/jobs/{job_id}",
                }
            ]
            if with_binding
            else []
        ),
        "precondition_plan": [],
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": "step_result",
                "operation_ref": "op_result",
                "actor_ref": "actor_1",
                "path": "/jobs/result",
                "wait_contract": {
                    "wait_id": "wait_job_ready",
                    "path_template": "/jobs/{job_id}/status",
                },
            }
        ],
    }


def test_wait_path_binding_missing_blocks_flow_data_freeze() -> None:
    source = _experiment(with_binding=False)
    projected = _requirement_projection_input(source)

    requirement = build_flow_data_requirement(projected, behavior_ir=IR)

    assert requirement["status"] == "BLOCKED"
    assert requirement["reason_code"] == BLOCKED_FLOW_DATA_BINDING_INCOMPLETE
    assert "treatment:step_result:job_id" in requirement["detail"]
    assert source["treatment_plan"][0].get("input_binding_refs") is None


def test_wait_path_binding_is_frozen_without_mutating_compiled_step() -> None:
    source = _experiment(with_binding=True)
    before = deepcopy(source)

    projected = _requirement_projection_input(source)
    requirement = build_flow_data_requirement(projected, behavior_ir=IR)

    assert requirement["status"] == "FROZEN"
    step = requirement["step_requirements"][0]
    assert step["required_binding_targets"] == ["job_id"]
    assert step["declared_input_binding_targets"] == ["job_id"]
    assert requirement["materialized_before_measurement_targets"] == ["job_id"]
    assert source == before
    assert projected is not source
    assert projected["treatment_plan"][0]["wait_binding_targets"] == ["job_id"]
