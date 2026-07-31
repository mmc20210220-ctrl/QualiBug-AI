from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center import process_graph_cleanup_executor as cleanup_runtime
from ai_test_asset_center.process_graph_rollback_contract import (
    freeze_process_graph_rollback_contract,
)


def _graph() -> dict:
    return {
        "execution_graph_id": "graph_rollback_1",
        "process_id": "process_rollback_1",
        "nodes": [
            {"node_id": "write_a"},
            {"node_id": "write_b"},
            {"node_id": "write_c"},
            {"node_id": "write_d"},
        ],
        "edges": [
            {"source_node_id": "write_a", "target_node_id": "write_b"},
            {"source_node_id": "write_b", "target_node_id": "write_c"},
        ],
        "topological_order": ["write_a", "write_b", "write_c", "write_d"],
    }


def _cleanup_steps() -> list[dict]:
    return [
        {
            "step_id": "cleanup_d",
            "source_step_id": "write_d",
            "operation_ref": "undo_d",
            "system_ref": "",
        },
        {
            "step_id": "cleanup_c",
            "source_step_id": "write_c",
            "operation_ref": "undo_c",
            "system_ref": "",
        },
        {
            "step_id": "cleanup_b",
            "source_step_id": "write_b",
            "operation_ref": "undo_b",
            "system_ref": "",
        },
        {
            "step_id": "cleanup_a",
            "source_step_id": "write_a",
            "operation_ref": "undo_a",
            "system_ref": "",
        },
    ]


def _experiment() -> dict:
    graph = _graph()
    write_contract = {
        "contract_id": "write_contract_1",
        "proof_set_id": "proof_set_1",
        "write_step_ids": ["write_a", "write_b", "write_c", "write_d"],
        "cleanup_steps": _cleanup_steps(),
    }
    rollback = freeze_process_graph_rollback_contract(graph, write_contract)
    write_contract["rollback_contract"] = deepcopy(rollback)
    write_contract["rollback_contract_id"] = rollback[
        "contract_fingerprint"
    ]
    graph["rollback_contract"] = deepcopy(rollback)
    graph["rollback_contract_id"] = rollback["contract_fingerprint"]
    return {
        "execution_graph": graph,
        "process_graph_write_contract": write_contract,
        "process_graph_rollback_contract": rollback,
        "cleanup_plan": _cleanup_steps(),
        "safety_contract": {
            "cleanup_authority": "process_graph_write_contract"
        },
    }


def _governed_step(step_id: str) -> dict:
    return {
        "phase": "treatment",
        "step_id": step_id,
        "operation_ref": f"op_{step_id}",
        "actor_ref": "actor_1",
        "status_code": 200,
        "governance_receipt": {
            "accepted": True,
            "audit_path": f"audit/{step_id}",
            "before": {"status": 200, "body": {"id": step_id, "state": "OLD"}},
            "write": {"status": 200, "body": {"id": step_id}},
            "after": {"status": 200, "body": {"id": step_id, "state": "NEW"}},
        },
    }


def _blocked_step(step_id: str) -> dict:
    return {
        "phase": "treatment",
        "step_id": step_id,
        "status": "blocked_request",
        "status_code": 0,
        "reason": "BLOCKED_PREDECESSOR_NOT_SUCCEEDED",
    }


def _kwargs(*, steps_out: list[dict], observations: dict | None = None) -> dict:
    return {
        "exp": _experiment(),
        "steps_out": steps_out,
        "observations": observations
        if observations is not None
        else {
            "process_graph_runtime": {
                "node_status": {
                    "write_a": "SUCCEEDED",
                    "write_b": "SUCCEEDED",
                    "write_c": "SUCCEEDED",
                    "write_d": "SUCCEEDED",
                }
            }
        },
        "contract_evidence_receipts": [],
        "request_bodies_for_cleanup": {},
        "runtime_bindings": {},
        "cleanup_failures": 0,
        "actors": {},
        "tokens": {},
        "eid": "exp_1",
        "oid": "obl_1",
        "resolved_campaign_id": "campaign_1",
        "resolved_execution_id": "run_1",
        "campaign_id": "campaign_1",
        "root": None,
        "project": "project_1",
        "base_url": "https://example.test",
        "runtime_contract": {},
    }


def _receipt_source(receipt: dict) -> str:
    evidence = receipt.get("evidence")
    evidence_row = evidence if isinstance(evidence, dict) else {}
    return str(
        receipt.get("source_step_id")
        or evidence_row.get("source_step_id")
        or ""
    )


