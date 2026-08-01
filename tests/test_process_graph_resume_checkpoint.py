from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from ai_test_asset_center import experiment_plan_executor as executor
from ai_test_asset_center.process_graph_executor_support import (
    public_binding_ledger,
    runtime_projection,
)
from ai_test_asset_center.process_graph_read_runtime import record_graph_step_outcome
from ai_test_asset_center.process_graph_resume import (
    GRAPH_RESUME_EFFECT_AMBIGUOUS,
    GRAPH_RESUME_STATE_INVALID,
    build_process_graph_resume_checkpoint,
    recover_process_graph_runtime,
)
from ai_test_asset_center.process_step_execution import (
    ProcessStepLedger,
    attach_ledger_refs_to_observations,
)

EID = "exp-order-resume"
OID = "obl-order-resume"
CID = "campaign-order-resume"
RUN_ID = "run-order-resume"
GRAPH_ID = "graph-order-resume"
SOURCE = "create_order"
TARGET = "confirm_order"


def _graph() -> dict:
    return {
        "execution_graph_id": GRAPH_ID,
        "process_id": "process-order-resume",
        "topological_order": [SOURCE, TARGET],
        "nodes": [
            {
                "node_id": SOURCE,
                "step_id": SOURCE,
                "method": "POST",
                "operation_ref": "op_create_order",
                "actor_ref": "actor-orders",
                "system_ref": "orders",
                "object_refs": ["order"],
                "output_binding_specs": [
                    {
                        "canonical_field_id": "order_id",
                        "json_path": "$.id",
                    }
                ],
            },
            {
                "node_id": TARGET,
                "step_id": TARGET,
                "method": "POST",
                "operation_ref": "op_confirm_order",
                "actor_ref": "actor-orders",
                "system_ref": "orders",
                "object_refs": ["order"],
                "input_binding_refs": [
                    {
                        "producer_node_id": SOURCE,
                        "producer_output_field": "order_id",
                        "target": "order_id",
                    }
                ],
            },
        ],
        "edges": [
            {
                "source_node_id": SOURCE,
                "target_node_id": TARGET,
                "binding_refs": [
                    {
                        "producer_node_id": SOURCE,
                        "producer_output_field": "order_id",
                        "target": "order_id",
                    }
                ],
            }
        ],
    }


def _steps() -> list[dict]:
    graph = _graph()
    rows: list[dict] = []
    for raw in graph["nodes"]:
        row = deepcopy(raw)
        row["_execution_graph"] = deepcopy(graph)
        rows.append(row)
    return rows


def _target_context(method: str, decision_id: str) -> dict:
    return {
        "system_ref": "orders",
        "primary": True,
        "base_url": "https://orders.example.test",
        "runtime_contract": {},
        "target_policy_decision": {"decision_id": decision_id},
        "credential_token_key": "",
        "method": method,
    }


def _fresh_runtime() -> dict:
    graph = _graph()
    return {
        "schema_version": "qualibug.process-graph-runtime.v1",
        "status": "READY",
        "execution_graph_id": GRAPH_ID,
        "process_id": graph["process_id"],
        "topological_order": [SOURCE, TARGET],
        "predecessors": {SOURCE: [], TARGET: [SOURCE]},
        "wave_by_node": {SOURCE: 0, TARGET: 1},
        "nodes": {
            row["node_id"]: deepcopy(row) for row in graph["nodes"]
        },
        "target_contexts": {
            SOURCE: _target_context("POST", "decision-create"),
            TARGET: _target_context("POST", "decision-confirm"),
        },
        "node_status": {SOURCE: "PENDING", TARGET: "PENDING"},
        "binding_ledger": {
            "schema_version": "qualibug.process-graph-binding-ledger.v1",
            "execution_graph_id": GRAPH_ID,
            "outputs_by_node": {},
            "consumptions": [],
            "unresolved": [],
        },
    }


def _source_observation(order_id: str = "ORD-42") -> dict:
    return {
        "phase": "treatment",
        "step_id": SOURCE,
        "status": 201,
        "status_code": 201,
        "body": {"id": order_id},
        "experiment_id": EID,
        "obligation_id": OID,
        "campaign_id": CID,
        "execution_id": RUN_ID,
        "execution_graph_id": GRAPH_ID,
        "process_id": "process-order-resume",
    }


