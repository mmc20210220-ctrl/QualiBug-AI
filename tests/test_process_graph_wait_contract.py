from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center import experiment_plan_step_executor as step_kernel
from ai_test_asset_center import multi_step_protocol
from ai_test_asset_center import process_graph_runtime
from ai_test_asset_center.process_graph_wait_contract import (
    STATUS_BLOCKED,
    STATUS_COMPILED,
    STATUS_CONVERGED,
    WAIT_ASYNC_EDGE_UNCOVERED,
    WAIT_BINDING_UNRESOLVED,
    WAIT_CONTRACT_AMBIGUOUS,
    WAIT_CONTRACT_INVALID,
    compile_process_graph_wait_contracts,
    execute_process_graph_wait,
    wait_predicate_matches,
)
from ai_test_asset_center.process_step_execution import ProcessStepLedger


OPERATIONS = [
    {
        "id": "op_source",
        "method": "GET",
        "read_write": "read",
        "path": "/jobs/{id}",
    },
    {
        "id": "op_target",
        "method": "GET",
        "read_write": "read",
        "path": "/jobs/{id}/result",
    },
    {
        "id": "op_wait",
        "method": "GET",
        "read_write": "read",
        "path": "/jobs/{id}/status",
    },
    {
        "id": "op_wait_write",
        "method": "POST",
        "read_write": "write",
        "path": "/jobs/{id}/status",
    },
]
IR = {"operations": OPERATIONS}


def _raw_graph(*, include_wait: bool = True, async_edge: bool = True) -> dict:
    graph = {
        "execution_graph_id": "graph_wait_1",
        "process_id": "process_wait_1",
        "nodes": [
            {
                "node_id": "step_source",
                "step_id": "step_source",
                "operation_ref": "op_source",
                "actor_ref": "actor_1",
                "system_ref": "",
                "method": "GET",
                "path": "/jobs/{id}",
            },
            {
                "node_id": "step_target",
                "step_id": "step_target",
                "operation_ref": "op_target",
                "actor_ref": "actor_1",
                "system_ref": "",
                "method": "GET",
                "path": "/jobs/{id}/result",
            },
        ],
        "edges": [
            {
                "edge_id": "edge_wait",
                "source_node_id": "step_source",
                "target_node_id": "step_target",
                "relation_type": "AWAITS" if async_edge else "DEPENDS_ON",
            }
        ],
        "topological_order": ["step_source", "step_target"],
        "start_node_refs": ["step_source"],
        "terminal_node_refs": ["step_target"],
        "fork_groups": [],
        "join_groups": [],
        "source_refs": [{"kind": "process", "locator": "process_wait_1"}],
    }
    graph["wait_contracts"] = (
        [
            {
                "wait_id": "wait_target_ready",
                "source_node_id": "step_source",
                "target_node_id": "step_target",
                "observer_operation_ref": "op_wait",
                "actor_ref": "actor_1",
                "system_ref": "",
                "predicate": {
                    "status_codes": [200],
                    "json_path": "$.state",
                    "operator": "equals",
                    "expected_value": "READY",
                },
                "async_policy": {
                    "enabled": True,
                    "expected_max_delay_ms": 20,
                    "poll_interval_ms": 1,
                    "max_attempts": 3,
                    "required_stable_observations": 1,
                    "terminal_condition": "source_declared_predicate",
                },
            }
        ]
        if include_wait
        else []
    )
    return graph


def _compiled_graph() -> dict:
    result = compile_process_graph_wait_contracts(
        _raw_graph(),
        behavior_ir=IR,
    )
    assert result["status"] == STATUS_COMPILED
    return result["graph"]


def test_complete_wait_contract_compiles_and_indexes_exact_target() -> None:
    result = compile_process_graph_wait_contracts(
        _raw_graph(),
        behavior_ir=IR,
    )

    assert result["status"] == STATUS_COMPILED
    graph = result["graph"]
    contract = graph["wait_contracts_by_target"]["step_target"]
    assert contract["observer_operation_ref"] == "op_wait"
    assert contract["method"] == "GET"
    assert contract["path_template"] == "/jobs/{id}/status"
    assert contract["async_policy"]["max_attempts"] == 3
    assert graph["wait_runtime_contract"]["contract_count"] == 1
    assert graph["runtime_blockers"] if "runtime_blockers" in graph else [] == []


