from __future__ import annotations

from ai_test_asset_center.process_graph_runtime import record_graph_step_outcome


def _runtime() -> dict:
    return {
        "node_status": {"step_target": "PENDING"},
        "nodes": {
            "step_target": {
                "node_id": "step_target",
                "output_binding_specs": [],
            }
        },
        "binding_ledger": {"outputs_by_node": {}, "unresolved": []},
    }


def test_explicit_zero_transport_wait_timeout_stays_blocked() -> None:
    runtime = _runtime()

    outcome = record_graph_step_outcome(
        runtime=runtime,
        graph={},
        step={"step_id": "step_target"},
        observation={
            "status": "blocked_request",
            "status_code": 0,
            "reason": "READBACK_ASYNC_TIMEOUT",
            "detail": "state_not_ready",
            "request_reached_transport": False,
        },
    )

    assert outcome["status"] == "BLOCKED"
    assert outcome["reason_code"] == "READBACK_ASYNC_TIMEOUT"
    assert outcome["request_reached_transport"] is False
    assert runtime["node_status"]["step_target"] == "BLOCKED"


def test_plain_transport_failure_remains_failed() -> None:
    runtime = _runtime()

    outcome = record_graph_step_outcome(
        runtime=runtime,
        graph={},
        step={"step_id": "step_target"},
        observation={
            "status_code": 0,
            "error": "connection_reset",
        },
    )

    assert outcome["status"] == "FAILED"
    assert outcome["status_code"] == 0
    assert runtime["node_status"]["step_target"] == "FAILED"