def _ledger(
    *,
    final_status: str = "EXECUTED",
    status_code: int = 201,
    operation_accepted: bool | None = True,
) -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        experiment_id=EID,
        fixture_id="fixture-order-resume",
        campaign_id=CID,
        run_id=RUN_ID,
        obligation_id=OID,
        protocol_id="protocol-order-resume",
        required_step_ids=[SOURCE, TARGET],
    )
    ledger.record_step_execution(
        step_id=SOURCE,
        phase="treatment",
        operation_ref="op_create_order",
        actor_ref="actor-orders",
        runtime_identity={"request_id": "REQ-42"},
        request_receipt_id=("request-create" if status_code else ""),
        response_receipt_id=("response-create" if status_code else ""),
        transport_receipt_id=("transport-create" if status_code else ""),
        after_state_receipt_id=("observer-create" if status_code else ""),
        observer_receipt_ids=(
            ["observer-create"] if status_code else []
        ),
        status_code=status_code,
        final_status=final_status,
        mutation_occurred=True if status_code else None,
        operation_accepted=operation_accepted,
        business_effect_observed=True if status_code else None,
        target_reached=True if final_status == "EXECUTED" else None,
    )
    return ledger


def _persisted_observations(
    *,
    runtime_status: str = "SUCCEEDED",
    ledger: ProcessStepLedger | None = None,
    graph_observations: list[dict] | None = None,
) -> dict:
    ledger = ledger or _ledger()
    runtime = _fresh_runtime()
    runtime["node_status"][SOURCE] = runtime_status
    if runtime_status == "SUCCEEDED":
        outcome = record_graph_step_outcome(
            runtime=runtime,
            graph=_graph(),
            step=_steps()[0],
            observation=_source_observation(),
        )
        assert outcome["status"] == "SUCCEEDED"
    observations: dict = {
        "disposable_fixture_contract": {
            "fixture_id": "fixture-order-resume"
        },
        "protocol_id": "protocol-order-resume",
    }
    attach_ledger_refs_to_observations(observations, ledger)
    observations.pop("process_step_ledger", None)
    observations["process_graph_runtime"] = runtime_projection(runtime)
    observations["process_graph_binding_ledger"] = public_binding_ledger(
        runtime
    )
    observations["graph_step_observations"] = deepcopy(
        graph_observations
        if graph_observations is not None
        else [_source_observation()] if runtime_status == "SUCCEEDED" else []
    )
    observations["process_graph_request_bodies_for_cleanup"] = {
        SOURCE: {"sku": "SKU-1"}
    }
    observations["process_graph_resume_checkpoint"] = (
        build_process_graph_resume_checkpoint(
            graph=_graph(),
            runtime=runtime,
            observations=observations,
            experiment_id=EID,
            obligation_id=OID,
            campaign_id=CID,
            execution_id=RUN_ID,
        )
    )
    return json.loads(json.dumps(observations))


def _recover(observations: dict, runtime: dict | None = None) -> dict:
    return recover_process_graph_runtime(
        graph=_graph(),
        treatment_plan=_steps(),
        runtime=runtime or _fresh_runtime(),
        observations=observations,
        experiment_id=EID,
        obligation_id=OID,
        campaign_id=CID,
        execution_id=RUN_ID,
    )


def test_restart_restores_completed_write_and_binding_value() -> None:
    observations = _persisted_observations()
    runtime = _fresh_runtime()

    recovered = _recover(observations, runtime)

    assert recovered["status"] == "RECOVERED"
    assert recovered["recovered_node_ids"] == [SOURCE]
    assert runtime["node_status"] == {SOURCE: "SUCCEEDED", TARGET: "PENDING"}
    assert runtime["binding_ledger"]["outputs_by_node"][SOURCE][
        "order_id"
    ]["value"] == "ORD-42"
    assert recovered["request_bodies_for_cleanup"] == {
        SOURCE: {"sku": "SKU-1"}
    }


def test_response_body_drift_cannot_rehydrate_binding() -> None:
    observations = _persisted_observations()
    observations["graph_step_observations"][0]["body"]["id"] = "ORD-FOREIGN"
    checkpoint = observations["process_graph_resume_checkpoint"]
    from ai_test_asset_center.process_graph_read_runtime import _stable_hash

    checkpoint["graph_observation_fingerprints"] = {
        SOURCE: _stable_hash(observations["graph_step_observations"][0])
    }
    checkpoint["checkpoint_fingerprint"] = _stable_hash(
        {
            key: value
            for key, value in checkpoint.items()
            if key != "checkpoint_fingerprint"
        }
    )

    recovered = _recover(observations)

    assert recovered["reason_code"] == GRAPH_RESUME_STATE_INVALID
    assert recovered["detail"] == (
        "resume_binding_value_fingerprint_mismatch:create_order:order_id"
    )