def test_duplicate_wait_for_same_target_blocks_as_ambiguous() -> None:
    graph = _raw_graph()
    duplicate = dict(graph["wait_contracts"][0])
    duplicate["wait_id"] = "wait_duplicate"
    graph["wait_contracts"].append(duplicate)

    result = compile_process_graph_wait_contracts(graph, behavior_ir=IR)

    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == WAIT_CONTRACT_AMBIGUOUS
    assert "duplicate_target_wait" in result["detail"]


def test_async_edge_without_enabled_wait_remains_blocked() -> None:
    result = compile_process_graph_wait_contracts(
        _raw_graph(include_wait=False),
        behavior_ir=IR,
    )

    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == WAIT_ASYNC_EDGE_UNCOVERED
    assert "step_source->step_target" in result["detail"]


def test_wait_observer_must_be_read_only() -> None:
    graph = _raw_graph()
    graph["wait_contracts"][0]["observer_operation_ref"] = "op_wait_write"

    result = compile_process_graph_wait_contracts(graph, behavior_ir=IR)

    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == WAIT_CONTRACT_INVALID
    assert "observer_transport_invalid:POST" in result["detail"]


def test_invalid_async_policy_blocks_compile() -> None:
    graph = _raw_graph()
    graph["wait_contracts"][0]["async_policy"]["max_attempts"] = 0

    result = compile_process_graph_wait_contracts(graph, behavior_ir=IR)

    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == WAIT_CONTRACT_INVALID
    assert "READBACK_ASYNC_POLICY_INVALID" in result["detail"]


@pytest.mark.parametrize(
    ("predicate", "body", "expected"),
    [
        ({"json_path": "$.state", "operator": "equals", "expected_value": "READY"}, {"state": "READY"}, True),
        ({"json_path": "$.state", "operator": "not_equals", "expected_value": "FAILED"}, {"state": "READY"}, True),
        ({"json_path": "$.result", "operator": "exists"}, {"result": None}, True),
        ({"json_path": "$.ready", "operator": "truthy"}, {"ready": 1}, True),
        ({"json_path": "$.state", "operator": "in", "expected_value": ["READY", "DONE"]}, {"state": "DONE"}, True),
        ({"status_codes": [202]}, {}, False),
    ],
)
def test_wait_predicate_is_explicit_and_deterministic(
    predicate: dict,
    body: dict,
    expected: bool,
) -> None:
    assert wait_predicate_matches(
        {"status_code": 200, "body": body},
        predicate,
    ) is expected


def test_runtime_wait_converges_before_target_step() -> None:
    graph = _compiled_graph()
    responses = iter(
        [
            {"status_code": 200, "body": {"state": "RUNNING"}},
            {"status_code": 200, "body": {"state": "READY"}},
        ]
    )
    ticks = iter([0.0, 0.0, 0.001, 0.002, 0.003])

    receipt = execute_process_graph_wait(
        graph=graph,
        step=next(
            row for row in graph["nodes"] if row["node_id"] == "step_target"
        ),
        context={"base_url": "https://example.test", "bindings": {"id": "42"}},
        actors={"actor_1": {"role": "public"}},
        tokens={},
        read_once=lambda: next(responses),
        sleep=lambda _: None,
        monotonic=lambda: next(ticks),
    )

    assert receipt["status"] == STATUS_CONVERGED
    assert receipt["converged"] is True
    assert receipt["attempt_count"] == 2
    assert receipt["path"] == "/jobs/42/status"
    assert receipt["receipt_id"].startswith("wait_")


