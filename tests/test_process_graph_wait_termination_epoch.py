from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from ai_test_asset_center import experiment_plan_step_executor as executor
from ai_test_asset_center.contract_oracles import build_contract_evidence_receipt
from ai_test_asset_center.process_graph_async_transition_observer import (
    EVIDENCE_KEY,
    observe_async_transitions,
)
from ai_test_asset_center.process_graph_cleanup_executor_core import (
    GRAPH_CLEANUP_SCHEMA,
)
from ai_test_asset_center.process_graph_event_transition import (
    RECEIPT_SCHEMA_VERSION as EVENT_RECEIPT_SCHEMA_VERSION,
)


RUN_ID = "run-current"
SOURCE_STEP = "submit_order"
TARGET_STEP = "consume_notification"
WAIT_FINGERPRINT = "wait-contract-fingerprint"
EVENT_FINGERPRINT = "event-contract-fingerprint"
ROLLBACK_FINGERPRINT = "rollback-contract-fingerprint"


def _step(*, source_step_id: str = SOURCE_STEP) -> dict:
    event_contract = {
        "wait_id": "wait_order_created",
        "source_node_id": source_step_id,
        "target_node_id": TARGET_STEP,
        "contract_fingerprint": EVENT_FINGERPRINT,
        "delivery_kind": "message",
        "delivery_semantics": "exactly_once",
    }
    wait_contract = {
        "wait_id": "wait_order_created",
        "transition_kind": "event_delivery",
        "source_node_id": source_step_id,
        "target_node_id": TARGET_STEP,
        "contract_fingerprint": WAIT_FINGERPRINT,
        "event_transition_contract": event_contract,
    }
    graph = {
        "execution_graph_id": "graph-order-events",
        "process_id": "process-order-events",
        "rollback_contract": {
            "contract_fingerprint": ROLLBACK_FINGERPRINT,
        },
        "wait_contracts": [deepcopy(wait_contract)],
        "wait_contracts_by_target": {TARGET_STEP: deepcopy(wait_contract)},
    }
    return {
        "step_id": TARGET_STEP,
        "node_id": TARGET_STEP,
        "operation_ref": "op_consume_notification",
        "actor_ref": "actor-service",
        "system_ref": "notifications",
        "method": "GET",
        "path": "/notifications/{order_id}",
        "wait_contract": wait_contract,
        "_execution_graph": graph,
    }


def _cleanup_receipt(
    *,
    source_step_id: str = SOURCE_STEP,
    execution_id: str = RUN_ID,
    status: str = "COMPLETED",
) -> dict:
    return build_contract_evidence_receipt(
        kind="cleanup",
        experiment_id="exp-order-events",
        obligation_id="obl-order-events",
        campaign_id="campaign-order-events",
        execution_id=execution_id,
        subject_id=f"cleanup_{source_step_id}",
        status=status,
        evidence={
            "schema_version": GRAPH_CLEANUP_SCHEMA,
            "source_step_id": source_step_id,
            "system_ref": "orders",
            "operation_ref": "op_delete_order",
            "request_reached_transport": status == "COMPLETED",
        },
    )


def _observations(receipts: list[dict]) -> dict:
    return {
        "process_graph_cleanup_receipts": receipts,
        "process_graph_rollback_contract_id": ROLLBACK_FINGERPRINT,
        "process_graph_runtime": {
            "execution_graph_id": "graph-order-events",
            "process_id": "process-order-events",
        },
    }


def _wait_kwargs(*, step: dict, observations: dict) -> dict:
    return {
        "control_plan": [],
        "treatment_plan": [step],
        "runtime_bindings": {
            "order_id": "ORD-42",
            "request_id": "REQ-CURRENT",
        },
        "observations": observations,
        "eid": "exp-order-events",
        "oid": "obl-order-events",
        "resolved_campaign_id": "campaign-order-events",
        "resolved_execution_id": RUN_ID,
        "actors": {"actor-service": {"role": "service"}},
        "tokens": {},
        "base_url": "https://notifications.example.test",
    }


def _public_kwargs(*, step: dict, observations: dict) -> dict:
    return {
        **_wait_kwargs(step=step, observations=observations),
        "consumed_barrier_steps": set(),
        "ops": {},
        "activation_requirements": {
            "control": [],
            "treatment": [TARGET_STEP],
        },
        "campaign_id": "campaign-order-events",
        "root": Path("."),
        "project": "project-order-events",
        "runtime_contract": {},
        "cleanup_failures": 0,
    }


