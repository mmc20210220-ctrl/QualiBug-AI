from __future__ import annotations

from copy import deepcopy
import json

import pytest

from ai_test_asset_center import experiment_plan_step_executor as executor
from ai_test_asset_center.contract_oracles import build_contract_evidence_receipt
from ai_test_asset_center.process_graph_cleanup_executor_core import (
    GRAPH_CLEANUP_SCHEMA,
)
from ai_test_asset_center.process_step_execution import (
    ProcessStepLedger,
    attach_ledger_refs_to_observations,
)

RUN_ID = "run-current"
SOURCE_STEP = "submit_order"
TARGET_STEP = "consume_notification"
ROLLBACK_FINGERPRINT = "rollback-contract-fingerprint"


def _step() -> dict:
    event = {
        "wait_id": "wait_order_created",
        "source_node_id": SOURCE_STEP,
        "target_node_id": TARGET_STEP,
        "contract_fingerprint": "event-contract-fingerprint",
        "delivery_kind": "message",
        "delivery_semantics": "exactly_once",
    }
    wait = {
        "wait_id": "wait_order_created",
        "transition_kind": "event_delivery",
        "source_node_id": SOURCE_STEP,
        "target_node_id": TARGET_STEP,
        "contract_fingerprint": "wait-contract-fingerprint",
        "event_transition_contract": event,
    }
    return {
        "step_id": TARGET_STEP,
        "node_id": TARGET_STEP,
        "operation_ref": "op_consume_notification",
        "actor_ref": "actor-service",
        "system_ref": "notifications",
        "method": "GET",
        "path": "/notifications/{order_id}",
        "wait_contract": wait,
        "_execution_graph": {
            "execution_graph_id": "graph-order-events",
            "process_id": "process-order-events",
            "rollback_contract": {
                "contract_fingerprint": ROLLBACK_FINGERPRINT,
            },
            "wait_contracts": [deepcopy(wait)],
            "wait_contracts_by_target": {TARGET_STEP: deepcopy(wait)},
        },
    }


def _cleanup_receipt(*, subject_id: str = "cleanup_submit_order") -> dict:
    return build_contract_evidence_receipt(
        kind="cleanup",
        experiment_id="exp-order-events",
        obligation_id="obl-order-events",
        campaign_id="campaign-order-events",
        execution_id=RUN_ID,
        subject_id=subject_id,
        status="COMPLETED",
        evidence={
            "schema_version": GRAPH_CLEANUP_SCHEMA,
            "source_step_id": SOURCE_STEP,
            "system_ref": "orders",
            "operation_ref": "op_delete_order",
            "request_reached_transport": True,
        },
    )


def _persisted_observations(
    receipts: list[dict],
    *,
    execution_id: str = RUN_ID,
    scope_receipts: bool = True,
) -> dict:
    ledger = ProcessStepLedger(
        experiment_id="exp-order-events",
        fixture_id="fixture-order-events",
        campaign_id="campaign-order-events",
        run_id=execution_id,
        obligation_id="obl-order-events",
        protocol_id="protocol-order-events",
        required_step_ids=[SOURCE_STEP],
    )
    ledger.record_step_execution(
        step_id=SOURCE_STEP,
        phase="treatment",
        operation_ref="op_submit_order",
        actor_ref="actor-service",
        runtime_identity={"order_id": "ORD-42"},
        request_receipt_id="request-submit-order",
        response_receipt_id="response-submit-order",
        transport_receipt_id="transport-submit-order",
        after_state_receipt_id="observer-submit-order",
        observer_receipt_ids=["observer-submit-order"],
        cleanup_contract_id="cleanup-contract-submit-order",
        status_code=201,
        final_status="EXECUTED",
        mutation_occurred=True,
        operation_accepted=True,
        business_effect_observed=True,
        target_reached=True,
    )
    if scope_receipts:
        for receipt in receipts:
            ledger.append_scoped_receipt_ref(
                step_id=SOURCE_STEP,
                field="cleanup_receipt_ids",
                receipt_id=receipt["receipt_id"],
                receipt_step_id=SOURCE_STEP,
            )

    observations: dict = {}
    attach_ledger_refs_to_observations(observations, ledger)
    observations.pop("process_step_ledger", None)
    observations["contract_evidence_receipts"] = deepcopy(receipts)
    observations["process_graph_rollback_contract_id"] = ROLLBACK_FINGERPRINT
    observations["process_graph_runtime"] = {
        "execution_graph_id": "graph-order-events",
        "process_id": "process-order-events",
    }
    return json.loads(json.dumps(observations))