def test_nonterminal_write_transport_is_never_replayed() -> None:
    observations = _persisted_observations(
        runtime_status="FAILED",
        ledger=_ledger(
            final_status="FAILED",
            status_code=500,
            operation_accepted=False,
        ),
        graph_observations=[],
    )

    recovered = _recover(observations)

    assert recovered["reason_code"] == GRAPH_RESUME_EFFECT_AMBIGUOUS
    assert recovered["detail"] == "resume_write_effect_not_terminal:create_order"


def test_pretransport_blocked_write_is_retryable() -> None:
    observations = _persisted_observations(
        runtime_status="BLOCKED",
        ledger=_ledger(
            final_status="BLOCKED",
            status_code=0,
            operation_accepted=False,
        ),
        graph_observations=[],
    )
    runtime = _fresh_runtime()

    recovered = _recover(observations, runtime)

    assert recovered["status"] == "RETRYABLE"
    assert recovered["recovered_node_ids"] == []
    assert runtime["node_status"][SOURCE] == "PENDING"


def test_graph_or_target_policy_drift_blocks_resume() -> None:
    observations = _persisted_observations()
    drifted = _fresh_runtime()
    drifted["target_contexts"][TARGET]["base_url"] = "https://other.test"

    recovered = _recover(observations, drifted)

    assert recovered["reason_code"] == GRAPH_RESUME_STATE_INVALID
    assert recovered["detail"] == "resume_target_decision_drift:confirm_order"


def test_old_execution_checkpoint_does_not_block_new_run() -> None:
    observations = _persisted_observations()

    recovered = recover_process_graph_runtime(
        graph=_graph(),
        treatment_plan=_steps(),
        runtime=_fresh_runtime(),
        observations=observations,
        experiment_id=EID,
        obligation_id=OID,
        campaign_id=CID,
        execution_id="run-new",
    )

    assert recovered["status"] == "FRESH"


def test_executor_skips_recovered_write_and_runs_only_pending_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = _persisted_observations()
    calls: list[str] = []
    monkeypatch.setattr(
        executor,
        "prepare_graph_runtime",
        lambda **kwargs: _fresh_runtime(),
    )

    def execute_pending(**kwargs):
        node_id = kwargs["node_id"]
        calls.append(node_id)
        assert node_id == TARGET
        kwargs["master"].record_step_execution(
            step_id=TARGET,
            phase="treatment",
            operation_ref="op_confirm_order",
            actor_ref="actor-orders",
            runtime_identity={"order_id": "ORD-42"},
            request_receipt_id="request-confirm",
            response_receipt_id="response-confirm",
            transport_receipt_id="transport-confirm",
            after_state_receipt_id="observer-confirm",
            observer_receipt_ids=["observer-confirm"],
            status_code=200,
            final_status="EXECUTED",
            mutation_occurred=True,
            operation_accepted=True,
            business_effect_observed=True,
            target_reached=True,
        )
        observation = {
            "phase": "treatment",
            "step_id": TARGET,
            "status": 200,
            "status_code": 200,
            "body": {},
            "experiment_id": EID,
            "obligation_id": OID,
            "campaign_id": CID,
            "execution_id": RUN_ID,
            "execution_graph_id": GRAPH_ID,
            "process_id": "process-order-resume",
        }
        outcome = record_graph_step_outcome(
            runtime=kwargs["runtime"],
            graph=kwargs["graph"],
            step=kwargs["step"],
            observation=observation,
        )
        assert outcome["status"] == "SUCCEEDED"
        return observation

    monkeypatch.setattr(executor, "_execute_graph_node", execute_pending)

    result = executor.execute_non_barrier_plans(
        control_plan=[],
        treatment_plan=_steps(),
        consumed_barrier_steps=set(),
        actors={},
        ops={},
        tokens={},
        runtime_bindings={},
        activation_requirements={
            "control": [],
            "treatment": [SOURCE, TARGET],
        },
        observations=observations,
        eid=EID,
        oid=OID,
        resolved_campaign_id=CID,
        resolved_execution_id=RUN_ID,
        campaign_id=CID,
        root=Path("."),
        project="project-order-resume",
        base_url="https://orders.example.test",
        runtime_contract={},
        cleanup_failures=0,
    )

    assert calls == [TARGET]
    assert result["process_graph_runtime"]["node_status"] == {
        SOURCE: "SUCCEEDED",
        TARGET: "SUCCEEDED",
    }
    assert result["process_graph_resume_checkpoint"]["node_status"] == {
        SOURCE: "SUCCEEDED",
        TARGET: "SUCCEEDED",
    }
    assert result["request_bodies_for_cleanup"] == {
        SOURCE: {"sku": "SKU-1"}
    }