def _converged_event_receipt(step: dict) -> dict:
    event = step["wait_contract"]["event_transition_contract"]
    return {
        "schema_version": EVENT_RECEIPT_SCHEMA_VERSION,
        "status": "CONVERGED",
        "semantic_status": "PASS",
        "reason_code": "",
        "semantic_reason_codes": [],
        "step_id": TARGET_STEP,
        "wait_id": event["wait_id"],
        "source_node_id": event["source_node_id"],
        "target_node_id": TARGET_STEP,
        "contract_fingerprint": event["contract_fingerprint"],
        "receipt_id": "event_wait_current",
        "attempt_count": 1,
        "coverage_complete": True,
        "observation_window_completed": True,
        "converged": True,
        "timed_out": False,
    }


def test_matching_cleanup_epoch_blocks_late_event_before_all_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _step()
    cleanup = _cleanup_receipt()
    observations = _observations([cleanup])
    calls = {"observer": 0, "business": 0}

    def forbidden_observer(**kwargs):
        calls["observer"] += 1
        raise AssertionError("late event observer must not execute")

    def forbidden_business(**kwargs):
        calls["business"] += 1
        raise AssertionError("terminated branch business transport must not execute")

    monkeypatch.setattr(executor, "execute_process_graph_wait", forbidden_observer)
    monkeypatch.setattr(executor._core, "execute_non_barrier_plans", forbidden_business)

    result = executor.execute_non_barrier_plans(
        **_public_kwargs(step=step, observations=observations)
    )

    assert calls == {"observer": 0, "business": 0}
    assert result["steps"][0]["status"] == "blocked_request"
    assert result["steps"][0]["reason"] == (
        executor.WAIT_TERMINATION_EPOCH_ACTIVE
    )
    receipt = result["process_graph_async_transition_receipts"][0]
    assert receipt["status"] == "BLOCKED"
    assert receipt["semantic_status"] == "INDETERMINATE"
    assert receipt["contract_fingerprint"] == EVENT_FINGERPRINT
    assert receipt["termination_epoch_authority"] == (
        "process_graph_cleanup_receipts"
    )
    assert receipt["termination_cleanup_receipt_ids"] == [
        cleanup["receipt_id"]
    ]
    assert receipt["observer_request_reached_transport"] is False
    assert receipt["request_reached_transport"] is False
    epoch = receipt["termination_epoch_contract"]
    assert epoch["execution_id"] == RUN_ID
    assert epoch["source_node_id"] == SOURCE_STEP
    assert epoch["target_node_id"] == TARGET_STEP
    assert epoch["wait_contract_fingerprint"] == WAIT_FINGERPRINT
    assert epoch["event_contract_fingerprint"] == EVENT_FINGERPRINT
    assert epoch["rollback_contract_fingerprint"] == ROLLBACK_FINGERPRINT
    assert observations["process_graph_async_transition_receipts"] == [receipt]
    evidence = result["contract_evidence_receipts"][0]["evidence"]
    assert evidence["request_reached_transport"] is False
    assert evidence["termination_epoch_contract_fingerprint"] == (
        receipt["termination_epoch_contract_fingerprint"]
    )
    assert evidence["termination_cleanup_receipt_ids"] == [
        cleanup["receipt_id"]
    ]

    observer = observe_async_transitions(
        {
            "experiment": {"execution_graph": step["_execution_graph"]},
            "observations": observations,
        }
    )
    assert observer["status"] == "INDETERMINATE"
    transitions = observer["evidence"][EVIDENCE_KEY]["transitions"]
    assert len(transitions) == 1
    assert transitions[0]["reason_code"] == (
        executor.WAIT_TERMINATION_EPOCH_ACTIVE
    )
    assert transitions[0]["termination_epoch_authority"] == (
        "process_graph_cleanup_receipts"
    )
    assert transitions[0]["termination_epoch_contract_fingerprint"] == (
        receipt["termination_epoch_contract_fingerprint"]
    )
    assert transitions[0]["termination_cleanup_receipt_ids"] == [
        cleanup["receipt_id"]
    ]


