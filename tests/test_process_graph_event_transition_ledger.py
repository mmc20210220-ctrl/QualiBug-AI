from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center import experiment_plan_step_executor as step_kernel
from ai_test_asset_center.process_graph_event_transition import (
    RECEIPT_SCHEMA_VERSION,
)
from ai_test_asset_center.process_step_execution import ProcessStepLedger


def _step() -> dict:
    return {
        "step_id": "consume_notification",
        "operation_ref": "op_consume",
        "actor_ref": "actor_1",
        "method": "GET",
        "path": "/notifications/{order_id}",
        "wait_contract": {"wait_id": "wait_order_created"},
        "_execution_graph": {
            "execution_graph_id": "graph_event_ledger",
            "wait_contracts_by_target": {
                "consume_notification": {"wait_id": "wait_order_created"}
            },
        },
    }


def _kwargs(observations: dict | None = None) -> dict:
    return {
        "control_plan": [],
        "treatment_plan": [_step()],
        "consumed_barrier_steps": set(),
        "actors": {"actor_1": {"role": "public"}},
        "ops": {
            "op_consume": {
                "id": "op_consume",
                "method": "GET",
                "path": "/notifications/{order_id}",
            }
        },
        "tokens": {},
        "runtime_bindings": {"order_id": "ORD-42", "request_id": "REQ-1"},
        "activation_requirements": {
            "control": [],
            "treatment": ["consume_notification"],
        },
        "observations": observations if observations is not None else {},
        "eid": "exp_event_ledger",
        "oid": "obl_event_ledger",
        "resolved_campaign_id": "campaign_event_ledger",
        "resolved_execution_id": "execution_event_ledger",
        "campaign_id": "campaign_event_ledger",
        "root": Path("."),
        "project": "project_event_ledger",
        "base_url": "https://notifications.example.test",
        "runtime_contract": {},
        "cleanup_failures": 0,
    }


def _receipt(*, status: str, semantic_status: str, reason_code: str = "") -> dict:
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": status,
        "semantic_status": semantic_status,
        "reason_code": reason_code,
        "step_id": "consume_notification",
        "source_node_id": "submit_order",
        "target_node_id": "consume_notification",
        "wait_id": "wait_order_created",
        "receipt_id": "event_wait_receipt_1",
        "observer_operation_ref": "op_event_observer",
        "delivery_kind": "message",
        "attempt_count": 3,
        "timed_out": False,
        "converged": status == "CONVERGED",
        "observed_unique_event_count": 2 if reason_code else 1,
        "idempotency_mismatch_count": 0,
        "retry_limit_violation_count": 0,
    }


def _successful_child() -> dict:
    ledger = ProcessStepLedger(
        experiment_id="exp_event_ledger",
        campaign_id="campaign_event_ledger",
        run_id="execution_event_ledger",
        obligation_id="obl_event_ledger",
        required_step_ids=["consume_notification"],
    )
    ledger.record_step_execution(
        step_id="consume_notification",
        phase="treatment",
        operation_ref="op_consume",
        actor_ref="actor_1",
        runtime_identity={"order_id": "ORD-42"},
        request_receipt_id="request_1",
        response_receipt_id="response_1",
        transport_receipt_id="transport_1",
        status_code=200,
        final_status="EXECUTED",
    )
    ledger.record_timeline_event(
        step_id="consume_notification",
        phase="treatment",
        event_type="STEP_COMPLETED",
        operation_ref="op_consume",
        actor_ref="actor_1",
        receipt_id="transport_1",
    )
    return {
        "steps": [
            {
                "phase": "treatment",
                "step_id": "consume_notification",
                "status_code": 200,
            }
        ],
        "contract_evidence_receipts": [],
        "request_bodies_for_cleanup": {},
        "pre_transport_block_reasons": [],
        "cleanup_failures": 0,
        "process_step_ledger": ledger,
    }


def test_verified_event_precedes_business_transport_in_same_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations: dict = {}
    monkeypatch.setattr(
        step_kernel,
        "execute_process_graph_wait",
        lambda **_: _receipt(status="CONVERGED", semantic_status="PASS"),
    )
    monkeypatch.setattr(
        step_kernel._core,
        "execute_non_barrier_plans",
        lambda **_: _successful_child(),
    )

    result = step_kernel.execute_non_barrier_plans(**_kwargs(observations))

    events = result["process_timeline"]["events"]
    assert [row["event_type"] for row in events] == [
        "ASYNC_EVENT_VERIFIED",
        "STEP_COMPLETED",
    ]
    row = result["process_step_ledger"].get_step_row("consume_notification")
    assert "event_wait_receipt_1" in row["scoped_observation_receipt_ids"]
    assert observations["process_graph_wait_receipts"][0]["receipt_id"] == (
        "event_wait_receipt_1"
    )
    assert observations["process_graph_async_transition_receipts"][0][
        "receipt_id"
    ] == "event_wait_receipt_1"


def test_event_violation_blocks_target_transport_and_keeps_scoped_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations: dict = {}
    monkeypatch.setattr(
        step_kernel,
        "execute_process_graph_wait",
        lambda **_: _receipt(
            status="BLOCKED",
            semantic_status="VIOLATION",
            reason_code="PROCESS_GRAPH_EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM",
        ),
    )
    monkeypatch.setattr(
        step_kernel._core,
        "execute_non_barrier_plans",
        lambda **_: pytest.fail("target transport must not run"),
    )

    result = step_kernel.execute_non_barrier_plans(**_kwargs(observations))

    assert result["steps"][0]["status"] == "blocked_request"
    assert result["steps"][0]["async_transition_kind"] == "message"
    assert result["process_timeline"]["events"][0]["event_type"] == (
        "ASYNC_EVENT_FAILED"
    )
    row = result["process_step_ledger"].get_step_row("consume_notification")
    assert "event_wait_receipt_1" in row["scoped_observation_receipt_ids"]
    evidence = result["contract_evidence_receipts"][0]["evidence"]
    assert evidence["request_reached_transport"] is False
    assert evidence["async_semantic_status"] == "VIOLATION"
    assert evidence["observed_unique_event_count"] == 2
    assert observations["process_graph_async_transition_receipts"][0][
        "receipt_id"
    ] == "event_wait_receipt_1"