def _wait_kwargs(observations: dict) -> dict:
    return {
        "control_plan": [],
        "treatment_plan": [_step()],
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


def _converged_receipt() -> dict:
    return {
        "schema_version": "qualibug.process-graph-event-transition-receipt.v1",
        "status": "CONVERGED",
        "semantic_status": "PASS",
        "reason_code": "",
        "semantic_reason_codes": [],
        "step_id": TARGET_STEP,
        "wait_id": "wait_order_created",
        "source_node_id": SOURCE_STEP,
        "target_node_id": TARGET_STEP,
        "contract_fingerprint": "event-contract-fingerprint",
        "receipt_id": "event_wait_current",
        "attempt_count": 1,
        "coverage_complete": True,
        "observation_window_completed": True,
        "converged": True,
        "timed_out": False,
    }


def test_restart_recovers_ledger_scoped_cleanup_and_blocks_observer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup = _cleanup_receipt()
    observations = _persisted_observations([cleanup])
    calls = {"observer": 0}

    def forbidden_observer(**kwargs):
        calls["observer"] += 1
        raise AssertionError("persisted terminal branch must not observe")

    monkeypatch.setattr(executor, "execute_process_graph_wait", forbidden_observer)
    _resolved, receipt = executor._wait_step(_wait_kwargs(observations))

    assert calls["observer"] == 0
    assert receipt["reason_code"] == executor.WAIT_TERMINATION_EPOCH_ACTIVE
    assert receipt["termination_epoch_authority"] == (
        "process_step_ledger_cleanup_receipts"
    )
    assert receipt["termination_cleanup_receipt_ids"] == [cleanup["receipt_id"]]
    assert receipt["termination_recovery_ledger_id"] == (
        observations["process_step_ledger_id"]
    )
    assert receipt["termination_recovery_ledger_hash"] == (
        observations["process_step_ledger_hash"]
    )
    assert receipt["observer_request_reached_transport"] is False
    recovery = observations["process_graph_wait_termination_recovery"]
    assert recovery["source_step_id"] == SOURCE_STEP
    assert recovery["cleanup_receipt_ids"] == [cleanup["receipt_id"]]


def test_persisted_unscoped_cleanup_body_cannot_terminate_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = _persisted_observations(
        [_cleanup_receipt()],
        scope_receipts=False,
    )
    calls = {"observer": 0}

    def observer(**kwargs):
        calls["observer"] += 1
        return _converged_receipt()

    monkeypatch.setattr(executor, "execute_process_graph_wait", observer)
    _resolved, receipt = executor._wait_step(_wait_kwargs(observations))

    assert calls["observer"] == 1
    assert receipt["status"] == "CONVERGED"


def test_missing_persisted_cleanup_body_fails_closed_before_observer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup = _cleanup_receipt()
    observations = _persisted_observations([cleanup])
    observations["contract_evidence_receipts"] = []

    monkeypatch.setattr(
        executor,
        "execute_process_graph_wait",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("missing receipt body must fail closed")
        ),
    )
    _resolved, receipt = executor._wait_step(_wait_kwargs(observations))

    assert receipt["reason_code"] == executor.WAIT_TERMINATION_RECEIPT_INVALID
    assert receipt["detail"] == (
        "persisted_cleanup_receipt_body_missing:" + cleanup["receipt_id"]
    )
    assert receipt["termination_epoch_authority"] == (
        "process_step_ledger_cleanup_receipts"
    )


def test_persisted_step_fact_drift_fails_closed_before_observer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = _persisted_observations([_cleanup_receipt()])
    observations["process_step_receipts"][0]["actor_ref"] = "foreign-actor"

    monkeypatch.setattr(
        executor,
        "execute_process_graph_wait",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("drifted ledger must fail closed")
        ),
    )
    _resolved, receipt = executor._wait_step(_wait_kwargs(observations))

    assert receipt["reason_code"] == executor.WAIT_TERMINATION_RECEIPT_INVALID
    assert receipt["detail"] == "persisted_step_fact_hash_mismatch"


def test_persisted_cleanup_from_old_execution_does_not_block_current_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = _persisted_observations(
        [_cleanup_receipt()],
        execution_id="run-old",
    )
    calls = {"observer": 0}

    def observer(**kwargs):
        calls["observer"] += 1
        return _converged_receipt()

    monkeypatch.setattr(executor, "execute_process_graph_wait", observer)
    _resolved, receipt = executor._wait_step(_wait_kwargs(observations))

    assert calls["observer"] == 1
    assert receipt["status"] == "CONVERGED"


def test_conflicting_persisted_receipt_bodies_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup = _cleanup_receipt()
    observations = _persisted_observations([cleanup])
    conflicting = deepcopy(cleanup)
    conflicting["status"] = "FAILED"
    observations["contract_evidence_receipts"].append(conflicting)

    monkeypatch.setattr(
        executor,
        "execute_process_graph_wait",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("conflicting receipt bodies must fail closed")
        ),
    )
    _resolved, receipt = executor._wait_step(_wait_kwargs(observations))

    assert receipt["reason_code"] == executor.WAIT_TERMINATION_RECEIPT_INVALID
    assert receipt["detail"] == (
        "persisted_cleanup_receipt_conflict:" + cleanup["receipt_id"]
    )


def test_recovered_epoch_is_stable_across_persisted_body_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _cleanup_receipt(subject_id="cleanup-submit-order-a")
    second = _cleanup_receipt(subject_id="cleanup-submit-order-b")
    observations_a = _persisted_observations([second, first])
    observations_b = _persisted_observations([first, second])

    monkeypatch.setattr(
        executor,
        "execute_process_graph_wait",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("recovered terminal branch must not observe")
        ),
    )
    _resolved_a, receipt_a = executor._wait_step(_wait_kwargs(observations_a))
    _resolved_b, receipt_b = executor._wait_step(_wait_kwargs(observations_b))

    assert receipt_a["termination_cleanup_receipt_ids"] == sorted(
        [first["receipt_id"], second["receipt_id"]]
    )
    assert receipt_a["termination_epoch_contract_fingerprint"] == (
        receipt_b["termination_epoch_contract_fingerprint"]
    )
    assert receipt_a["receipt_id"] == receipt_b["receipt_id"]