def _install_fake_core(monkeypatch, statuses: dict[str, str], called: list[str]) -> None:
    def fake_execute(**kwargs):
        cleanup = kwargs["exp"]["process_graph_write_contract"][
            "cleanup_steps"
        ][0]
        source = cleanup["source_step_id"]
        called.append(source)
        status = statuses.get(source, "COMPLETED")
        receipt = {
            "status": status,
            "receipt_id": f"receipt_{source}",
            "source_step_id": source,
            "subject_id": cleanup["step_id"],
        }
        kwargs["contract_evidence_receipts"].append(receipt)
        rows = []
        if status == "COMPLETED":
            row = {
                "phase": "cleanup",
                "cleanup_subject_id": cleanup["step_id"],
                "compensates_step_id": source,
                "status_code": 200,
            }
            kwargs["steps_out"].append(row)
            rows.append(row)
        kwargs["observations"]["process_graph_cleanup_receipts"] = [
            receipt
        ]
        kwargs["observations"]["process_graph_cleanup_steps"] = rows
        failures = int(kwargs.get("cleanup_failures") or 0)
        if status not in {"COMPLETED", "NOT_REQUIRED"}:
            failures += 1
        return {
            "steps_out": kwargs["steps_out"],
            "observations": kwargs["observations"],
            "contract_evidence_receipts": kwargs[
                "contract_evidence_receipts"
            ],
            "cleanup_failures": failures,
            "process_graph_cleanup_receipts": [receipt],
        }

    monkeypatch.setattr(
        cleanup_runtime._core,
        "execute_process_graph_cleanup",
        fake_execute,
    )
    monkeypatch.setattr(
        cleanup_runtime._core,
        "_cleanup_candidate",
        lambda source: True,
    )


def test_unreached_descendant_is_not_cleanup_failure(monkeypatch) -> None:
    called: list[str] = []
    _install_fake_core(monkeypatch, {}, called)
    observations = {
        "process_graph_runtime": {
            "node_status": {
                "write_a": "SUCCEEDED",
                "write_b": "SUCCEEDED",
                "write_c": "BLOCKED",
                "write_d": "SUCCEEDED",
            }
        }
    }
    result = cleanup_runtime.execute_process_graph_cleanup(
        **_kwargs(
            steps_out=[
                _governed_step("write_a"),
                _governed_step("write_b"),
                _blocked_step("write_c"),
                _governed_step("write_d"),
            ],
            observations=observations,
        )
    )

    assert called == ["write_d", "write_b", "write_a"]
    receipts = {
        _receipt_source(row): row
        for row in result["process_graph_cleanup_receipts"]
    }
    assert receipts["write_c"]["status"] == "NOT_REQUIRED"
    assert receipts["write_c"]["evidence"]["reason_code"] == (
        cleanup_runtime.GRAPH_CLEANUP_SOURCE_WRITE_NOT_REACHED
    )
    assert result["cleanup_failures"] == 0
    assert observations["process_graph_rollback_outcomes"] == {
        "write_d": "COMPLETED",
        "write_c": "NOT_REQUIRED",
        "write_b": "COMPLETED",
        "write_a": "COMPLETED",
    }


def test_descendant_cleanup_failure_blocks_only_ancestors(monkeypatch) -> None:
    called: list[str] = []
    _install_fake_core(
        monkeypatch,
        {"write_c": "FAILED"},
        called,
    )
    result = cleanup_runtime.execute_process_graph_cleanup(
        **_kwargs(
            steps_out=[
                _governed_step("write_a"),
                _governed_step("write_b"),
                _governed_step("write_c"),
                _governed_step("write_d"),
            ]
        )
    )

    # Independent D still restores; C attempts and fails; B/A never reach transport.
    assert called == ["write_d", "write_c"]
    outcomes = result["observations"]["process_graph_rollback_outcomes"]
    assert outcomes == {
        "write_d": "COMPLETED",
        "write_c": "FAILED",
        "write_b": "BLOCKED",
        "write_a": "BLOCKED",
    }
    receipts = {
        _receipt_source(row): row
        for row in result["process_graph_cleanup_receipts"]
    }
    assert receipts["write_b"]["status"] == "BLOCKED"
    assert receipts["write_b"]["evidence"]["reason_code"] == (
        cleanup_runtime.GRAPH_CLEANUP_DEPENDENCY_NOT_RESTORED
    )
    assert receipts["write_b"]["evidence"][
        "unsafe_downstream_outcomes"
    ] == {"write_c": "FAILED"}
    assert receipts["write_a"]["evidence"][
        "unsafe_downstream_outcomes"
    ] == {"write_c": "FAILED", "write_b": "BLOCKED"}
    assert result["cleanup_failures"] == 3


def test_rollback_contract_drift_blocks_all_cleanup_transport(monkeypatch) -> None:
    called: list[str] = []
    _install_fake_core(monkeypatch, {}, called)
    kwargs = _kwargs(
        steps_out=[
            _governed_step("write_a"),
            _governed_step("write_b"),
            _governed_step("write_c"),
            _governed_step("write_d"),
        ]
    )
    kwargs["exp"]["process_graph_rollback_contract"][
        "contract_fingerprint"
    ] = "drifted"

    result = cleanup_runtime.execute_process_graph_cleanup(**kwargs)

    assert called == []
    assert result["cleanup_failures"] == 4
    assert all(
        row["status"] == "FAILED"
        and row["evidence"]["reason_code"] == "PROCESS_GRAPH_ROLLBACK_CONTRACT_DRIFT"
        for row in result["process_graph_cleanup_receipts"]
    )