def test_termination_epoch_is_branch_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _step()
    sibling_cleanup = _cleanup_receipt(source_step_id="charge_payment")
    calls = {"observer": 0}

    def observe(**kwargs):
        calls["observer"] += 1
        return _converged_event_receipt(step)

    monkeypatch.setattr(executor, "execute_process_graph_wait", observe)
    _resolved_step, receipt = executor._wait_step(
        _wait_kwargs(
            step=step,
            observations=_observations([sibling_cleanup]),
        )
    )

    assert calls["observer"] == 1
    assert receipt["status"] == "CONVERGED"


def test_termination_epoch_is_execution_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _step()
    old_run_cleanup = _cleanup_receipt(execution_id="run-old")
    calls = {"observer": 0}

    def observe(**kwargs):
        calls["observer"] += 1
        return _converged_event_receipt(step)

    monkeypatch.setattr(executor, "execute_process_graph_wait", observe)
    _resolved_step, receipt = executor._wait_step(
        _wait_kwargs(
            step=step,
            observations=_observations([old_run_cleanup]),
        )
    )

    assert calls["observer"] == 1
    assert receipt["status"] == "CONVERGED"


def test_matching_malformed_cleanup_receipt_fails_closed_before_observer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _step()
    malformed = _cleanup_receipt()
    malformed["receipt_id"] = "contract_tampered"
    calls = {"observer": 0}

    def forbidden_observer(**kwargs):
        calls["observer"] += 1
        raise AssertionError("invalid terminal authority must fail closed")

    monkeypatch.setattr(executor, "execute_process_graph_wait", forbidden_observer)
    _resolved_step, receipt = executor._wait_step(
        _wait_kwargs(
            step=step,
            observations=_observations([malformed]),
        )
    )

    assert calls["observer"] == 0
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == (
        executor.WAIT_TERMINATION_RECEIPT_INVALID
    )
    assert receipt["detail"].startswith("cleanup_receipt_invalid:")
    assert receipt["observer_request_reached_transport"] is False


def test_termination_epoch_fingerprint_is_order_and_duplicate_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _step()
    first = _cleanup_receipt(status="COMPLETED")
    second = _cleanup_receipt(status="FAILED")

    def forbidden_observer(**kwargs):
        raise AssertionError("terminal epoch must block before observation")

    monkeypatch.setattr(executor, "execute_process_graph_wait", forbidden_observer)
    _step_a, receipt_a = executor._wait_step(
        _wait_kwargs(
            step=step,
            observations=_observations([second, first, second]),
        )
    )
    _step_b, receipt_b = executor._wait_step(
        _wait_kwargs(
            step=step,
            observations=_observations([first, second]),
        )
    )

    assert receipt_a["termination_cleanup_receipt_ids"] == sorted(
        {first["receipt_id"], second["receipt_id"]}
    )
    assert receipt_a["termination_epoch_contract_fingerprint"] == (
        receipt_b["termination_epoch_contract_fingerprint"]
    )
    assert receipt_a["receipt_id"] == receipt_b["receipt_id"]


def test_matching_cleanup_with_rollback_scope_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _step()
    observations = _observations([_cleanup_receipt()])
    observations["process_graph_rollback_contract_id"] = "other-rollback"
    calls = {"observer": 0}

    def forbidden_observer(**kwargs):
        calls["observer"] += 1
        raise AssertionError("rollback drift must fail closed")

    monkeypatch.setattr(executor, "execute_process_graph_wait", forbidden_observer)
    _resolved_step, receipt = executor._wait_step(
        _wait_kwargs(step=step, observations=observations)
    )

    assert calls["observer"] == 0
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == executor.WAIT_TERMINATION_RECEIPT_INVALID
    assert receipt["detail"] == (
        "cleanup_termination_rollback_contract_mismatch"
    )


def test_cleanup_from_other_graph_does_not_terminate_current_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _step()
    observations = _observations([_cleanup_receipt()])
    observations["process_graph_runtime"]["execution_graph_id"] = "other-graph"
    calls = {"observer": 0}

    def observe(**kwargs):
        calls["observer"] += 1
        return _converged_event_receipt(step)

    monkeypatch.setattr(executor, "execute_process_graph_wait", observe)
    _resolved_step, receipt = executor._wait_step(
        _wait_kwargs(step=step, observations=observations)
    )

    assert calls["observer"] == 1
    assert receipt["status"] == "CONVERGED"
