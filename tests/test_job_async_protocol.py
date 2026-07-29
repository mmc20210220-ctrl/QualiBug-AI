from __future__ import annotations

from ai_test_asset_center.job_async_protocol import (
    ASYNC_JOB_NOT_EXECUTION_READY,
    TEMPLATE_ASYNC_JOB_EXECUTION,
    compile_async_job_protocol,
)


def _envelope(*, execution_status: str = "EXECUTION_READY") -> dict:
    return {
        "risk_family": "process",
        "operation_ref": "business_operation:job",
        "treatment_actor_ref": "actor:test_runner",
        "operation": {
            "operation_id": "business_operation:job",
            "operation_kind": "ASYNC_JOB",
            "evidence": [{"source_id": "job-source", "connector_id": "conn-job"}],
            "async_contract": {
                "platform_type": "xxl_job",
                "platform_job_id": "close_expired_orders",
                "testability": {
                    "execution_status": execution_status,
                    "safety_level": "READ_ONLY",
                },
            },
        },
        "property_spec": {"template": TEMPLATE_ASYNC_JOB_EXECUTION},
        "behavior_ir": {},
    }


def test_async_job_compiler_emits_existing_one_step_treatment_contract() -> None:
    result = compile_async_job_protocol(_envelope())

    assert result["status"] == "COMPILED"
    assert result["control_plan"] == []
    assert result["treatment_plan"] == [
        {
            "step_id": "job_treatment_1",
            "step_ordinal": 1,
            "operation_ref": "business_operation:job",
            "actor_ref": "actor:test_runner",
            "method": "JOB",
            "path": "",
            "intent": "source_declared_async_job_execution",
            "protocol_step": "async_job_treatment",
        }
    ]
    assert result["assertion"]["kind"] == "process_completion"
    assert result["assertion"]["runtime_integrity_only"] is True
    assert result["assertion"]["formal_business_finding_eligible"] is False
    assert result["_registry_protocol_id"] == f"process:{TEMPLATE_ASYNC_JOB_EXECUTION}"


def test_async_job_compiler_blocks_non_ready_job_without_degrading() -> None:
    result = compile_async_job_protocol(_envelope(execution_status="PARTIALLY_EXECUTABLE"))

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == ASYNC_JOB_NOT_EXECUTION_READY
