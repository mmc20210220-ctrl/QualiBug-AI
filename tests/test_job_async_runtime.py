from __future__ import annotations

from ai_test_asset_center.job_async_runtime import (
    JOB_ASYNC_CONVERGENCE_TIMEOUT,
    JOB_TERMINAL_FAILURE,
    JOB_WRITE_CLEANUP_EXECUTION_NOT_CLOSED,
    execute_registered_job_operation,
)
from ai_test_asset_center.job_platform_contract import register_job_platform_adapter


class FakeJobAdapter:
    adapter_kind = "fake_job_runtime"

    def __init__(self, states: list[str], *, return_run_id: bool = True) -> None:
        self.states = list(states)
        self.return_run_id = return_run_id
        self.trigger_count = 0
        self.poll_count = 0
        self.cancel_count = 0

    def list_jobs(self, connector):
        return []

    def get_job_definition(self, connector, platform_job_id):
        return {}

    def trigger_job(self, connector, platform_job_id, parameters):
        self.trigger_count += 1
        return {
            "status_code": 202,
            **({"job_run_id": "run-1"} if self.return_run_id else {}),
        }

    def get_job_run(self, connector, platform_job_id, job_run_id):
        index = min(self.poll_count, len(self.states) - 1)
        state = self.states[index]
        self.poll_count += 1
        return {
            "status_code": 200,
            "status": state,
            "terminal": state in {"SUCCESS", "FAILED"},
        }

    def list_job_steps(self, connector, platform_job_id, job_run_id):
        return [{"step_id": "remote-step-1", "status": "SUCCESS"}]

    def get_job_log(self, connector, platform_job_id, job_run_id):
        return {"status_code": 200, "content_hash": "log-hash"}

    def cancel_job(self, connector, platform_job_id, job_run_id):
        self.cancel_count += 1
        return {"status_code": 202, "cancelled": True}


def _operation(*, write_set: list[str] | None = None, max_attempts: int = 3) -> dict:
    writes = list(write_set or [])
    return {
        "operation_id": "business_operation:job",
        "operation_kind": "ASYNC_JOB",
        "evidence": [{"connector_id": "conn-job", "source_id": "job-source"}],
        "async_contract": {
            "platform_type": "fake_job_runtime",
            "platform_job_id": "job-1",
            "runtime": {
                "terminal_states": ["SUCCESS", "FAILED"],
                "success_states": ["SUCCESS"],
                "step_query_ref": "adapter:list_job_steps",
                "log_query_ref": "adapter:get_job_log",
                "cancel_ref": "adapter:cancel_job",
            },
            "execution_policy": {"max_attempts": max_attempts, "backoff_ms": 0},
            "write_set": writes,
            "testability": {
                "execution_status": "EXECUTION_READY",
                "safety_level": "READ_ONLY" if not writes else "REVERSIBLE_WRITE",
            },
        },
    }


def _registry() -> dict:
    return {
        "connectors": [
            {
                "connector_id": "conn-job",
                "enabled": True,
                "kind": "http_api",
                "external_ref": "job_platform:fake_job_runtime",
            }
        ]
    }


def test_read_only_job_triggers_once_polls_to_success_and_emits_existing_receipts() -> None:
    adapter = FakeJobAdapter(["RUNNING", "SUCCESS"])
    register_job_platform_adapter("fake_job_runtime", adapter)

    result = execute_registered_job_operation(
        operation=_operation(),
        connector_registry=_registry(),
        parameters={"fixture_id": "fixture-1"},
        step_id="job_treatment_1",
        sleeper=lambda _: None,
    )

    assert result["ok"] is True
    assert result["job_run_id"] == "run-1"
    assert result["terminal_status"] == "SUCCESS"
    assert adapter.trigger_count == 1
    assert adapter.poll_count == 2
    assert adapter.cancel_count == 0
    assert result["formal_business_finding_eligible"] is False
    assert result["receipts"]["transport"]["receipt_id"]
    assert result["receipts"]["observation"]["step_id"] == "job_treatment_1"
    assert result["receipts"]["cleanup_execution"]["cleanup_status"] == "NOT_REQUIRED"
    assert result["receipts"]["environment_restoration"]["restored"] is True


def test_write_job_is_blocked_before_adapter_trigger() -> None:
    adapter = FakeJobAdapter(["SUCCESS"])
    register_job_platform_adapter("fake_job_runtime", adapter)

    result = execute_registered_job_operation(
        operation=_operation(write_set=["order.status"]),
        connector_registry=_registry(),
        parameters={},
        step_id="job_treatment_1",
        sleeper=lambda _: None,
    )

    assert result["ok"] is False
    assert result["reason_code"] == JOB_WRITE_CLEANUP_EXECUTION_NOT_CLOSED
    assert adapter.trigger_count == 0


def test_timeout_never_retriggers_mutating_job_and_cancels_at_most_once() -> None:
    adapter = FakeJobAdapter(["RUNNING"])
    register_job_platform_adapter("fake_job_runtime", adapter)

    result = execute_registered_job_operation(
        operation=_operation(max_attempts=2),
        connector_registry=_registry(),
        parameters={},
        step_id="job_treatment_1",
        sleeper=lambda _: None,
    )

    assert result["ok"] is False
    assert result["reason_code"] == JOB_ASYNC_CONVERGENCE_TIMEOUT
    assert adapter.trigger_count == 1
    assert adapter.poll_count == 2
    assert adapter.cancel_count == 1


def test_terminal_failure_is_runtime_integrity_failure_not_business_finding() -> None:
    adapter = FakeJobAdapter(["FAILED"])
    register_job_platform_adapter("fake_job_runtime", adapter)

    result = execute_registered_job_operation(
        operation=_operation(),
        connector_registry=_registry(),
        parameters={},
        step_id="job_treatment_1",
        sleeper=lambda _: None,
    )

    assert result["ok"] is False
    assert result["reason_code"] == JOB_TERMINAL_FAILURE
    assert result["terminal_status"] == "FAILED"
    assert result["formal_business_finding_eligible"] is False
    assert result["receipts"]["oracle_invocation"]["passed"] is False