def test_runtime_wait_timeout_is_visible_and_bounded() -> None:
    graph = _compiled_graph()
    ticks = iter([0.0, 0.0, 0.001, 0.002, 0.003, 0.004])

    receipt = execute_process_graph_wait(
        graph=graph,
        step=next(
            row for row in graph["nodes"] if row["node_id"] == "step_target"
        ),
        context={"base_url": "https://example.test", "bindings": {"id": "42"}},
        actors={"actor_1": {"role": "public"}},
        tokens={},
        read_once=lambda: {
            "status_code": 200,
            "body": {"state": "RUNNING"},
        },
        sleep=lambda _: None,
        monotonic=lambda: next(ticks),
    )

    assert receipt["status"] == STATUS_BLOCKED
    assert receipt["converged"] is False
    assert receipt["timed_out"] is True
    assert receipt["attempt_count"] == 3
    assert receipt["reason_code"] == "READBACK_ASYNC_TIMEOUT"


def test_runtime_wait_does_not_guess_missing_path_binding() -> None:
    graph = _compiled_graph()

    receipt = execute_process_graph_wait(
        graph=graph,
        step=next(
            row for row in graph["nodes"] if row["node_id"] == "step_target"
        ),
        context={"base_url": "https://example.test", "bindings": {}},
        actors={"actor_1": {"role": "public"}},
        tokens={},
        read_once=lambda: pytest.fail("read must not run"),
    )

    assert receipt["status"] == STATUS_BLOCKED
    assert receipt["reason_code"] == WAIT_BINDING_UNRESOLVED


def test_public_protocol_resumes_only_after_wait_contract_compiles() -> None:
    envelope = {
        "risk_family": "process",
        "operation_ref": "op_source",
        "treatment_actor_ref": "actor_1",
        "property_spec": {
            "execution_graph": _raw_graph(),
            "source_refs": [{"kind": "process", "locator": "process_wait_1"}],
        },
        "behavior_ir": IR,
    }

    result = multi_step_protocol.compile_multi_step_process_protocol(envelope)

    assert result["status"] == "COMPILED"
    assert result["wait_runtime_contract"]["status"] == STATUS_COMPILED
    target = next(
        row for row in result["treatment_plan"] if row["step_id"] == "step_target"
    )
    assert target["wait_contract"]["wait_id"] == "wait_target_ready"
    assert target["_execution_graph"]["wait_runtime_contract"]["contract_count"] == 1


def test_graph_runtime_accepts_only_compiled_wait_contract(monkeypatch) -> None:
    graph = _compiled_graph()
    treatment = [
        {
            "step_id": row["node_id"],
            "operation_ref": row["operation_ref"],
            "actor_ref": row["actor_ref"],
            "method": row["method"],
            "path": row["path"],
        }
        for row in graph["nodes"]
    ]
    monkeypatch.setattr(
        process_graph_runtime,
        "resolve_graph_target_context",
        lambda **kwargs: {
            "status": "READY",
            "base_url": "https://example.test",
            "runtime_contract": {},
            "target_policy_decision": {"decision_id": "decision_1"},
            "credential_token_key": "",
            "read_allowed": True,
            "write_allowed": False,
            "primary": True,
            "system_ref": kwargs.get("system_ref", ""),
        },
    )

    runtime = process_graph_runtime.prepare_graph_runtime(
        graph=graph,
        treatment_plan=treatment,
        ops={row["id"]: row for row in OPERATIONS},
        base_url="https://example.test",
        runtime_contract={},
    )

    assert runtime["status"] == "READY"
    assert runtime["wait_runtime_contract"]["status"] == STATUS_COMPILED
    assert runtime["target_contexts"]["step_target"]["wait_contract"]["wait_id"] == "wait_target_ready"


def _kernel_kwargs(step: dict) -> dict:
    return {
        "control_plan": [],
        "treatment_plan": [step],
        "consumed_barrier_steps": set(),
        "actors": {"actor_1": {"role": "public"}},
        "ops": {
            "op_target": {
                "id": "op_target",
                "method": "GET",
                "path": "/jobs/{id}/result",
            }
        },
        "tokens": {},
        "runtime_bindings": {"id": "42"},
        "activation_requirements": {
            "control": [],
            "treatment": ["step_target"],
        },
        "observations": {},
        "eid": "exp_wait",
        "oid": "obl_wait",
        "resolved_campaign_id": "campaign_wait",
        "resolved_execution_id": "execution_wait",
        "campaign_id": "campaign_wait",
        "root": Path("."),
        "project": "project_wait",
        "base_url": "https://example.test",
        "runtime_contract": {},
        "cleanup_failures": 0,
    }


def _kernel_step() -> dict:
    graph = _compiled_graph()
    contract = graph["wait_contracts_by_target"]["step_target"]
    return {
        "step_id": "step_target",
        "operation_ref": "op_target",
        "actor_ref": "actor_1",
        "method": "GET",
        "path": "/jobs/{id}/result",
        "wait_contract": contract,
        "_execution_graph": graph,
    }


def test_step_kernel_timeout_blocks_business_transport(monkeypatch) -> None:
    step = _kernel_step()
    monkeypatch.setattr(
        step_kernel,
        "execute_process_graph_wait",
        lambda **kwargs: {
            "status": STATUS_BLOCKED,
            "reason_code": "READBACK_ASYNC_TIMEOUT",
            "detail": "state_not_ready",
            "step_id": "step_target",
            "wait_id": "wait_target_ready",
            "receipt_id": "wait_timeout_1",
            "attempt_count": 3,
            "timed_out": True,
            "converged": False,
        },
    )
    monkeypatch.setattr(
        step_kernel._core,
        "execute_non_barrier_plans",
        lambda **kwargs: pytest.fail("business transport must not run"),
    )

    result = step_kernel.execute_non_barrier_plans(**_kernel_kwargs(step))

    assert result["steps"][0]["status"] == "blocked_request"
    assert result["steps"][0]["reason"] == "READBACK_ASYNC_TIMEOUT"
    ledger = result["process_step_ledger"]
    assert ledger.get_step_row("step_target")["final_step_status"] == "BLOCKED"
    assert result["process_timeline"]["events"][0]["event_type"] == "WAIT_FAILED"


def test_step_kernel_records_wait_before_copied_transport(monkeypatch) -> None:
    step = _kernel_step()
    monkeypatch.setattr(
        step_kernel,
        "execute_process_graph_wait",
        lambda **kwargs: {
            "status": STATUS_CONVERGED,
            "reason_code": "",
            "step_id": "step_target",
            "wait_id": "wait_target_ready",
            "receipt_id": "wait_converged_1",
            "observer_operation_ref": "op_wait",
            "attempt_count": 2,
            "timed_out": False,
            "converged": True,
        },
    )

    def fake_core(**kwargs):
        ledger = ProcessStepLedger(
            experiment_id="exp_wait",
            campaign_id="campaign_wait",
            run_id="execution_wait",
            obligation_id="obl_wait",
            required_step_ids=["step_target"],
        )
        ledger.record_step_execution(
            step_id="step_target",
            phase="treatment",
            operation_ref="op_target",
            actor_ref="actor_1",
            runtime_identity={"id": "42"},
            request_receipt_id="request_1",
            response_receipt_id="response_1",
            transport_receipt_id="transport_1",
            status_code=200,
            final_status="EXECUTED",
        )
        ledger.record_timeline_event(
            step_id="step_target",
            phase="treatment",
            event_type="STEP_COMPLETED",
            operation_ref="op_target",
            actor_ref="actor_1",
            receipt_id="transport_1",
        )
        return {
            "steps": [{"phase": "treatment", "step_id": "step_target", "status_code": 200}],
            "contract_evidence_receipts": [],
            "request_bodies_for_cleanup": {},
            "pre_transport_block_reasons": [],
            "cleanup_failures": 0,
            "process_step_ledger": ledger,
        }

    monkeypatch.setattr(
        step_kernel._core,
        "execute_non_barrier_plans",
        fake_core,
    )

    result = step_kernel.execute_non_barrier_plans(**_kernel_kwargs(step))

    events = result["process_timeline"]["events"]
    assert [row["event_type"] for row in events] == [
        "WAIT_CONVERGED",
        "STEP_COMPLETED",
    ]
    row = result["process_step_ledger"].get_step_row("step_target")
    assert "wait_converged_1" in row["scoped_observation_receipt_ids"]
    assert result["process_graph_wait_receipts"][0]["converged"] is True
